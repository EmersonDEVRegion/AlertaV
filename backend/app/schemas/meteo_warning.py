"""Contrato de `GET /feed/avisos-meteo`: avisos, alertas y alarmas de la DMC."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.vehicle_feed import VehicleFeedSource


class MeteoWarningTableRow(BaseModel):
    zona: str
    valores: list[str]


class MeteoWarningTable(BaseModel):
    """Valores estimados por zona (km/h, mm, °C), sólo la sección de Valparaíso."""

    titulo: str | None = None
    columnas: list[str] = Field(default_factory=list)
    filas: list[MeteoWarningTableRow] = Field(default_factory=list)


class MeteoWarningZone(BaseModel):
    clave: str = Field(
        ..., description="Zona de pronóstico de la DMC: `Litoral`, `Cordillera_Costa`…"
    )
    nombre: str = Field(..., description="Nombre legible: «Cordillera de la Costa».")


class MeteoWarningItem(BaseModel):
    """Un aviso, alerta o alarma de la DMC que toca la Región de Valparaíso."""

    id: str = Field(..., description="Identificador de la emisión en la DMC.")
    tipo: Literal["Aviso", "Alerta", "Alarma"]
    severidad: int = Field(..., ge=1, le=3, description="1 = Aviso, 2 = Alerta, 3 = Alarma.")
    codigo: str | None = Field(None, description="`A561/2026`; `-N` marca una actualización.")
    fenomeno: str | None = None
    titulo: str | None = None
    emision: datetime | None = None
    desde: datetime | None = Field(
        None,
        description=(
            "Inicio del tramo del día que nombra la DMC («la tarde» → 12:00). "
            "La DMC no publica horas: es una lectura del texto."
        ),
    )
    desde_texto: str | None = Field(None, description="Tal como lo escribe la DMC.")
    hasta: datetime | None = Field(
        None, description="Fin del tramo («la noche del martes» → miércoles 06:00)."
    )
    hasta_texto: str | None = None
    en_curso: bool = Field(..., description="`desde` ya pasó (o no se pudo leer).")
    observacion: str | None = None
    alcance: Literal["region", "zonas"]
    zonas: list[MeteoWarningZone] = Field(
        default_factory=list, description="Vacía con `alcance = region`."
    )
    tabla: MeteoWarningTable | None = None
    url: str = Field(..., description="El mapa oficial de la DMC, para citarla.")


class MeteoWarningResponse(BaseModel):
    generado_en: datetime
    total: int
    items: list[MeteoWarningItem]
    fuente: VehicleFeedSource


class MeteoWarningZoneProperties(BaseModel):
    """Una zona de pronóstico con al menos un aviso vigente."""

    zona: str = Field(..., description="Clave de la DMC: `Litoral`, `Cordillera_Costa`…")
    nombre: str
    severidad: int = Field(..., ge=1, le=3, description="La mayor de sus avisos.")
    tipo: Literal["Aviso", "Alerta", "Alarma"] = Field(
        ..., description="El nivel de esa severidad."
    )
    avisos: int = Field(..., ge=1, description="Cuántos avisos vigentes la tocan.")
    fenomenos: str = Field(
        ..., description="«Tormentas Eléctricas · Viento», en orden de urgencia."
    )
    ids: str = Field(
        ...,
        description=(
            "Ids de los avisos separados por coma, para resaltar la zona de uno. "
            "Texto y no lista: MapLibre convierte las listas de propiedades en JSON."
        ),
    )


class MeteoWarningZoneFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: MeteoWarningZoneProperties


class MeteoWarningZoneCollection(BaseModel):
    """Las zonas con avisos vigentes. Sin avisos, `features` vacío."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    generado_en: datetime
    features: list[MeteoWarningZoneFeature]
