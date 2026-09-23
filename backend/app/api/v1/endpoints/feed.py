"""Feeds de consumo rápido, paralelos al mapa.

Hoy uno solo: vehículos robados, recuperados y abandonados publicados por GBV.
No son incidentes ni señales del mapa —no tienen coordenadas ni confianza— y no
pasan por el motor de correlación.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import VehicleFeedServiceDep
from app.collectors.lugares import comuna_por_nombre
from app.models.enums import VehicleStatus
from app.schemas.vehicle_feed import VehicleFeedResponse
from app.services.vehicle_feed_service import FEED_MAX_HORAS

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get(
    "/vehiculos",
    response_model=VehicleFeedResponse,
    summary="Vehículos robados, recuperados y abandonados (GBV), últimas 48 h",
    description=(
        "Avisos que AlertaV vio por primera vez en las últimas `horas` (máximo "
        "48), del más nuevo al más viejo. La ventana se mide desde que AlertaV "
        "detectó el aviso (`detectado_en`), no desde la fecha del delito: GBV "
        "publica con atraso y los recuperados no traen fecha.\n\n"
        "Sólo avisos de la Región de Valparaíso (`region = v_region`) y, salvo "
        "`incluir_sin_ubicar=false`, los que el texto no permite ubicar "
        "(`sin_ubicar`). Lo que es claramente de otra región no sale nunca.\n\n"
        "`fuente.estado` distinto de `ok` significa que un feed vacío NO quiere "
        "decir que no haya avisos."
    ),
)
async def vehicle_feed(
    service: VehicleFeedServiceDep,
    horas: Annotated[
        int, Query(ge=1, le=FEED_MAX_HORAS, description="Ventana hacia atrás, en horas.")
    ] = FEED_MAX_HORAS,
    estado: Annotated[
        list[VehicleStatus] | None,
        Query(description="Uno o más estados. Sin este parámetro, los tres."),
    ] = None,
    comuna: Annotated[
        str | None,
        Query(max_length=60, description="Comuna de la V Región, con o sin tildes."),
    ] = None,
    incluir_sin_ubicar: Annotated[
        bool, Query(description="Incluir los avisos cuyo lugar no se pudo ubicar.")
    ] = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> VehicleFeedResponse:
    comuna_canonica: str | None = None
    if comuna is not None and comuna.strip():
        comuna_canonica = comuna_por_nombre(comuna)
        if comuna_canonica is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{comuna}' no es una comuna de la Región de Valparaíso",
            )

    return await service.feed(
        horas=horas,
        estados=estado,
        comuna=comuna_canonica,
        incluir_sin_ubicar=incluir_sin_ubicar,
        limit=limit,
    )
