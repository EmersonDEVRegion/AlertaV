"""Endpoints de eventos crudos.

Importante: esta API expone *señales*, no incidentes. El endpoint de incidentes
correlacionados llega en el siguiente hito.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    HazardServiceDep,
    IngestServiceDep,
    SeismicServiceDep,
    WeatherServiceDep,
)
from app.api.v1.params import parse_bbox
from app.core.config import settings
from app.core.ratelimit import RateLimiter, client_ip
from app.models.enums import EventSource, EventType
from app.schemas.event import (
    CitizenReportCreate,
    EventBatchCreate,
    EventCreate,
    EventRead,
    EventStats,
    GeoJSONFeatureCollection,
    IngestResult,
)
from app.schemas.seismic import SeismicEventRead, SeismicStats
from app.schemas.weather import WeatherForecastRead, WeatherStats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

#: Limitador del endpoint ciudadano. Vive a nivel de módulo —una instancia por
#: proceso— y sólo protege ese endpoint: los demás son de lectura, o requieren
#: credenciales de operador.
citizen_report_limiter = RateLimiter(
    interval_seconds=settings.CITIZEN_REPORT_MIN_INTERVAL_SECONDS
)


@router.post(
    "",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingesta de un evento",
)
async def create_event(event: EventCreate, service: IngestServiceDep) -> EventRead:
    entity = await service.repo.add(event)
    await service.session.commit()
    return EventRead.model_validate(entity)


@router.post(
    "/batch",
    response_model=IngestResult,
    summary="Ingesta idempotente por lote",
    description=(
        "Usado por los collectors. Reejecutar el mismo lote no duplica: la clave "
        "es (source, external_id)."
    ),
)
async def create_events_batch(
    payload: EventBatchCreate, service: IngestServiceDep
) -> IngestResult:
    return await service.ingest_batch(payload.events)


@router.post(
    "/citizen-report",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reporte ciudadano desde la PWA",
    description=(
        "La fuente y la confianza las fija el servidor. Un reporte se guarda como "
        "señal, nunca como incidente confirmado.\n\n"
        "Limitado a un reporte por IP cada "
        f"{settings.CITIZEN_REPORT_MIN_INTERVAL_SECONDS // 60} minutos."
    ),
    responses={
        429: {
            "description": (
                "Demasiados reportes desde la misma IP. La cabecera `Retry-After` "
                "indica en cuántos segundos se puede reintentar."
            )
        }
    },
)
async def create_citizen_report(
    report: CitizenReportCreate, request: Request, service: IngestServiceDep
) -> EventRead:
    ip = client_ip(
        forwarded_for=request.headers.get("x-forwarded-for"),
        real_ip=request.headers.get("x-real-ip"),
        peer=request.client.host if request.client else None,
    )

    decision = citizen_report_limiter.check(ip)
    if not decision.allowed:
        # Se registra la IP truncada, no entera: para operar basta saber que
        # alguien insiste desde el mismo lugar, y guardar direcciones completas
        # de personas que reportan emergencias no hace falta para eso.
        logger.info(
            "reporte ciudadano rechazado por frecuencia",
            extra={"ip": _anonimizar(ip), "retry_after_s": decision.retry_after_seconds},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Ya recibimos un reporte tuyo hace poco. Espera unos minutos "
                "antes de enviar otro."
            ),
            # Sin esta cabecera el 429 no dice cuánto esperar y el cliente sólo
            # puede adivinar o reintentar en bucle.
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    entity = await service.ingest_citizen_report(report)
    return EventRead.model_validate(entity)


def _anonimizar(ip: str) -> str:
    """Últimos octetos ocultos. IPv4 e IPv6 con el mismo criterio."""
    if ":" in ip:
        partes = ip.split(":")
        return ":".join(partes[:3]) + ":···" if len(partes) > 3 else ip
    partes = ip.split(".")
    return ".".join(partes[:2]) + ".×.×" if len(partes) == 4 else ip


@router.get("", response_model=list[EventRead], summary="Listado de eventos")
async def list_events(
    service: IngestServiceDep,
    since: Annotated[datetime | None, Query(description="ISO 8601")] = None,
    until: Annotated[datetime | None, Query(description="ISO 8601")] = None,
    source: Annotated[list[EventSource] | None, Query()] = None,
    type: Annotated[list[EventType] | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    bbox: Annotated[
        str | None, Query(description="west,south,east,north en WGS84")
    ] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[float | None, Query(gt=0, le=200_000)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EventRead]:
    events = await service.repo.list_events(
        since=since,
        until=until,
        sources=source,
        types=type,
        min_confidence=min_confidence,
        bbox=_parse_bbox(bbox),
        near=_parse_near(lat, lon, radius_m),
        limit=limit,
        offset=offset,
    )
    return [EventRead.model_validate(event) for event in events]


@router.get(
    "/geojson",
    response_model=GeoJSONFeatureCollection,
    summary="Eventos como GeoJSON",
    description="Consumible directamente por MapLibre GL JS.",
)
async def events_geojson(
    service: IngestServiceDep,
    hours: Annotated[int, Query(ge=1, le=720, description="Ventana hacia atrás")] = 24,
    source: Annotated[list[EventSource] | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> GeoJSONFeatureCollection:
    since = datetime.now(UTC) - timedelta(hours=hours)
    events = await service.repo.list_events(
        since=since,
        sources=source,
        min_confidence=min_confidence,
        bbox=service.region_bbox(),
        limit=limit,
    )
    return service.to_geojson(events)


@router.get("/stats", response_model=EventStats, summary="Resumen de la recolección")
async def events_stats(
    service: IngestServiceDep,
    hours: Annotated[int | None, Query(ge=1, le=8760)] = None,
) -> EventStats:
    since = datetime.now(UTC) - timedelta(hours=hours) if hours else None
    return EventStats(**await service.repo.stats(since=since))


@router.get(
    "/{public_id}/neighbours",
    response_model=list[EventRead],
    summary="Señales cercanas en espacio y tiempo",
    description=(
        "Vista previa del motor de correlación: devuelve las señales de cualquier "
        "fuente próximas al evento dado."
    ),
)
async def event_neighbours(
    public_id: UUID,
    service: IngestServiceDep,
    radius_m: Annotated[float, Query(gt=0, le=50_000)] = 2000,
    window_minutes: Annotated[int, Query(ge=1, le=1440)] = 60,
) -> list[EventRead]:
    event = await service.repo.get_by_public_id(public_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evento no encontrado")
    if event.lat is None or event.lon is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="el evento no tiene coordenadas",
        )

    neighbours = await service.repo.find_spatiotemporal_neighbours(
        lat=event.lat,
        lon=event.lon,
        timestamp=event.timestamp,
        radius_m=radius_m,
        window_minutes=window_minutes,
        exclude_id=event.id,
    )
    return [EventRead.model_validate(item) for item in neighbours]


# -- Sismos (USGS) -----------------------------------------------------------
#
# Un sismo es una señal como cualquier otra y vive en `raw_events`, pero tiene
# dos dimensiones que ninguna otra fuente tiene —magnitud y profundidad— y que
# viven en la tabla satélite `seismic_details`. Estas rutas son el JOIN de las
# dos, y existen aparte de `/events` genérico por dos motivos:
#
#   * el mapa las consume como una capa independiente, con su propia cadencia;
#   * el JOIN no tiene por qué pesar en cada consulta de incendios.
#
# IMPORTANTE: van declaradas antes de `/{public_id}`. FastAPI resuelve por orden
# de registro y, puestas después, "seismic" entraría por la ruta del detalle y
# fallaría al parsearlo como UUID.


@router.get(
    "/seismic",
    response_model=list[SeismicEventRead],
    summary="Sismos recientes con su detalle sismológico",
    description=(
        "Sismos del USGS con magnitud y profundidad. **Un sismo no es un "
        "incidente**: no pasa por el motor de correlación, no tiene `confidence` "
        "y no implica que haya un siniestro en el epicentro. Es contexto, y "
        "causa posible de incendios, derrumbes o tsunami.\n\n"
        "El recorte geográfico es el de `usgs_bbox`, más ancho que la Región de "
        "Valparaíso: un sismo a 200 km se siente igual."
    ),
)
async def list_seismic_events(
    service: SeismicServiceDep,
    hours: Annotated[
        int, Query(ge=1, le=720, description="Ventana hacia atrás.")
    ] = 72,
    min_magnitude: Annotated[float | None, Query(ge=-2.0, le=10.5)] = None,
    max_depth_km: Annotated[float | None, Query(ge=-15.0, le=800.0)] = None,
    tsunami_only: Annotated[
        bool,
        Query(
            description=(
                "Sólo los marcados por el USGS para evaluación de tsunami. NO "
                "equivale a una alerta vigente en Chile: eso lo declara SENAPRED."
            )
        ),
    ] = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SeismicEventRead]:
    return await service.list_recent(
        hours=hours,
        min_magnitude=min_magnitude,
        max_depth_km=max_depth_km,
        tsunami_only=tsunami_only,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/seismic/geojson",
    response_model=GeoJSONFeatureCollection,
    summary="Sismos como GeoJSON",
    description=(
        "Mismo conjunto que `/events/seismic`, en el formato que MapLibre GL JS "
        "consume directamente. `magnitude` puede venir en `null` cuando el USGS "
        "publicó una solución preliminar; la capa del mapa tiene que preverlo."
    ),
)
async def seismic_geojson(
    service: SeismicServiceDep,
    hours: Annotated[int, Query(ge=1, le=720)] = 72,
    min_magnitude: Annotated[float | None, Query(ge=-2.0, le=10.5)] = None,
    max_depth_km: Annotated[float | None, Query(ge=-15.0, le=800.0)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> GeoJSONFeatureCollection:
    events = await service.list_recent(
        hours=hours,
        min_magnitude=min_magnitude,
        max_depth_km=max_depth_km,
        limit=limit,
    )
    return service.to_geojson(events)


@router.get(
    "/seismic/stats",
    response_model=SeismicStats,
    summary="Resumen de la ventana sísmica",
)
async def seismic_stats(
    service: SeismicServiceDep,
    hours: Annotated[int, Query(ge=1, le=720)] = 72,
) -> SeismicStats:
    return service.stats(await service.list_recent(hours=hours, limit=2000))


@router.get(
    "/seismic/hazard",
    summary="Capa de amenaza sísmica del CSN (modelo MASCSN26)",
    response_class=Response,
    responses={
        200: {"description": "GeoJSON de celdas con PGA y aceleraciones espectrales."},
        304: {"description": "El artefacto no cambió desde el `ETag` que trae el cliente."},
        502: {
            "description": (
                "El artefacto no está publicado ni hay copia en caché. El cuerpo "
                "trae el sobre de error con la instrucción para regenerarlo."
            )
        },
    },
    description=(
        "Modelo **probabilístico y estático**: dice cuánto puede llegar a "
        "acelerar el suelo, no qué está temblando ahora. No pasa por el motor de "
        "correlación ni por el pipeline de cinco minutos — lo genera a mano "
        "`scripts/fetch_seismic_hazard.py` desde el producto descargable del "
        "CSN.\n\n"
        "Se sirve por la API y no sólo por `/static` porque el frontend vive en "
        "otro origen: una ruta relativa a `/static` se resuelve contra el "
        "dominio del frontend, que no tiene el archivo. Si el artefacto falta o "
        "está corrupto, el servidor entrega la última copia buena que leyó "
        "(cabecera `X-AlertaV-Hazard-Stale: true`) y, si no tiene ninguna, "
        "responde 502 con un mensaje accionable en vez de un 404 desnudo."
    ),
)
async def seismic_hazard(service: HazardServiceDep, request: Request) -> Response:
    """Sirve el artefacto con validación condicional.

    El modelo cambia cada varios años, así que lo caro no es generarlo sino
    mandarlo entero en cada carga del mapa. Con `ETag` + `If-None-Match` el
    navegador lo pide una vez y después recibe 304 sin cuerpo — lo mismo que
    daba `StaticFiles`, que es lo que esta ruta reemplaza.
    """
    artifact = service.load()

    headers = {
        "ETag": artifact.etag,
        # `must-revalidate` y no un `max-age` largo: el artefacto se regenera a
        # mano y, cuando eso pasa, la capa nueva tiene que llegar en la
        # siguiente carga y no dentro de un año. La revalidación cuesta un 304.
        "Cache-Control": "public, max-age=0, must-revalidate",
        "X-AlertaV-Hazard-Stale": "true" if artifact.stale else "false",
    }
    if artifact.generated_at:
        headers["X-AlertaV-Hazard-Generated-At"] = artifact.generated_at

    if request.headers.get("if-none-match") == artifact.etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return JSONResponse(content=artifact.payload, headers=headers)


# -- Meteorología (Open-Meteo) -----------------------------------------------
#
# La capa de lluvia pronosticada. Existe aparte de `/events` genérico por el
# mismo motivo que la sísmica y por uno más:
#
#   * `/events/geojson` no expone `raw_data`, y todo lo que esta capa necesita
#     —milímetros, umbral cruzado, el flag— vive ahí dentro. Sin esta ruta el
#     frontend recibiría un punto sin ninguna de sus propiedades;
#   * es una FOTO, no un histórico: una fila por comuna, la ventana más reciente
#     de cada una. Eso es lo que una capa de mapa consume, y es lo que hace
#     exacto el filtro `solo_riesgo` (ver `WeatherService.list_current`).
#
# La advertencia que esta capa arrastra en todos sus textos: habla del futuro.
# `riesgo_inundacion` es un riesgo pronosticado, no una inundación en curso, y
# las alertas las declara SENAPRED.
#
# IMPORTANTE: van declaradas antes de `/{public_id}`, por lo mismo que las
# sísmicas — FastAPI resuelve por orden de registro y "weather" entraría por la
# ruta del detalle, fallando al parsearlo como UUID.


@router.get(
    "/weather",
    response_model=list[WeatherForecastRead],
    summary="Lluvia pronosticada por comuna, con riesgo de inundación",
    description=(
        "Pronóstico de precipitación de las próximas horas para cada comuna de "
        "la Región de Valparaíso, con el flag `riesgo_inundacion` ya calculado "
        "por el backend.\n\n"
        "**Es un pronóstico, no una emergencia.** `riesgo_inundacion: true` "
        "significa que el modelo anuncia lluvia suficiente para que la comuna "
        "tenga un problema; no significa que haya una inundación, y no es una "
        "alerta oficial: esas las declara SENAPRED.\n\n"
        "Devuelve **una fila por comuna** —la ventana más reciente de cada una— "
        "y sólo las comunas con lluvia: una comuna ausente es una comuna seca."
    ),
)
async def list_weather_forecast(
    service: WeatherServiceDep,
    hours: Annotated[
        int,
        Query(
            ge=1,
            le=48,
            description=(
                "Holgura hacia atrás, no histórico. El pronóstico se reescribe "
                "cada hora; 3 h cubren una corrida que llegó tarde sin arrastrar "
                "la lluvia de anteayer al mapa de hoy."
            ),
        ),
    ] = 3,
    solo_riesgo: Annotated[
        bool, Query(description="Sólo las comunas con `riesgo_inundacion`.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[WeatherForecastRead]:
    return await service.list_current(
        hours=hours, solo_riesgo=solo_riesgo, limit=limit
    )


@router.get(
    "/weather/geojson",
    response_model=GeoJSONFeatureCollection,
    summary="Lluvia pronosticada como GeoJSON",
    description=(
        "Mismo conjunto que `/events/weather`, en el formato que MapLibre GL JS "
        "consume directamente.\n\n"
        "`riesgo_inundacion` viaja como booleano real, así que la capa puede "
        "filtrar con `[\"==\", [\"get\", \"riesgo_inundacion\"], true]`. "
        "`motivos` viaja como una sola cadena: MapLibre serializa a texto "
        "cualquier arreglo anidado en las propiedades de un feature.\n\n"
        "Pensada para superponerse a `/incidents/geojson`: una tarde de 8 mm/h "
        "cambia la lectura de los avisos de vía cortada que llegan esa misma "
        "tarde."
    ),
)
async def weather_geojson(
    service: WeatherServiceDep,
    hours: Annotated[int, Query(ge=1, le=48)] = 3,
    solo_riesgo: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> GeoJSONFeatureCollection:
    return service.to_geojson(
        await service.list_current(hours=hours, solo_riesgo=solo_riesgo, limit=limit)
    )


@router.get(
    "/weather/stats",
    response_model=WeatherStats,
    summary="Resumen de la capa meteorológica vigente",
    description=(
        "Cuántas comunas tienen lluvia y cuántas cruzan un umbral, para una "
        "tarjeta de estado. Nunca filtra por riesgo: cuenta las dos cosas."
    ),
)
async def weather_stats(
    service: WeatherServiceDep,
    hours: Annotated[int, Query(ge=1, le=48)] = 3,
) -> WeatherStats:
    return service.stats(await service.list_current(hours=hours, limit=2000))


@router.get("/{public_id}", response_model=EventRead, summary="Detalle de un evento")
async def get_event(public_id: UUID, service: IngestServiceDep) -> EventRead:
    event = await service.repo.get_by_public_id(public_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="evento no encontrado")
    return EventRead.model_validate(event)


# --- helpers -----------------------------------------------------------------


#: El parseo vive en `app.api.v1.params` desde que los incidentes también
#: aceptan bbox. Se conserva el alias para no romper importaciones existentes.
_parse_bbox = parse_bbox


def _parse_near(
    lat: float | None, lon: float | None, radius_m: float | None
) -> tuple[float, float, float] | None:
    provided = [value is not None for value in (lat, lon, radius_m)]
    if not any(provided):
        return None
    if not all(provided):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="lat, lon y radius_m deben usarse juntos",
        )
    return (lat, lon, radius_m)  # type: ignore[return-value]
