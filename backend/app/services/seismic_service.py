"""Lectura de sismos para el mapa.

Delgado a propósito: no hay reglas de negocio que aplicar. Un sismo no se
correlaciona, no acumula confianza y no cambia de estado; lo único que hace este
servicio es acotar la ventana, aplicar el recorte geográfico correcto y armar el
GeoJSON.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories.seismic_repository import SeismicRepository
from app.schemas.seismic import SeismicEventRead


class SeismicService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SeismicRepository(session)

    @staticmethod
    def seismic_bbox() -> tuple[float, float, float, float]:
        """Recorte geográfico de los sismos.

        Es `usgs_bbox`, no `region_bbox`. La diferencia es deliberada y viene
        del collector: un sismo a 200 km de Valparaíso se siente en Valparaíso,
        así que aplicarle el recorte pensado para incendios puntuales borraría
        del mapa justo los eventos que explican por qué tembló.
        """
        bbox = settings.usgs_bbox
        return (bbox.west, bbox.south, bbox.east, bbox.north)

    async def list_recent(
        self,
        *,
        hours: int = 72,
        min_magnitude: float | None = None,
        max_depth_km: float | None = None,
        tsunami_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[SeismicEventRead]:
        rows = await self.repo.list_seismic(
            since=datetime.now(UTC) - timedelta(hours=hours),
            min_magnitude=min_magnitude,
            max_depth_km=max_depth_km,
            bbox=self.seismic_bbox(),
            tsunami_only=tsunami_only,
            limit=limit,
            offset=offset,
        )
        return [SeismicEventRead.from_row(row) for row in rows]


