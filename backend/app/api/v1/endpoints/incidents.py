"""Endpoints de incidentes consolidados.

Contrapunto de `/events`: allí viven las **señales**, aquí los **hechos**. Un
cliente que quiera pintar el mapa operativo debe consumir esta ruta; `/events`
es para calibrar, auditar y depurar.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import IncidentServiceDep
from app.api.v1.params import parse_bbox
from app.models.enums import IncidentStatus, IncidentType
from app.schemas.event import GeoJSONFeatureCollection
from app.schemas.incident import (
    CorrelationRunResult,
    IncidentDetail,
    IncidentRead,
    IncidentStats,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get(
    "/active",
    response_model=list[IncidentRead],
    summary="Incidentes activos consolidados",
    description=(
        "Incidentes vigentes con su confianza agregada y la traza de cómo se "
        "calculó. `confidence` mide el fenómeno; `alert_level` y "
        "`alert_confidence` miden lo que declaró SENAPRED. Son ejes distintos: "
        "un incidente puede tener alerta roja vigente y aun así no estar "
        "confirmado por CONAF, y el mapa debería mostrar ambas cosas."
    ),
)
async def list_active_incidents(
    service: IncidentServiceDep,
    hours: Annotated[
        int | None,
        Query(ge=1, le=720, description="Ventana hacia atrás sobre `last_seen_at`."),
    ] = None,
    type: Annotated[list[IncidentType] | None, Query()] = None,
    status_: Annotated[
        list[IncidentStatus] | None,
        Query(alias="status", description="Por defecto: active y controlled."),
    ] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    commune: Annotated[str | None, Query(max_length=120)] = None,
    bbox: Annotated[
        str | None, Query(description="west,south,east,north en WGS84")
    ] = None,
    confirmed_only: Annotated[
        bool, Query(description="Sólo lo confirmado por CONAF o Bomberos.")
    ] = False,
    with_alert_only: Annotated[
        bool, Query(description="Sólo incidentes con alerta oficial vigente.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentRead]:
    incidents = await service.list_active(
        hours=hours,
        types=type,
        statuses=status_,
        min_confidence=min_confidence,
        commune=commune,
        bbox=parse_bbox(bbox),
        confirmed_only=confirmed_only,
        with_alert_only=with_alert_only,
        limit=limit,
        offset=offset,
    )
    return service.to_read(incidents)


@router.get(
    "/geojson",
    response_model=GeoJSONFeatureCollection,
    summary="Incidentes activos como GeoJSON",
    description=(
        "Mismo conjunto que `/active`, en el formato que MapLibre GL JS consume "
        "directamente. Aquí `is_confirmed_incident` sí es un dato real del "
        "motor, a diferencia del GeoJSON de señales crudas donde va fijo en "
        "`false`."
    ),
)
async def active_incidents_geojson(
    service: IncidentServiceDep,
    hours: Annotated[int | None, Query(ge=1, le=720)] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    confirmed_only: Annotated[bool, Query()] = False,
    bbox: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> GeoJSONFeatureCollection:
    incidents = await service.list_active(
        hours=hours,
        min_confidence=min_confidence,
        confirmed_only=confirmed_only,
        bbox=parse_bbox(bbox),
        limit=limit,
    )
    return service.to_geojson(incidents)


@router.get(
    "/stats",
    response_model=IncidentStats,
    summary="Resumen de la correlación",
)
async def incident_stats(
    service: IncidentServiceDep,
    hours: Annotated[int | None, Query(ge=1, le=8760)] = None,
) -> IncidentStats:
    return IncidentStats(**await service.stats(hours=hours))


@router.post(
    "/correlate",
    response_model=CorrelationRunResult,
    summary="Disparo manual del motor de correlación",
    description=(
        "Ejecuta una pasada completa. Pensado para calibrar y operar, no para "
        "el camino caliente: el disparo normal es el worker periódico "
        "(`python -m app.services.correlation.runner --loop`). Debe quedar "
        "detrás de autenticación de operador en producción."
    ),
)
async def correlate_now(service: IncidentServiceDep) -> CorrelationRunResult:
    result = await service.correlate_now()
    return CorrelationRunResult(**result.as_dict())


@router.get(
    "/{code}",
    response_model=IncidentDetail,
    summary="Detalle de un incidente con todas sus señales",
    description=(
        "Acepta el folio legible (`INC-2026-00142`) o el `public_id`. Devuelve "
        "cada señal con el motivo de su vínculo: `spatial` con su distancia en "
        "metros, `commune_text` con la comuna que produjo la coincidencia."
    ),
)
async def get_incident(code: str, service: IncidentServiceDep) -> IncidentDetail:
    detail = None
    if code.upper().startswith("INC-"):
        detail = await service.get_detail(code=code.upper())
    else:
        try:
            detail = await service.get_detail(public_id=UUID(code))
        except ValueError:
            detail = await service.get_detail(code=code)

    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="incidente no encontrado")
    return detail
