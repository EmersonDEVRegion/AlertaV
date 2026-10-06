"""Contrato de `GET /feed/noticias`: la prensa local, fuera del mapa.

Desde el 2026-10-06 una noticia no es un pin: llega con horas de atraso y el
mapa tiene que describir el presente. Se lee como un feed informativo, con la
hora de publicación a la vista para que nadie la confunda con algo en curso.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import EventType
from app.schemas.vehicle_feed import VehicleFeedSource


class NewsFeedItem(BaseModel):
    """Una nota de un medio local."""

    id: UUID = Field(..., description="`public_id` de la señal.")
    titular: str
    bajada: str | None = None
    medio: str | None = Field(default=None, description="«Pura Noticia», «Alerta Noticias»…")
    url: str | None = Field(default=None, description="Enlace a la nota en el medio.")
    comuna: str | None = Field(
        default=None, description="Comuna que dijo el medio o que se leyó del texto."
    )
    tipo: EventType = Field(..., description="Lo que el clasificador leyó: incendio, choque…")
    publicada_en: datetime = Field(
        ...,
        description=(
            "Cuándo se publicó. Si el portal no dio la hora, la cota superior "
            "conocida (ver `hora_aproximada`)."
        ),
    )
    hora_aproximada: bool = Field(
        default=False,
        description="El portal publicó sólo el día, o nada: la hora no es exacta.",
    )
    detectada_en: datetime = Field(..., description="Cuándo la leyó AlertaV.")


class NewsFeedResponse(BaseModel):
    generado_en: datetime
    horas: int
    total: int
    items: list[NewsFeedItem]
    fuente: VehicleFeedSource = Field(
        ..., description="Salud de `prensa_local`. Un feed vacío con la fuente caída no es calma."
    )
