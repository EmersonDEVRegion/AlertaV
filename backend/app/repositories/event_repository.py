"""Acceso a datos de `raw_events`.

Aísla el SQL del resto de la aplicación: la API y los collectors hablan con el
servicio, y sólo el servicio habla con este repositorio.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import Select, cast, func, literal_column, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EventSource, EventType
from app.models.event import RawEvent
from app.schemas.event import EventCreate

#: Geografía usada para distancias en metros reales.
_GEOGRAPHY = Geography(geometry_type="POINT", srid=4326)

#: Columnas que el upsert refresca cuando la fuente reemite la misma
#: observación. `timestamp`, `source` y `external_id` NO se tocan: son la
#: identidad del evento.
_UPSERT_UPDATE_COLUMNS = ("type", "lat", "lon", "text", "confidence", "raw_data")


def _dedupe_batch(events: Sequence[EventCreate]) -> list[EventCreate]:
    """Colapsa duplicados dentro del mismo lote.

    Postgres aborta con "ON CONFLICT DO UPDATE cannot affect row a second time"
    si un único INSERT trae dos filas con la misma clave de conflicto. FIRMS
    puede devolver la misma detección repetida entre sensores solapados, así que
    esto no es teórico. Gana la última ocurrencia.
    """
    seen: dict[tuple[EventSource, str], int] = {}
    result: list[EventCreate] = []
    for event in events:
        if event.external_id is None:
            result.append(event)
            continue
        key = (event.source, event.external_id)
        if key in seen:
            result[seen[key]] = event
        else:
            seen[key] = len(result)
            result.append(event)
    return result


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Escritura -----------------------------------------------------------

    async def upsert_many(self, events: Sequence[EventCreate]) -> tuple[int, int]:
        """Inserta un lote de forma idempotente. Devuelve `(insertados, duplicados)`.

        La idempotencia se apoya en el índice único parcial
        `uq_raw_events_source_external_id`. Los eventos sin `external_id`
        (reportes ciudadanos) no participan de la inferencia de conflicto y
        siempre se insertan: dos vecinos reportando el mismo humo son dos
        señales distintas, y esa multiplicidad es justamente lo que eleva la
        confianza del incidente.

        `xmax = 0` distingue fila insertada de fila actualizada: en una fila
        recién insertada el id de transacción de borrado es 0.
        """
        deduped = _dedupe_batch(events)
        if not deduped:
            return (0, 0)

        rows = [event.to_orm_kwargs() for event in deduped]

        stmt = pg_insert(RawEvent).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[RawEvent.source, RawEvent.external_id],
            index_where=sa_text("external_id IS NOT NULL"),
            set_={
                column: getattr(stmt.excluded, column)
                for column in _UPSERT_UPDATE_COLUMNS
            },
        ).returning(literal_column("(xmax = 0)").label("inserted"))

        result = await self.session.execute(stmt)
        flags = [bool(row.inserted) for row in result]
        inserted = sum(flags)
        return (inserted, len(flags) - inserted)

    async def ids_by_external_id(
        self, source: EventSource, external_ids: Sequence[str]
    ) -> dict[str, int]:
        """Mapa `external_id → id` para una fuente. Vacío si no se pide nada.

        `upsert_many` devuelve cuántas filas entraron, no cuáles: la sentencia es
        un INSERT masivo y hacerle devolver los ids obligaría a cambiar su
        contrato para todos los collectors. Los que necesitan colgar datos
        propios de la fila recién escrita —hoy sólo el de sismos, con
        `seismic_details`— los recuperan acá con un SELECT acotado por el índice
        único `uq_raw_events_source_external_id`. Es una consulta por corrida
        sobre un puñado de filas.
        """
        wanted = [external_id for external_id in external_ids if external_id]
        if not wanted:
            return {}

        stmt = select(RawEvent.external_id, RawEvent.id).where(
            RawEvent.source == source, RawEvent.external_id.in_(wanted)
        )
        result = await self.session.execute(stmt)
        return dict(result.all())  # type: ignore[arg-type]

    async def add(self, event: EventCreate) -> RawEvent:
        """Inserta un evento y devuelve la entidad persistida."""
        entity = RawEvent(**event.to_orm_kwargs())
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    # -- Lectura -------------------------------------------------------------

    async def get_by_public_id(self, public_id: UUID) -> RawEvent | None:
        stmt = select(RawEvent).where(RawEvent.public_id == public_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        sources: Sequence[EventSource] | None = None,
        types: Sequence[EventType] | None = None,
        min_confidence: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        near: tuple[float, float, float] | None = None,
        only_unprocessed: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[RawEvent]:
        stmt = self._apply_filters(
            select(RawEvent),
            since=since,
            until=until,
            sources=sources,
            types=types,
            min_confidence=min_confidence,
            bbox=bbox,
            near=near,
            only_unprocessed=only_unprocessed,
        )
        stmt = stmt.order_by(RawEvent.timestamp.desc()).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def count_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        sources: Sequence[EventSource] | None = None,
        types: Sequence[EventType] | None = None,
        min_confidence: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        near: tuple[float, float, float] | None = None,
        only_unprocessed: bool = False,
    ) -> int:
        stmt = self._apply_filters(
            select(func.count()).select_from(RawEvent),
            since=since,
            until=until,
            sources=sources,
            types=types,
            min_confidence=min_confidence,
            bbox=bbox,
            near=near,
            only_unprocessed=only_unprocessed,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    def _apply_filters(
        self,
        stmt: Select,
        *,
        since: datetime | None,
        until: datetime | None,
        sources: Sequence[EventSource] | None,
        types: Sequence[EventType] | None,
        min_confidence: float | None,
        bbox: tuple[float, float, float, float] | None,
        near: tuple[float, float, float] | None,
        only_unprocessed: bool,
    ) -> Select:
        if since is not None:
            stmt = stmt.where(RawEvent.timestamp >= since)
        if until is not None:
            stmt = stmt.where(RawEvent.timestamp <= until)
        if sources:
            stmt = stmt.where(RawEvent.source.in_(list(sources)))
        if types:
            stmt = stmt.where(RawEvent.type.in_(list(types)))
        if min_confidence is not None:
            stmt = stmt.where(RawEvent.confidence >= min_confidence)
        if only_unprocessed:
            stmt = stmt.where(RawEvent.processed_at.is_(None))
        if bbox is not None:
            west, south, east, north = bbox
            stmt = stmt.where(
                func.ST_Intersects(
                    RawEvent.geom,
                    func.ST_MakeEnvelope(west, south, east, north, 4326),
                )
            )
        if near is not None:
            lat, lon, radius_m = near
            # ::geography → distancia en metros reales; el índice GiST sobre
            # geom se sigue usando para el pre-filtrado por bounding box.
            stmt = stmt.where(
                func.ST_DWithin(
                    cast(RawEvent.geom, _GEOGRAPHY),
                    cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), _GEOGRAPHY),
                    radius_m,
                )
            )
        return stmt

    # -- Agregados -----------------------------------------------------------

    async def stats(self, *, since: datetime | None = None) -> dict[str, Any]:
        base = select(RawEvent)
        if since is not None:
            base = base.where(RawEvent.timestamp >= since)
        subq = base.subquery()

        totals = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    func.count(subq.c.geom).label("georeferenced"),
                    func.min(subq.c.timestamp).label("first_event_at"),
                    func.max(subq.c.timestamp).label("last_event_at"),
                ).select_from(subq)
            )
        ).one()

        by_source = (
            await self.session.execute(
                select(subq.c.source, func.count()).group_by(subq.c.source)
            )
        ).all()
        by_type = (
            await self.session.execute(
                select(subq.c.type, func.count()).group_by(subq.c.type)
            )
        ).all()

        def _key(value: Any) -> str:
            return value.value if hasattr(value, "value") else str(value)

        return {
            "total": totals.total or 0,
            "georeferenced": totals.georeferenced or 0,
            "first_event_at": totals.first_event_at,
            "last_event_at": totals.last_event_at,
            "by_source": {_key(source): count for source, count in by_source},
            "by_type": {_key(event_type): count for event_type, count in by_type},
        }

    async def find_spatiotemporal_neighbours(
        self,
        *,
        lat: float,
        lon: float,
        timestamp: datetime,
        radius_m: float = 2000.0,
        window_minutes: int = 60,
        exclude_id: int | None = None,
    ) -> Sequence[RawEvent]:
        """Señales cercanas en espacio y tiempo.

        Consulta base del futuro motor de correlación: es la que convierte
        señales sueltas en un incidente. Se incluye desde ahora para poder
        validar el índice GiST combinado contra datos reales desde el día uno.
        """
        window = timedelta(minutes=window_minutes)
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)

        stmt = (
            select(RawEvent)
            .where(RawEvent.geom.isnot(None))
            .where(
                func.ST_DWithin(
                    cast(RawEvent.geom, _GEOGRAPHY),
                    cast(point, _GEOGRAPHY),
                    radius_m,
                )
            )
            .where(RawEvent.timestamp.between(timestamp - window, timestamp + window))
            .order_by(RawEvent.timestamp.asc())
        )
        if exclude_id is not None:
            stmt = stmt.where(RawEvent.id != exclude_id)
        return (await self.session.execute(stmt)).scalars().all()
