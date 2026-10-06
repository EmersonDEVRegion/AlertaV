"""Feed de prensa local: la lectura detrás de `GET /feed/noticias`.

Las noticias siguen entrando como siempre (`prensa_local` → `raw_events`), pero
desde el 2026-10-06 no pasan por el motor (`CORRELATION_PRENSA`): no abren
incidentes ni se pegan a uno. Este feed es el único lugar donde se leen.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EventType
from app.models.event import CollectorRun, RawEvent
from app.repositories.event_repository import EventRepository
from app.schemas.news_feed import NewsFeedItem, NewsFeedResponse
from app.schemas.vehicle_feed import VehicleFeedSource
from app.services.collector_health import estado_de_collector

logger = logging.getLogger(__name__)

#: El collector que alimenta el feed. Literal para no importar el árbol de
#: collectors de prensa (BeautifulSoup, feedparser) desde la API.
COLLECTOR = "prensa_local"

#: Lo que el feed muestra por defecto y su techo.
FEED_HORAS = 24
FEED_MAX_HORAS = 48


class NewsFeedService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    async def feed(
        self,
        *,
        horas: int = FEED_HORAS,
        limit: int = 60,
        ahora: datetime | None = None,
    ) -> NewsFeedResponse:
        momento = ahora or datetime.now(UTC)
        horas = max(1, min(horas, FEED_MAX_HORAS))
        filas = await self.repo.list_news_feed(since=momento - timedelta(hours=horas), limit=limit)
        items = [item for item in (to_item(fila) for fila in filas) if item is not None]

        run = await self._ultima_corrida()
        referencia = (run.finished_at or run.started_at) if run else None
        return NewsFeedResponse(
            generado_en=momento,
            horas=horas,
            total=len(items),
            items=items,
            fuente=VehicleFeedSource(
                collector=COLLECTOR,
                estado=estado_de_collector(COLLECTOR, run, ahora=momento),
                ultima_corrida=referencia,
                detalle=(run.error[:300] if run and run.error else None),
            ),
        )

    async def _ultima_corrida(self) -> CollectorRun | None:
        stmt = (
            select(CollectorRun)
            .where(CollectorRun.collector == COLLECTOR)
            .order_by(desc(CollectorRun.started_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = " ".join(valor.split())
    return limpio or None


def to_item(fila: RawEvent) -> NewsFeedItem | None:
    """Fila → ítem. None si no hay titular ni texto con qué mostrarla."""
    datos: Mapping[str, Any] = fila.raw_data or {}
    prensa: Mapping[str, Any] = datos.get("_prensa") or {}
    titular = _texto(datos.get("titular")) or _texto((fila.text or "")[:240])
    if titular is None:
        return None
    try:
        return NewsFeedItem(
            id=fila.public_id,
            titular=titular,
            bajada=_texto(datos.get("bajada")),
            medio=_texto(prensa.get("medio")) or _texto(prensa.get("portal")),
            url=_texto(datos.get("url")),
            comuna=_texto(datos.get("comuna")),
            tipo=EventType(getattr(fila.type, "value", fila.type)),
            publicada_en=fila.timestamp,
            hora_aproximada=bool(prensa.get("resolucion_dia")) or not prensa.get("fecha_declarada"),
            detectada_en=fila.ingested_at,
        )
    except (ValueError, PydanticValidationError) as exc:
        logger.warning(
            "noticia con raw_data inesperado; se omite del feed",
            extra={"public_id": str(fila.public_id), "error": str(exc)[:200]},
        )
        return None
