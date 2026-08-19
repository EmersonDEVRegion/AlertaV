"""Servicio de lectura de incidentes — lo que consume el mapa.

Separado del `CorrelationEngine` a propósito: escribir incidentes y leerlos son
dos responsabilidades con ritmos distintos. El motor corre cada pocos minutos en
un proceso aparte; este servicio atiende peticiones de ciudadanos y no debe
poder, ni por accidente, disparar una correlación completa dentro del request de
alguien que sólo abrió el mapa.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import IncidentStatus, IncidentType, level_for
from app.models.incident import Incident
from app.repositories.incident_repository import IncidentRepository
from app.schemas.event import GeoJSONFeature, GeoJSONFeatureCollection
from app.schemas.incident import (
    IncidentDetail,
    IncidentEventLink,
    IncidentRead,
    confidence_label,
)
from app.services.correlation.engine import CorrelationEngine, CorrelationPass

logger = logging.getLogger(__name__)


class IncidentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = IncidentRepository(session)

    # -- Lectura --------------------------------------------------------------

    async def list_active(
        self,
        *,
        hours: int | None = None,
        types: Sequence[IncidentType] | None = None,
        statuses: Sequence[IncidentStatus] | None = None,
        min_confidence: float | None = None,
        commune: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        confirmed_only: bool = False,
        with_alert_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Incident]:
        window = hours if hours is not None else settings.CORRELATION_ACTIVE_WINDOW_HOURS
        since = datetime.now(UTC) - timedelta(hours=window)
        incidents = await self.repo.list_incidents(
            since=since,
            statuses=statuses,
            types=types,
            min_confidence=min_confidence,
            commune=commune,
            bbox=bbox,
            confirmed_only=confirmed_only,
            with_alert_only=with_alert_only,
            limit=limit,
            offset=offset,
        )
        return list(incidents)

    async def get_detail(self, *, code: str | None = None, public_id: UUID | None = None):
        if code is not None:
            incident = await self.repo.get_by_code(code)
        elif public_id is not None:
            incident = await self.repo.get_by_public_id(public_id)
        else:
            incident = None
        if incident is None:
            return None

        pairs = await self.repo.links_with_events(incident.id)
        detail = IncidentDetail.model_validate(incident)
        detail.events = [
            IncidentEventLink(
                raw_event_id=event.id,
                public_id=event.public_id,
                source=event.source,
                type=event.type.value,
                timestamp=event.timestamp,
                confidence=event.confidence,
                text=event.text,
                lat=event.lat,
                lon=event.lon,
                link_method=link.link_method,
                link_confidence=link.link_confidence,
                distance_m=link.distance_m,
                matched_commune=link.matched_commune,
                note=link.note,
            )
            for link, event in pairs
        ]
        return detail

    async def stats(self, *, hours: int | None = None) -> dict[str, Any]:
        since = (
            datetime.now(UTC) - timedelta(hours=hours) if hours is not None else None
        )
        return await self.repo.stats(since=since)

    # -- Salida ---------------------------------------------------------------

    @staticmethod
    def to_geojson(incidents: Sequence[Incident]) -> GeoJSONFeatureCollection:
        """FeatureCollection consumible por MapLibre GL JS.

        A diferencia del GeoJSON de `raw_events` —que lleva
        `is_confirmed_incident: false` fijo para que nadie pinte una detección
        satelital como incendio— aquí el flag es real y sale del motor.
        """
        features = [
            GeoJSONFeature(
                geometry={"type": "Point", "coordinates": [incident.lon, incident.lat]},
                properties={
                    "code": incident.code,
                    "public_id": str(incident.public_id),
                    "type": incident.type.value,
                    "status": incident.status.value,
                    "title": incident.title,
                    "confidence": incident.confidence,
                    "confidence_label": confidence_label(
                        incident.confidence, confirmed=incident.is_official_confirmed
                    ),
                    # Tramo operativo. Va en las propiedades para que la
                    # expresión `match` de MapLibre lea una clave ya calculada y
                    # no sea una segunda copia de los umbrales.
                    "confidence_level": level_for(incident.confidence).value,
                    "is_confirmed_incident": incident.is_official_confirmed,
                    "alert_level": incident.alert_level,
                    "alert_confidence": incident.alert_confidence,
                    "commune": incident.commune,
                    "event_count": incident.event_count,
                    "source_count": incident.source_count,
                    "sources": list(incident.sources or []),
                    "first_seen_at": incident.first_seen_at.isoformat(),
                    "last_seen_at": incident.last_seen_at.isoformat(),
                },
            )
            for incident in incidents
        ]
        return GeoJSONFeatureCollection(features=features)

    @staticmethod
    def to_read(incidents: Sequence[Incident]) -> list[IncidentRead]:
        return [IncidentRead.model_validate(incident) for incident in incidents]

    # -- Escritura (disparo manual) ------------------------------------------

    async def correlate_now(self) -> CorrelationPass:
        """Ejecuta una pasada bajo demanda.

        Existe para calibrar y para operar, no para el camino caliente: el
        disparo normal es el worker periódico. Debe quedar detrás de
        autenticación de operador en producción.
        """
        return await CorrelationEngine(self.session).run()
