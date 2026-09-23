"""Contrato de `GET /feed/vehiculos`: avisos de vehículos de GBV.

No es un incidente ni una señal del mapa: es un aviso de consumo rápido. Por
eso no lleva coordenadas, ni confianza, ni tramo de color.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import VehicleLocation, VehicleStatus


class VehicleFeedItem(BaseModel):
    """Un vehículo robado, recuperado o abandonado."""

    id: UUID = Field(..., description="`public_id` del evento.")
    estado: VehicleStatus
    patente: str | None = Field(
        default=None,
        description=(
            "Normalizada (`LKXV55`). `null` si GBV no la publicó o publicó algo "
            "que no es una patente chilena válida."
        ),
    )
    tipo_vehiculo: str | None = None
    marca: str | None = None
    modelo: str | None = None
    color: str | None = None
    anio: int | None = None
    delito: str | None = None
    lugar: str | None = Field(
        default=None,
        description="Texto libre tal como lo publicó GBV. No es una dirección geocodificada.",
    )
    comuna: str | None = Field(
        default=None, description="Comuna de la V Región deducida del texto, si se pudo."
    )
    region: VehicleLocation = Field(
        ...,
        description=(
            "`v_region` si se reconoció una comuna o sector de la región; "
            "`sin_ubicar` si el texto no alcanza para decirlo. `otra` nunca "
            "llega a este feed."
        ),
    )
    recuperado_en: str | None = None
    autoridad: str | None = None
    tiempo_abandono: str | None = Field(
        default=None, description="Relativo, como lo publica GBV: `3 semanas`."
    )
    fecha_delito: date | None = Field(
        default=None, description="Día del delito según GBV (sin hora)."
    )
    fecha_precision: Literal["dia", "deteccion"] = Field(
        ...,
        description=(
            "`dia`: el hecho tiene fecha publicada. `deteccion`: no la tiene y "
            "la única referencia es `detectado_en`."
        ),
    )
    detectado_en: datetime = Field(
        ..., description="Cuándo lo vio AlertaV por primera vez. Es lo que mide la ventana."
    )
    url_fuente: str


class VehicleFeedSource(BaseModel):
    """Salud de la fuente. Un feed vacío con la fuente caída no es calma."""

    collector: str
    estado: str = Field(
        ...,
        description=(
            "`ok`, `degraded`, `failing`, `stale` o `never`, con las mismas reglas "
            "que `/collectors/health`."
        ),
    )
    ultima_corrida: datetime | None = None
    detalle: str | None = None


class VehicleFeedResponse(BaseModel):
    generado_en: datetime
    horas: int
    total: int
    items: list[VehicleFeedItem]
    fuente: VehicleFeedSource
