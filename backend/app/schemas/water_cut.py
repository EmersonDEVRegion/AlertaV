"""Cortes de agua vigentes (Esval) como GeoJSON para el mapa."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.event import GeoJSONFeatureCollection
from app.schemas.vehicle_feed import VehicleFeedSource


class WaterCutSource(VehicleFeedSource):
    """Salud del collector de Esval, más cuándo leyó la API por última vez."""

    ultima_lectura: datetime | None = Field(
        None,
        description=(
            "Inicio de la última corrida que leyó la API de Esval (`success` o "
            "`partial`). `null` = nunca se leyó: la capa todavía no tiene datos "
            "y el mapa no la muestra."
        ),
    )


class WaterCutCollection(GeoJSONFeatureCollection):
    """Los cortes vigentes, con la salud de la fuente.

    Un corte sin coordenadas (el KML del visor falló) viaja con
    `geometry: null`: cuenta en el panel aunque no se pueda dibujar.
    """

    generado_en: datetime
    total: int
    fuente: WaterCutSource
