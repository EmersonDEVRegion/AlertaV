"""Servicio de ingesta: única puerta de entrada de datos al sistema."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import CollectorStatus, EventSource
from app.models.event import CollectorRun, RawEvent
from app.repositories.event_repository import EventRepository
from app.schemas.event import (
    CitizenReportCreate,
    EventCreate,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    IngestResult,
)

logger = logging.getLogger(__name__)


class IngestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    # -- Ingesta -------------------------------------------------------------

    async def ingest_batch(self, events: Sequence[EventCreate]) -> IngestResult:
        """Ingesta idempotente de un lote ya validado."""
        outcome = await self.repo.upsert_many(events)
        await self.session.commit()

        logger.info(
            "batch ingerido",
            extra={
                "received": len(events),
                "inserted": outcome.inserted,
                "duplicated": outcome.duplicated,
                # Sin esto la resta no cuadraba y nadie tenía por qué mirarla:
                # `received` cuenta la lista original y las otras dos el lote ya
                # colapsado. Un corte de Chilquinta se perdía así en cada
                # corrida, en silencio y sin una sola línea roja.
                "collapsed": outcome.collapsed_count,
            },
        )

        if outcome.collapsed:
            # WARNING y no INFO: esto es la fuente entregando dos veces la misma
            # identidad en una sola lectura, y sólo hay dos explicaciones. O
            # repite una fila —benigno— o dos hechos distintos están chocando en
            # la construcción del `external_id`, y entonces se está perdiendo un
            # evento real por corrida. Los ids van en el mensaje porque son lo
            # que separa un diagnóstico del otro: repetidos entre corridas es lo
            # primero, distintos cada vez es lo segundo.
            #
            # Se recortan a diez para que una fuente que un día devuelva basura
            # no escriba un log de megabytes.
            logger.warning(
                "el lote traía identidades repetidas; se fundieron antes de insertar",
                extra={
                    "collapsed": outcome.collapsed_count,
                    "external_ids": sorted(set(outcome.collapsed))[:10],
                    "received": len(events),
                },
            )

        return IngestResult(
            received=len(events),
            inserted=outcome.inserted,
            duplicated=outcome.duplicated,
            collapsed=outcome.collapsed_count,
        )


    async def ingest_citizen_report(self, report: CitizenReportCreate) -> RawEvent:
        """Reporte desde la PWA.

        Se persiste como señal, nunca como incidente confirmado: la confianza
        sale de la línea base de `citizen` y sólo sube cuando el motor de
        correlación encuentra señales concordantes.
        """
        entity = await self.repo.add(report.to_event_create())
        await self.session.commit()
        return entity

    # -- Trazabilidad de collectors -----------------------------------------

    async def start_run(
        self, *, source: EventSource, collector: str, params: dict[str, Any]
    ) -> CollectorRun:
        run = CollectorRun(
            source=source,
            collector=collector,
            status=CollectorStatus.RUNNING.value,
            params=params,
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        # `refresh` es un SELECT, y un SELECT abre una transacción nueva
        # (autobegin). Sin este commit, la conexión quedaba «idle in transaction»
        # durante todo el `fetch()` del collector —minutos, en los que llaman a
        # Gemini y a Nominatim— y cinco collectors así agotaban el pool.
        # `expire_on_commit=False`: el objeto conserva sus atributos.
        await self.session.commit()
        return run

    async def resume_run(self, run_id: int, params: dict[str, Any]) -> CollectorRun:
        """Retoma una corrida que otro proceso abrió, sumándole `params`.

        La usa el inbox del webhook de Apify: la API deja la fila `running` al
        recibir el aviso y el proceso de workers la completa. Lanza `LookupError`
        si la fila no existe, que sólo puede pasar si alguien la borró a mano.
        """
        run = await self.session.get(CollectorRun, run_id)
        if run is None:
            raise LookupError(f"collector_runs.id={run_id} no existe")
        # JSONB no detecta mutaciones en el lugar: se reasigna un dict nuevo.
        run.params = {**(run.params or {}), **params}
        await self.session.commit()
        return run

    async def finish_run(
        self,
        run: CollectorRun,
        *,
        status: CollectorStatus,
        fetched: int = 0,
        inserted: int = 0,
        duplicate: int = 0,
        error: str | None = None,
    ) -> None:
        run.finished_at = datetime.now(UTC)
        run.status = status.value
        run.events_fetched = fetched
        run.events_inserted = inserted
        run.events_duplicate = duplicate
        run.error = error
        await self.session.commit()

    # -- Salida --------------------------------------------------------------

    @staticmethod
    def to_geojson(events: Sequence[RawEvent]) -> GeoJSONFeatureCollection:
        """FeatureCollection listo para MapLibre GL JS en la PWA."""
        features: list[GeoJSONFeature] = []
        for event in events:
            geometry = (
                {"type": "Point", "coordinates": [event.lon, event.lat]}
                if event.lat is not None and event.lon is not None
                else None
            )
            features.append(
                GeoJSONFeature(
                    geometry=geometry,
                    properties={
                        "public_id": str(event.public_id),
                        "timestamp": event.timestamp.isoformat(),
                        "source": event.source.value,
                        "type": event.type.value,
                        "confidence": event.confidence,
                        "text": event.text,
                        "commune": event.commune,
                        # Recordatorio explícito para la capa de presentación:
                        # una señal cruda no es un incidente confirmado.
                        "is_confirmed_incident": False,
                    },
                )
            )
        return GeoJSONFeatureCollection(features=features)

    @staticmethod
    def region_bbox() -> tuple[float, float, float, float]:
        bbox = settings.region_bbox
        return (bbox.west, bbox.south, bbox.east, bbox.north)
