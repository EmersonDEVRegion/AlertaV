"""Acceso a datos de las notificaciones push.

Mismo criterio que el resto de `repositories/`: el SQL vive acá y sólo acá. El
notificador pregunta «a quién le aviso de esto» y recibe filas; no arma
consultas.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.enums import EventSource, EventType, IncidentStatus
from app.models.event import RawEvent
from app.models.incident import Incident
from app.models.push import PushDelivery, PushSubscription
from app.models.seismic import SeismicDetail
from app.services.push.rules import QuakeView

_GEOGRAPHY = Geography(geometry_type="POINT", srid=4326)

#: Tope del radio de aviso de una suscripción. Es el mismo del CHECK de la
#: tabla, y se usa como prefiltro constante: `ST_DWithin` sólo aprovecha el
#: índice cuando el radio se conoce antes de recorrer la tabla, y el radio de
#: cada suscripción es una columna de la misma tabla que se está recorriendo.
MAX_SUBSCRIPTION_RADIUS_M = 20_000.0


@dataclass(frozen=True, slots=True)
class Recipient:
    """Una suscripción a la que corresponde avisarle algo, y a qué distancia."""

    subscription_id: int
    endpoint: str
    p256dh: str
    auth: str
    distance_m: float


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Resultado de un envío, listo para registrarse."""

    delivery_id: int
    subscription_id: int
    status: str  # sent | failed | gone
    http_status: int | None


class PushRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Suscripciones --------------------------------------------------------

    async def upsert_subscription(
        self,
        *,
        endpoint: str,
        p256dh: str,
        auth: str,
        lat: float,
        lon: float,
        accuracy_m: float | None,
        radius_m: float,
        notify_incidents: bool,
        notify_seismic: bool,
    ) -> PushSubscription:
        """Crea la suscripción o la actualiza si el navegador ya la tenía.

        La PWA la reenvía cada vez que se abre con una ubicación nueva, así que
        éste es el camino más transitado del módulo y tiene que ser una sola
        sentencia. Las claves se sobrescriben siempre: un navegador que renovó su
        suscripción puede conservar el endpoint y cambiar las claves, y
        quedarse con las viejas sería cifrar para nadie.

        Reiniciar `consecutive_failures` también es deliberado: que el navegador
        vuelva a registrarse es la mejor prueba de que el canal está vivo.
        """
        values: dict[str, Any] = {
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "lat": lat,
            "lon": lon,
            "accuracy_m": accuracy_m,
            "radius_m": radius_m,
            "notify_incidents": notify_incidents,
            "notify_seismic": notify_seismic,
        }
        insert = pg_insert(PushSubscription).values(**values)
        stmt = insert.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={
                **{key: insert.excluded[key] for key in values if key != "endpoint"},
                "location_updated_at": func.now(),
                "consecutive_failures": 0,
            },
        ).returning(PushSubscription)
        result = await self.session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalar_one()

    async def get_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        stmt = select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_by_endpoint(self, endpoint: str) -> bool:
        result = await self.session.execute(
            delete(PushSubscription)
            .where(PushSubscription.endpoint == endpoint)
            .returning(PushSubscription.id)
        )
        return result.first() is not None

    async def count_subscriptions(self) -> int:
        stmt = select(func.count()).select_from(PushSubscription)
        return int((await self.session.execute(stmt)).scalar_one())

    # -- Qué avisar -----------------------------------------------------------

    async def recent_active_incidents(self, *, since: datetime) -> Sequence[Incident]:
        """Incidentes activos que aparecieron después de `since`.

        Se filtra por `first_seen_at` y no por `last_seen_at`: lo que se avisa
        es que *apareció* una emergencia, no que siga habiendo señales de una
        conocida. Un incendio de ayer que hoy recibe un tuit nuevo no es noticia
        para el teléfono.
        """
        stmt = (
            select(Incident)
            .where(Incident.status == IncidentStatus.ACTIVE)
            .where(Incident.first_seen_at >= since)
            .order_by(Incident.first_seen_at.asc())
            .limit(500)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def recent_quakes(self, *, since: datetime) -> list[QuakeView]:
        """Sismos de las dos redes desde `since`, con o sin magnitud.

        Sin filtro de magnitud a propósito: la deduplicación entre el CSN y el
        USGS necesita ver las dos versiones de cada sismo aunque una quede bajo
        el umbral. El umbral se aplica después, sobre la versión elegida.
        """
        stmt = (
            select(
                RawEvent.external_id,
                RawEvent.source,
                RawEvent.timestamp,
                RawEvent.lat,
                RawEvent.lon,
                SeismicDetail.magnitude,
                SeismicDetail.mag_type,
                SeismicDetail.depth_km,
                SeismicDetail.place,
                SeismicDetail.usgs_url,
            )
            .join(SeismicDetail, SeismicDetail.raw_event_id == RawEvent.id)
            .where(
                RawEvent.type == EventType.EARTHQUAKE,
                RawEvent.source.in_([EventSource.CSN, EventSource.USGS]),
                RawEvent.timestamp >= since,
                RawEvent.lat.is_not(None),
                RawEvent.lon.is_not(None),
                RawEvent.external_id.is_not(None),
            )
            .order_by(RawEvent.timestamp.asc())
            .limit(200)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            QuakeView(
                key=row.external_id,
                provider=row.source.value if hasattr(row.source, "value") else str(row.source),
                timestamp=row.timestamp,
                lat=float(row.lat),
                lon=float(row.lon),
                magnitude=row.magnitude,
                mag_type=row.mag_type,
                depth_km=row.depth_km,
                place=row.place,
                url=row.usgs_url,
            )
            for row in rows
        ]

    # -- A quién avisar -------------------------------------------------------

    async def recipients_for_incident(
        self, *, incident_id: int, code: str, lat: float, lon: float, limit: int = 5000
    ) -> list[Recipient]:
        """Suscripciones dentro de su propio radio que todavía no recibieron el aviso.

        «Todavía no recibieron» incluye a los incidentes absorbidos: si el
        INC-00280 se fusionó dentro del INC-00281, quien ya supo del 280 no
        tiene que enterarse de nuevo por el 281. Es el mismo incendio con otro
        folio.
        """
        merged = aliased(Incident)
        sub_geog = func.cast(PushSubscription.geom, _GEOGRAPHY)
        point_geog = func.cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), _GEOGRAPHY)
        distance = func.ST_Distance(sub_geog, point_geog)

        known_codes = (
            select(merged.code).where(merged.merged_into_id == incident_id).scalar_subquery()
        )
        already = exists().where(
            PushDelivery.subscription_id == PushSubscription.id,
            PushDelivery.kind == "incident",
            or_(
                PushDelivery.subject_key == code,
                PushDelivery.subject_key.in_(known_codes),
            ),
        )

        stmt = (
            select(
                PushSubscription.id,
                PushSubscription.endpoint,
                PushSubscription.p256dh,
                PushSubscription.auth,
                distance.label("distance_m"),
            )
            .where(PushSubscription.notify_incidents.is_(True))
            .where(func.ST_DWithin(sub_geog, point_geog, MAX_SUBSCRIPTION_RADIUS_M))
            .where(distance <= PushSubscription.radius_m)
            .where(~already)
            .order_by(distance.asc())
            .limit(limit)
        )
        return [
            Recipient(
                subscription_id=row.id,
                endpoint=row.endpoint,
                p256dh=row.p256dh,
                auth=row.auth,
                distance_m=float(row.distance_m),
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def recipients_for_quake(
        self,
        *,
        lat: float,
        lon: float,
        reach_m: float,
        keys: Sequence[str],
        limit: int = 20_000,
    ) -> list[Recipient]:
        """Suscripciones dentro del radio de percepción sin aviso de este sismo.

        `keys` son las claves de TODAS las versiones del sismo (CSN y USGS): si
        ya se avisó con cualquiera, no se vuelve a avisar.
        """
        sub_geog = func.cast(PushSubscription.geom, _GEOGRAPHY)
        point_geog = func.cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), _GEOGRAPHY)
        distance = func.ST_Distance(sub_geog, point_geog)
        already = exists().where(
            PushDelivery.subscription_id == PushSubscription.id,
            PushDelivery.kind == "seismic",
            PushDelivery.subject_key.in_(list(keys)),
        )
        stmt = (
            select(
                PushSubscription.id,
                PushSubscription.endpoint,
                PushSubscription.p256dh,
                PushSubscription.auth,
                distance.label("distance_m"),
            )
            .where(PushSubscription.notify_seismic.is_(True))
            .where(func.ST_DWithin(sub_geog, point_geog, reach_m))
            .where(~already)
            .order_by(distance.asc())
            .limit(limit)
        )
        return [
            Recipient(
                subscription_id=row.id,
                endpoint=row.endpoint,
                p256dh=row.p256dh,
                auth=row.auth,
                distance_m=float(row.distance_m),
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    # -- Registro de envíos ---------------------------------------------------

    async def reserve_deliveries(
        self, *, kind: str, subject_key: str, recipients: Sequence[Recipient]
    ) -> dict[int, int]:
        """Reserva un envío por destinatario. Devuelve {subscription_id: delivery_id}.

        Sólo vuelven las filas que se insertaron de verdad. Si otro notificador
        reservó primero —un deploy que se solapa con el anterior— el
        `ON CONFLICT DO NOTHING` descarta esa fila y acá no aparece: nadie envía
        dos veces.
        """
        if not recipients:
            return {}
        stmt = (
            pg_insert(PushDelivery)
            .values(
                [
                    {
                        "subscription_id": recipient.subscription_id,
                        "kind": kind,
                        "subject_key": subject_key,
                        "distance_m": round(recipient.distance_m, 1),
                        "status": "pending",
                    }
                    for recipient in recipients
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    PushDelivery.subscription_id,
                    PushDelivery.kind,
                    PushDelivery.subject_key,
                ]
            )
            .returning(PushDelivery.subscription_id, PushDelivery.id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row.subscription_id: row.id for row in rows}

    async def record_outcomes(
        self, outcomes: Sequence[DeliveryOutcome], *, now: datetime, max_failures: int
    ) -> dict[str, int]:
        """Cierra los envíos y actualiza la salud de cada suscripción.

        * `sent` limpia el contador de fallos.
        * `gone` (404/410) borra la suscripción: el navegador la dio de baja y
          el servicio de push no la va a aceptar nunca más.
        * `failed` suma un fallo; al llegar a `max_failures` seguidos, también
          se borra. Un 403 permanente —claves VAPID cambiadas— cae acá.
        """
        counts = {"sent": 0, "failed": 0, "gone": 0, "removed": 0}
        if not outcomes:
            return counts

        # Una sentencia por combinación de (estado, código HTTP), no una por
        # envío: un sismo sentido en toda la región son miles de filas, y casi
        # todas comparten el mismo 201.
        groups: dict[tuple[str, int | None], list[int]] = defaultdict(list)
        for outcome in outcomes:
            groups[(outcome.status, outcome.http_status)].append(outcome.delivery_id)
            counts[outcome.status] += 1
        for (status, http_status), delivery_ids in groups.items():
            await self.session.execute(
                update(PushDelivery)
                .where(PushDelivery.id.in_(delivery_ids))
                .values(
                    status=status,
                    http_status=http_status,
                    sent_at=now if status == "sent" else None,
                )
            )

        sent_ids = sorted({o.subscription_id for o in outcomes if o.status == "sent"})
        if sent_ids:
            await self.session.execute(
                update(PushSubscription)
                .where(PushSubscription.id.in_(sent_ids))
                .values(last_success_at=now, consecutive_failures=0)
            )

        failed_ids = sorted({o.subscription_id for o in outcomes if o.status == "failed"})
        if failed_ids:
            await self.session.execute(
                update(PushSubscription)
                .where(PushSubscription.id.in_(failed_ids))
                .values(consecutive_failures=PushSubscription.consecutive_failures + 1)
            )

        doomed = delete(PushSubscription).where(
            or_(
                PushSubscription.id.in_(
                    sorted({o.subscription_id for o in outcomes if o.status == "gone"})
                ),
                and_(
                    PushSubscription.id.in_(failed_ids),
                    PushSubscription.consecutive_failures >= max_failures,
                ),
            )
        )
        removed = await self.session.execute(doomed.returning(PushSubscription.id))
        counts["removed"] = len(removed.all())
        return counts

    async def prune_deliveries(self, *, before: datetime) -> int:
        result = await self.session.execute(
            delete(PushDelivery).where(PushDelivery.created_at < before).returning(PushDelivery.id)
        )
        return len(result.all())
