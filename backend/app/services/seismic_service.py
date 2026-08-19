"""Lectura de sismos para el mapa.

Delgado a propósito: no hay reglas de negocio que aplicar. Un sismo no se
correlaciona, no acumula confianza y no cambia de estado; lo único que hace este
servicio es acotar la ventana, aplicar el recorte geográfico correcto y armar el
GeoJSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories.seismic_repository import SeismicRepository
from app.schemas.event import GeoJSONFeature, GeoJSONFeatureCollection
from app.schemas.seismic import SeismicEventRead, SeismicStats


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

    @staticmethod
    def to_geojson(events: Sequence[SeismicEventRead]) -> GeoJSONFeatureCollection:
        """FeatureCollection para la capa sísmica de MapLibre.

        Sólo viajan escalares: MapLibre serializa los objetos anidados de las
        propiedades de un feature, así que anidar el detalle no serviría de nada
        del otro lado.
        """
        features = [
            GeoJSONFeature(
                geometry={"type": "Point", "coordinates": [event.lon, event.lat]},
                properties={
                    "public_id": str(event.public_id),
                    "usgs_id": event.usgs_id,
                    "timestamp": event.timestamp.isoformat(),
                    "magnitude": event.magnitude,
                    "mag_type": event.mag_type,
                    "depth_km": event.depth_km,
                    "place": event.place,
                    "commune": event.commune,
                    "felt_reports": event.felt_reports,
                    "tsunami": event.tsunami,
                    "pager_alert": event.pager_alert,
                    "review_status": event.review_status,
                    "usgs_url": event.usgs_url,
                    # Un sismo es un hecho medido, no una emergencia declarada.
                    # El mismo recordatorio que lleva el GeoJSON de señales
                    # crudas, por el mismo motivo.
                    "is_confirmed_incident": False,
                },
            )
            for event in events
        ]
        return GeoJSONFeatureCollection(features=features)

    @staticmethod
    def stats(events: Sequence[SeismicEventRead]) -> SeismicStats:
        magnitudes = [e.magnitude for e in events if e.magnitude is not None]
        return SeismicStats(
            total=len(events),
            max_magnitude=max(magnitudes) if magnitudes else None,
            felt_count=sum(1 for e in events if (e.felt_reports or 0) > 0),
            tsunami_flagged=sum(1 for e in events if e.tsunami),
        )
