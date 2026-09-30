"""Foto de la grilla de lluvia (migración 0017).

Una fila por grilla (hoy sólo `lluvia`), que se pisa en cada corrida: la capa
del mapa muestra el pronóstico vigente, no un historial. Los valores viajan en
JSONB porque se leen y se escriben enteros —nadie consulta una celda suelta— y
son ~255 números.

Si una corrida falla, la foto anterior se conserva y se anotan `attempted_at` y
`error`: el mapa sigue mostrando lo último que se supo, con su edad, y la ruta
dice que la fuente no respondió.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base

_SCHEMA = settings.DB_SCHEMA


class WeatherGrid(Base):
    __tablename__ = "weather_grids"
    __table_args__ = ({"schema": _SCHEMA},)

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: Cuándo se leyó con éxito la foto guardada. `None` si nunca.
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(String(60))
    step: Mapped[float | None] = mapped_column(Float)
    west: Mapped[float | None] = mapped_column(Float)
    north: Mapped[float | None] = mapped_column(Float)
    nx: Mapped[int | None] = mapped_column(Integer)
    ny: Mapped[int | None] = mapped_column(Integer)
    hours: Mapped[int | None] = mapped_column(Integer)
    #: Máximo de mm/h en las próximas `hours` horas, por celda, fila a fila de
    #: norte a sur y de oeste a este. `null` donde el modelo no dio dato.
    values: Mapped[list[Any] | None] = mapped_column(JSONB)
    #: Último intento, haya salido bien o mal.
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Error del último intento, o `None` si salió bien.
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["WeatherGrid"]
