"""Endpoints de eventos crudos.

Importante: esta API expone *señales*, no incidentes. El endpoint de incidentes
correlacionados llega en el siguiente hito.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import IngestServiceDep, SeismicServiceDep
from app.api.v1.params import parse_bbox
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

router = APIRouter(prefix="/events", tags=["events"])


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
        "señal, nunca como incidente confirmado."
    ),
)
async def create_citizen_report(
    report: CitizenReportCreate, service: IngestServiceDep
) -> EventRead:
    entity = await service.ingest_citizen_report(report)
    return EventRead.model_validate(entity)


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
