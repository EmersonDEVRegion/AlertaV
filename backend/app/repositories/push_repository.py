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
from sqlalchemy import (
    DateTime,
    Float,
    String,
    and_,
    cast,
    delete,
    exists,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collectors.water.esval_parser import motivo_legible
from app.core.config import settings
from app.models.enums import EventSource, EventType, IncidentStatus
from app.models.event import RawEvent
from app.models.incident import Incident
from app.models.push import PushDelivery, PushPlace, PushSubscription
from app.models.seismic import SeismicDetail
from app.services.push.radios import por_defecto
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
    #: Lugar guardado desde el que se midió (`"Casa"`), o `None` si fue desde la
    #: última ubicación del teléfono.
    place: str | None = None
    #: Cuándo se obtuvo esa última ubicación. Sólo importa si `place` es `None`.
    located_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Resultado de un envío, listo para registrarse."""

    delivery_id: int
    subscription_id: int
    status: str  # sent | failed | gone
    http_status: int | None


@dataclass(frozen=True, slots=True)
class WaterCutView:
    """Lo que el aviso de un corte de agua necesita, fuera del ORM."""

    key: str
    public_id: str
    lat: float
    lon: float
    comuna: str | None
    calles: str | None
    sector: str | None
    inicio: str | None
    fin: str | None
    programado: bool | None
    motivo: str | None

    @classmethod
    def de(cls, fila: RawEvent) -> WaterCutView | None:
        raw = fila.raw_data if isinstance(fila.raw_data, dict) else {}
        esval = raw.get("_esval")
        if not isinstance(esval, dict) or fila.lat is None or fila.lon is None:
            return None

        def texto(valor: Any) -> str | None:
            return str(valor).strip() or None if valor is not None else None

        bloque_visor = esval.get("visor")
        visor: dict[str, Any] = bloque_visor if isinstance(bloque_visor, dict) else {}
        programado = esval.get("programado")
        return cls(
            key=fila.external_id or str(fila.public_id),
            public_id=str(fila.public_id),
            lat=float(fila.lat),
            lon=float(fila.lon),
            comuna=texto(esval.get("comuna")) or fila.commune,
            calles=texto(esval.get("calles")) or texto(visor.get("donde")),
            sector=texto(esval.get("sector")),
            inicio=texto(esval.get("inicio")),
            fin=texto(esval.get("fin")),
            programado=programado if isinstance(programado, bool) else None,
            motivo=motivo_legible(texto(esval.get("motivo"))),
        )


def radio_de_categoria_sql(radios: Any, categoria: str) -> Any:
    """`COALESCE((radios ->> categoria)::float, <por defecto>)` para una suscripción."""
    defecto = por_defecto().get(categoria, settings.PUSH_INCIDENT_RADIUS_M)
    return func.coalesce(
        cast(radios[categoria].astext, Float),
        literal(defecto, Float),
    )


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
        located_at: datetime | None = None,
        radios: dict[str, float] | None = None,
    ) -> PushSubscription:
        """Crea la suscripción o la actualiza si el navegador ya la tenía.

        La PWA la reenvía cada vez que se abre con una ubicación nueva, así que
        éste es el camino más transitado del módulo y tiene que ser una sola
        sentencia. Las claves se sobrescriben siempre: un navegador que renovó su
        suscripción puede conservar el endpoint y cambiar las claves, y
        quedarse con las viejas sería cifrar para nadie.

        Reiniciar `consecutive_failures` también es deliberado: que el navegador
        vuelva a registrarse es la mejor prueba de que el canal está vivo.

        `located_at` es cuándo el teléfono obtuvo la ubicación, que puede ser
        anterior al registro (la PWA reenvía la suscripción al cambiar un lugar
        guardado sin volver a pedir el GPS). Nunca se acepta una hora futura.
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
        # `None` = la PWA no los mandó (una versión anterior, o una
        # resincronización de ubicación): se conservan los que había.
        if radios is not None:
            values["radios"] = radios
        located = func.now() if located_at is None else func.least(located_at, func.now())
        insert = pg_insert(PushSubscription).values(**values, location_updated_at=located)
        stmt = insert.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={
                **{key: insert.excluded[key] for key in values if key != "endpoint"},
                "location_updated_at": insert.excluded.location_updated_at,
                "consecutive_failures": 0,
            },
        ).returning(PushSubscription)
        result = await self.session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalar_one()

    async def replace_places(
        self, subscription_id: int, places: Sequence[tuple[str, float, float]]
    ) -> list[PushPlace]:
        """Reemplaza los lugares guardados de una suscripción por `places`.

        Reemplazar y no fusionar: el teléfono es la fuente de verdad (los
        lugares viven en su `localStorage`) y manda la lista completa.
        """
        await self.session.execute(
            delete(PushPlace).where(PushPlace.subscription_id == subscription_id)
        )
        rows = [
            PushPlace(subscription_id=subscription_id, name=name, lat=lat, lon=lon, position=i)
            for i, (name, lat, lon) in enumerate(places)
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def places_of(self, subscription_id: int) -> list[PushPlace]:
        stmt = (
            select(PushPlace)
            .where(PushPlace.subscription_id == subscription_id)
            .order_by(PushPlace.position.asc(), PushPlace.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

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
            # Nada que el mapa no muestre puede despertar a alguien.
            .where(Incident.publico.is_(True))
            .where(Incident.first_seen_at >= since)
            .order_by(Incident.first_seen_at.asc())
            .limit(500)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def recent_water_cuts(
        self, *, seen_since: datetime, first_seen_since: datetime, limit: int = 200
    ) -> list[WaterCutView]:
        """Cortes de agua vigentes con punto que AlertaV vio por primera vez hace poco.

        Vigente = la última lectura de Esval lo seguía listando (`_esval.visto_en`).
        «Por primera vez hace poco» se mide con `ingested_at`, no con el inicio
        del corte: uno programado para mañana se avisa cuando aparece, y uno que
        lleva tres días no se avisa después de un deploy.
        """
        visto_en = cast(RawEvent.raw_data["_esval"]["visto_en"].astext, DateTime(timezone=True))
        stmt = (
            select(RawEvent)
            .where(RawEvent.source == EventSource.ESVAL)
            .where(RawEvent.type == EventType.WATER_CUT)
            .where(RawEvent.lat.isnot(None), RawEvent.lon.isnot(None))
            .where(visto_en >= seen_since)
            .where(RawEvent.ingested_at >= first_seen_since)
            .order_by(RawEvent.ingested_at.asc())
            .limit(limit)
        )
        filas = (await self.session.execute(stmt)).scalars().all()
        return [vista for vista in (WaterCutView.de(fila) for fila in filas) if vista is not None]

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
        self,
        *,
        incident_id: int,
        code: str,
        lat: float,
        lon: float,
        categoria: str = "other",
        limit: int = 5000,
    ) -> list[Recipient]:
        """Suscripciones dentro de su propio radio que todavía no recibieron el aviso.

        «Su propio radio» es el de la categoría del incidente (§K): el que la
        persona eligió en `radios`, o el del servidor si nunca lo tocó. Con 0,
        esa categoría no se le avisa.

        «Dentro de su radio» se mide desde la última ubicación del teléfono Y
        desde cada uno de sus lugares guardados; manda el más cercano, y ése es
        el que nombra el aviso («a 1,2 km de Casa»). Un teléfono recibe un solo
        aviso por incidente aunque tenga Casa y Trabajo dentro del radio.

        «Todavía no recibieron» incluye a los incidentes absorbidos: si el
        INC-00280 se fusionó dentro del INC-00281, quien ya supo del 280 no
        tiene que enterarse de nuevo por el 281. Es el mismo incendio con otro
        folio.
        """
        merged = aliased(Incident)
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
        return await self._cercanos(
            lat=lat, lon=lon, categoria=categoria, ya_avisados=already, limit=limit
        )

    async def recipients_for_water_cut(
        self, *, subject_key: str, lat: float, lon: float, limit: int = 5000
    ) -> list[Recipient]:
        """Como `recipients_for_incident`, con el radio de cortes de agua."""
        already = exists().where(
            PushDelivery.subscription_id == PushSubscription.id,
            PushDelivery.kind == "water_cut",
            PushDelivery.subject_key == subject_key,
        )
        return await self._cercanos(
            lat=lat, lon=lon, categoria="water", ya_avisados=already, limit=limit
        )

    async def _cercanos(
        self, *, lat: float, lon: float, categoria: str, ya_avisados: Any, limit: int
    ) -> list[Recipient]:
        """Suscripciones con avisos de emergencias dentro del radio de `categoria`."""
        point_geog = func.cast(func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), _GEOGRAPHY)

        # Candidatos: la ubicación de cada suscripción y cada lugar guardado.
        # El prefiltro con radio constante es el que usa los índices GiST.
        sub_geog = func.cast(PushSubscription.geom, _GEOGRAPHY)
        own = select(
            PushSubscription.id.label("subscription_id"),
            cast(null(), String(40)).label("place"),
            func.ST_Distance(sub_geog, point_geog).label("distance_m"),
        ).where(
            PushSubscription.notify_incidents.is_(True),
            func.ST_DWithin(sub_geog, point_geog, MAX_SUBSCRIPTION_RADIUS_M),
        )
        place_geog = func.cast(PushPlace.geom, _GEOGRAPHY)
        saved = select(
            PushPlace.subscription_id.label("subscription_id"),
            PushPlace.name.label("place"),
            func.ST_Distance(place_geog, point_geog).label("distance_m"),
        ).where(func.ST_DWithin(place_geog, point_geog, MAX_SUBSCRIPTION_RADIUS_M))
        candidates = union_all(own, saved).subquery("candidates")

        # El más cercano de cada suscripción, dentro de SU radio para esta
        # categoría. A igual distancia gana el lugar guardado: «de Casa» dice
        # más que nada.
        within = aliased(PushSubscription)
        radio = radio_de_categoria_sql(within.radios, categoria)
        nearest = (
            select(candidates.c.subscription_id, candidates.c.place, candidates.c.distance_m)
            .join(within, within.id == candidates.c.subscription_id)
            .where(radio > 0)
            .where(candidates.c.distance_m <= radio)
            .distinct(candidates.c.subscription_id)
            .order_by(
                candidates.c.subscription_id,
                candidates.c.distance_m.asc(),
                candidates.c.place.is_(None),
            )
            .subquery("nearest")
        )

        stmt = (
            select(
                PushSubscription.id,
                PushSubscription.endpoint,
                PushSubscription.p256dh,
                PushSubscription.auth,
                PushSubscription.location_updated_at,
                nearest.c.place,
                nearest.c.distance_m,
            )
            .join(nearest, nearest.c.subscription_id == PushSubscription.id)
            .where(PushSubscription.notify_incidents.is_(True))
            .where(~ya_avisados)
            .order_by(nearest.c.distance_m.asc())
            .limit(limit)
        )
        return [
            Recipient(
                subscription_id=row.id,
                endpoint=row.endpoint,
                p256dh=row.p256dh,
                auth=row.auth,
                distance_m=float(row.distance_m),
                place=row.place,
                located_at=row.location_updated_at,
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
