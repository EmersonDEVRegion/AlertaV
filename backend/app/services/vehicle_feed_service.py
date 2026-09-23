"""Feed de vehículos de GBV: la lectura detrás de `GET /feed/vehiculos`."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.vehicles.gbv_worker import GbvCollector
from app.models.enums import VehicleLocation, VehicleStatus
from app.models.event import CollectorRun, RawEvent
from app.repositories.event_repository import EventRepository
from app.schemas.vehicle_feed import VehicleFeedItem, VehicleFeedResponse, VehicleFeedSource
from app.services.collector_health import estado_de_collector

logger = logging.getLogger(__name__)

#: El feed es de consumo rápido: lo de las últimas 48 horas y nada más.
FEED_MAX_HORAS = 48


class VehicleFeedService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventRepository(session)

    async def feed(
        self,
        *,
        horas: int = FEED_MAX_HORAS,
        estados: Sequence[VehicleStatus] | None = None,
        comuna: str | None = None,
        incluir_sin_ubicar: bool = True,
        limit: int = 100,
        ahora: datetime | None = None,
    ) -> VehicleFeedResponse:
        momento = ahora or datetime.now(UTC)
        horas = max(1, min(horas, FEED_MAX_HORAS))
        ubicaciones = [VehicleLocation.V_REGION]
        if incluir_sin_ubicar:
            ubicaciones.append(VehicleLocation.SIN_UBICAR)

        filas = await self.repo.list_vehicle_feed(
            since=momento - timedelta(hours=horas),
            statuses=estados,
            locations=ubicaciones,
            commune=comuna,
            limit=limit,
        )
        items = [item for item in (to_item(fila) for fila in filas) if item is not None]

        run = await self._ultima_corrida()
        referencia = (run.finished_at or run.started_at) if run else None
        return VehicleFeedResponse(
            generado_en=momento,
            horas=horas,
            total=len(items),
            items=items,
            fuente=VehicleFeedSource(
                collector=GbvCollector.name,
                estado=estado_de_collector(GbvCollector.name, run, ahora=momento),
                ultima_corrida=referencia,
                detalle=(run.error[:300] if run and run.error else None),
            ),
        )

    async def _ultima_corrida(self) -> CollectorRun | None:
        stmt = (
            select(CollectorRun)
            .where(CollectorRun.collector == GbvCollector.name)
            .order_by(desc(CollectorRun.started_at))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def to_item(fila: RawEvent) -> VehicleFeedItem | None:
    """Fila → ítem del feed. None si su `raw_data` no tiene la forma esperada.

    Tolerante a propósito: una fila escrita por una versión anterior del
    collector no puede tumbar el feed entero. Se descarta y queda en el log.
    """
    datos: Mapping[str, Any] = (fila.raw_data or {}).get("gbv") or {}
    try:
        return VehicleFeedItem(
            id=fila.public_id,
            estado=VehicleStatus(datos.get("estado")),
            patente=datos.get("patente"),
            tipo_vehiculo=datos.get("tipo_vehiculo"),
            marca=datos.get("marca"),
            modelo=datos.get("modelo"),
            color=datos.get("color"),
            anio=datos.get("anio"),
            delito=datos.get("delito"),
            lugar=datos.get("lugar"),
            comuna=datos.get("comuna"),
            region=VehicleLocation(datos.get("region")),
            recuperado_en=datos.get("recuperado_en"),
            autoridad=datos.get("autoridad"),
            tiempo_abandono=datos.get("tiempo_abandono"),
            fecha_delito=_fecha(datos.get("fecha_delito")),
            fecha_precision=datos.get("fecha_precision") or "deteccion",
            detectado_en=fila.ingested_at,
            url_fuente=str(datos.get("url") or ""),
        )
    except (ValueError, PydanticValidationError) as exc:
        logger.warning(
            "fila de GBV con raw_data inesperado; se omite del feed",
            extra={"public_id": str(fila.public_id), "error": str(exc)[:200]},
        )
        return None


def _fecha(valor: Any) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        return None
