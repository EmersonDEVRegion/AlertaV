"""Acceso a `weather_grids`: una fila por grilla, que se pisa en cada corrida."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.weather_grid import WeatherGrid

if TYPE_CHECKING:
    from app.services.rain_grid_service import GridSpec


class WeatherGridRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> WeatherGrid | None:
        return (
            await self.session.execute(select(WeatherGrid).where(WeatherGrid.key == key))
        ).scalar_one_or_none()

    async def save(
        self,
        key: str,
        *,
        grid: GridSpec,
        values: Sequence[float | None],
        model: str,
        hours: int,
        now: datetime,
    ) -> None:
        """Guarda una foto nueva y limpia el error del intento anterior."""
        row = {
            "generated_at": now,
            "model": model,
            "step": grid.step,
            "west": grid.west,
            "north": grid.north,
            "nx": grid.nx,
            "ny": grid.ny,
            "hours": hours,
            "values": list(values),
            "attempted_at": now,
            "error": None,
            "updated_at": now,
        }
        stmt = pg_insert(WeatherGrid).values(key=key, **row)
        await self.session.execute(
            stmt.on_conflict_do_update(index_elements=[WeatherGrid.key], set_=row)
        )

    async def mark_failure(self, key: str, *, error: str, now: datetime) -> None:
        """Anota un intento fallido sin tocar la foto que hubiera."""
        row = {"attempted_at": now, "error": error, "updated_at": now}
        stmt = pg_insert(WeatherGrid).values(key=key, **row)
        await self.session.execute(
            stmt.on_conflict_do_update(index_elements=[WeatherGrid.key], set_=row)
        )
