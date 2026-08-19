"""Acceso a datos de `seismic_details`.

Mismo criterio que `EventRepository`: el SQL vive acá y sólo acá.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EventSource
from app.models.event import RawEvent
from app.models.seismic import SeismicDetail

#: Columnas que el upsert refresca. `raw_event_id` y `usgs_id` no se tocan: son
#: la identidad de la fila. Todo lo demás sí cambia entre la solución preliminar
#: y la revisada —la magnitud se corrige, la profundidad se recalcula, el nivel
#: PAGER aparece horas después—, y quedarse con la primera lectura sería
#: conservar el peor dato disponible.
_UPSERT_UPDATE_COLUMNS = (
    "magnitude",
    "mag_type",
    "depth_km",
    "place",
    "felt_reports",
    "tsunami",
    "pager_alert",
    "significance",
    "review_status",
    "usgs_url",
    "source_updated_at",
)


class SeismicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> int:
        """Inserta o actualiza los detalles sísmicos. Devuelve filas afectadas.

        Idempotente por `raw_event_id`, que es la clave primaria y espejo del
        `external_id` de la señal. Releer el feed cinco minutos después no
        duplica nada: actualiza la fila con la solución más reciente.
        """
        if not rows:
            return 0

        stmt = pg_insert(SeismicDetail).values(list(rows))
        stmt = stmt.on_conflict_do_update(
            index_elements=[SeismicDetail.raw_event_id],
            set_={
                column: getattr(stmt.excluded, column)
                for column in _UPSERT_UPDATE_COLUMNS
            },
        ).returning(SeismicDetail.raw_event_id)

        result = await self.session.execute(stmt)
        return len(result.fetchall())

    async def get_by_usgs_id(self, usgs_id: str) -> SeismicDetail | None:
        stmt = select(SeismicDetail).where(SeismicDetail.usgs_id == usgs_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # -- Lectura para el mapa -------------------------------------------------

    async def list_seismic(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        min_magnitude: float | None = None,
        max_depth_km: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        tsunami_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> Sequence[Row[Any]]:
        """Sismos con su detalle, listos para pintar.

        `JOIN` y no `LEFT JOIN`: una fila de `raw_events` con `source='usgs'` sin
        su fila satélite es una escritura a medias, y devolverla con la magnitud
        en `NULL` obligaría a la capa de presentación a decidir qué hacer con un
        sismo sin magnitud. Si aparece una, es un error del collector que hay que
        ver en los logs, no algo que el mapa deba disimular.

        El orden es por magnitud descendente dentro de la ventana: si el límite
        recorta, que recorte lo irrelevante. `NULLS LAST` porque una solución
        preliminar sin magnitud no encabeza ninguna lista.
        """
        stmt = (
            select(
                RawEvent.public_id,
                RawEvent.timestamp,
                RawEvent.lat,
                RawEvent.lon,
                RawEvent.commune,
                RawEvent.province,
                SeismicDetail.usgs_id,
                SeismicDetail.magnitude,
                SeismicDetail.mag_type,
                SeismicDetail.depth_km,
                SeismicDetail.place,
                SeismicDetail.felt_reports,
                SeismicDetail.tsunami,
                SeismicDetail.pager_alert,
                SeismicDetail.significance,
                SeismicDetail.review_status,
                SeismicDetail.usgs_url,
            )
            .join(SeismicDetail, SeismicDetail.raw_event_id == RawEvent.id)
            .where(
                RawEvent.source == EventSource.USGS,
                RawEvent.lat.is_not(None),
                RawEvent.lon.is_not(None),
            )
        )

        if since is not None:
            stmt = stmt.where(RawEvent.timestamp >= since)
        if until is not None:
            stmt = stmt.where(RawEvent.timestamp <= until)
        if min_magnitude is not None:
            # Un sismo sin magnitud no pasa un filtro de magnitud mínima: no se
            # puede afirmar que la cumpla.
            stmt = stmt.where(SeismicDetail.magnitude >= min_magnitude)
        if max_depth_km is not None:
            stmt = stmt.where(SeismicDetail.depth_km <= max_depth_km)
        if tsunami_only:
            stmt = stmt.where(SeismicDetail.tsunami.is_(True))
        if bbox is not None:
            west, south, east, north = bbox
            stmt = stmt.where(
                RawEvent.lon >= west,
                RawEvent.lon <= east,
                RawEvent.lat >= south,
                RawEvent.lat <= north,
            )

        stmt = (
            stmt.order_by(SeismicDetail.magnitude.desc().nullslast())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).all()
