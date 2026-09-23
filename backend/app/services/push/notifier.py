"""Notificador: convierte incidentes y sismos nuevos en avisos push.

Un tercer motor de fondo, al lado de la recolección y la correlación, con el
mismo patrón que ellos: una pasada idempotente cada `PUSH_POLL_INTERVAL_SECONDS`.

    python -m app.services.push.runner          # una pasada
    python -m app.services.push.runner --loop   # continuo

Por qué un motor aparte y no un gancho dentro de la correlación
----------------------------------------------------------------
Lo más directo habría sido enviar desde `CorrelationEngine._open_incident`. Se
descartó por dos razones:

* **La pasada de correlación es una transacción atómica.** Un envío hecho a la
  mitad no se puede revertir: si la pasada falla después y se deshace, el aviso
  ya salió de un incidente que no existe.
* **El umbral no se cruza al nacer.** Un incendio suele abrir con una señal en
  el tramo `unsafe` y cruzar a `possible` dos pasadas después, cuando llega la
  corroboración. Lo que dispara el aviso es un *estado*, no un evento, y un
  motor que mira el estado cada minuto lo encuentra venga de donde venga.

La idempotencia la da `push_deliveries`: cada pasada pregunta «a quién le
corresponde este aviso y todavía no lo recibió», así que correr de más no
duplica nada.

Una pasada
----------
1. **Incidentes.** Los activos que aparecieron en las últimas
   `PUSH_INCIDENT_MAX_AGE_MINUTES`, filtrados por `rules.incident_is_notifiable`.
   Para cada uno, las suscripciones a menos de su propio radio.
2. **Sismos.** Los de las dos redes en los últimos `PUSH_SEISMIC_MAX_AGE_MINUTES`,
   agrupados (una sola vez por sismo aunque lo publiquen el CSN y el USGS). Para
   cada uno con magnitud suficiente, las suscripciones dentro de su radio de
   percepción.
3. **Poda** del registro de envíos más viejo que `PUSH_DELIVERY_RETENTION_DAYS`.

Cada aviso se **reserva** (fila `pending`, commit) antes de enviarse. Si el
proceso muere entre la reserva y el envío, ese aviso se pierde en vez de
duplicarse. Para una alerta es la elección correcta: un aviso repetido enseña a
ignorar los avisos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import IncidentStatus, IncidentType
from app.models.incident import Incident
from app.repositories.incident_repository import IncidentRepository
from app.repositories.push_repository import (
    DeliveryOutcome,
    PushRepository,
    Recipient,
)
from app.services.push.messages import (
    OutageFacts,
    PushMessage,
    incident_message,
    seismic_message,
)
from app.services.push.rules import (
    QuakeView,
    group_quakes,
    incident_is_notifiable,
    perception_reach_km,
)
from app.services.push.webpush import PushSubscriptionKeys, WebPushSender

logger = logging.getLogger("alertav.push")

#: Envíos simultáneos. Los servicios de push responden en decenas de
#: milisegundos; con 0.1 vCPU lo que cuesta es cifrar, no esperar la red.
SEND_CONCURRENCY = 16


@dataclass(frozen=True, slots=True)
class IncidentSnapshot:
    """Lo que el notificador necesita de un incidente, fuera del ORM.

    El notificador confirma la transacción varias veces por pasada (una por
    reserva y una por resultado). Trabajar sobre copias planas evita depender de
    si el ORM refresca o no los objetos después de cada commit.
    """

    id: int
    code: str
    type: IncidentType
    status: IncidentStatus
    lat: float
    lon: float
    confidence: float
    source_count: int
    sources: tuple[str, ...]
    is_official_confirmed: bool
    alert_level: str | None
    commune: str | None
    first_seen_at: datetime

    @classmethod
    def of(cls, incident: Incident) -> IncidentSnapshot:
        return cls(
            id=incident.id,
            code=incident.code,
            type=incident.type,
            status=incident.status,
            lat=incident.lat,
            lon=incident.lon,
            confidence=float(incident.confidence),
            source_count=int(incident.source_count),
            sources=tuple(incident.sources or ()),
            is_official_confirmed=bool(incident.is_official_confirmed),
            alert_level=incident.alert_level,
            commune=incident.commune,
            first_seen_at=incident.first_seen_at,
        )


@dataclass(slots=True)
class NotifierPass:
    """Traza de una pasada, en el mismo espíritu que `CorrelationPass`."""

    started_at: datetime
    finished_at: datetime | None = None
    incidents_considered: int = 0
    incidents_notifiable: int = 0
    quakes_considered: int = 0
    quakes_notifiable: int = 0
    sent: int = 0
    failed: int = 0
    gone: int = 0
    subscriptions_removed: int = 0
    deliveries_pruned: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        duration = (self.finished_at - self.started_at).total_seconds() if self.finished_at else 0.0
        return {
            "started_at": self.started_at.isoformat(),
            "duration_seconds": round(duration, 3),
            "incidents_considered": self.incidents_considered,
            "incidents_notifiable": self.incidents_notifiable,
            "quakes_considered": self.quakes_considered,
            "quakes_notifiable": self.quakes_notifiable,
            "sent": self.sent,
            "failed": self.failed,
            "gone": self.gone,
            "subscriptions_removed": self.subscriptions_removed,
            "deliveries_pruned": self.deliveries_pruned,
            "warnings": list(self.warnings),
        }


def _parse_restoration(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class PushNotifier:
    """Una pasada del notificador sobre una sesión."""

    def __init__(
        self,
        session: AsyncSession,
        sender: WebPushSender,
        *,
        incident_max_age_minutes: int | None = None,
        min_confidence: float | None = None,
        min_sources: int | None = None,
        seismic_min_magnitude: float | None = None,
        seismic_max_age_minutes: int | None = None,
        seismic_max_reach_km: float | None = None,
        max_failures: int | None = None,
        retention_days: int | None = None,
    ) -> None:
        self.session = session
        self.sender = sender
        self.repo = PushRepository(session)
        self.incidents = IncidentRepository(session)
        self.incident_max_age = timedelta(
            minutes=incident_max_age_minutes or settings.PUSH_INCIDENT_MAX_AGE_MINUTES
        )
        self.min_confidence = (
            settings.PUSH_INCIDENT_MIN_CONFIDENCE if min_confidence is None else min_confidence
        )
        self.min_sources = min_sources or settings.PUSH_INCIDENT_MIN_SOURCES
        self.seismic_min_magnitude = (
            settings.PUSH_SEISMIC_MIN_MAGNITUDE
            if seismic_min_magnitude is None
            else seismic_min_magnitude
        )
        self.seismic_max_age = timedelta(
            minutes=seismic_max_age_minutes or settings.PUSH_SEISMIC_MAX_AGE_MINUTES
        )
        self.seismic_max_reach_km = seismic_max_reach_km or settings.PUSH_SEISMIC_MAX_REACH_KM
        self.max_failures = max_failures or settings.PUSH_MAX_CONSECUTIVE_FAILURES
        self.retention = timedelta(days=retention_days or settings.PUSH_DELIVERY_RETENTION_DAYS)

    async def run(self, *, now: datetime | None = None) -> NotifierPass:
        now = now or datetime.now(UTC)
        result = NotifierPass(started_at=now)
        try:
            await self._incidents(result, now=now)
            await self._quakes(result, now=now)
            result.deliveries_pruned = await self.repo.prune_deliveries(before=now - self.retention)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception("la pasada del notificador falló")
            raise
        result.finished_at = datetime.now(UTC)
        if result.sent or result.failed or result.gone or result.warnings:
            logger.info("pasada del notificador", extra=result.as_dict())
        else:
            logger.debug("pasada del notificador sin envíos", extra=result.as_dict())
        return result

    # -- Incidentes -------------------------------------------------------------

    async def _incidents(self, result: NotifierPass, *, now: datetime) -> None:
        loaded = await self.repo.recent_active_incidents(since=now - self.incident_max_age)
        snapshots = [IncidentSnapshot.of(incident) for incident in loaded]
        result.incidents_considered = len(snapshots)

        notifiable = [
            snap
            for snap in snapshots
            if incident_is_notifiable(
                status=snap.status,
                incident_type=snap.type,
                confidence=snap.confidence,
                source_count=snap.source_count,
                is_official_confirmed=snap.is_official_confirmed,
                min_confidence=self.min_confidence,
                min_sources=self.min_sources,
            )
        ]
        result.incidents_notifiable = len(notifiable)
        if not notifiable:
            return

        outage_ids = [snap.id for snap in notifiable if snap.type is IncidentType.POWER_OUTAGE]
        outages = await self.incidents.outage_details(outage_ids) if outage_ids else {}

        for snap in notifiable:
            recipients = await self.repo.recipients_for_incident(
                incident_id=snap.id, code=snap.code, lat=snap.lat, lon=snap.lon
            )
            if not recipients:
                continue

            outage_payload = outages.get(snap.id)
            outage = (
                OutageFacts(
                    provider=outage_payload.get("provider"),
                    affected_clients=outage_payload.get("affected_clients"),
                    estimated_restoration=_parse_restoration(
                        outage_payload.get("estimated_restoration")
                    ),
                )
                if outage_payload
                else None
            )

            def compose(
                recipient: Recipient,
                snap: IncidentSnapshot = snap,
                outage: OutageFacts | None = outage,
            ) -> PushMessage:
                return incident_message(
                    code=snap.code,
                    incident_type=snap.type,
                    distance_m=recipient.distance_m,
                    commune=snap.commune,
                    first_seen_at=snap.first_seen_at,
                    confidence=snap.confidence,
                    source_count=snap.source_count,
                    sources=snap.sources,
                    is_official_confirmed=snap.is_official_confirmed,
                    alert_level=snap.alert_level,
                    outage=outage,
                )

            await self._deliver(
                kind="incident",
                subject_key=snap.code,
                recipients=recipients,
                compose=compose,
                now=now,
                result=result,
            )

    # -- Sismos -----------------------------------------------------------------

    async def _quakes(self, result: NotifierPass, *, now: datetime) -> None:
        quakes = await self.repo.recent_quakes(since=now - self.seismic_max_age)
        groups = group_quakes(quakes)
        result.quakes_considered = len(groups)

        for group in groups:
            quake = group.representative
            if quake.magnitude is None or quake.magnitude < self.seismic_min_magnitude:
                continue
            reach_km = perception_reach_km(
                quake.magnitude, quake.depth_km, max_reach_km=self.seismic_max_reach_km
            )
            if reach_km is None:
                continue
            result.quakes_notifiable += 1

            recipients = await self.repo.recipients_for_quake(
                lat=quake.lat, lon=quake.lon, reach_m=reach_km * 1000, keys=group.keys
            )
            if not recipients:
                continue

            magnitude: float = quake.magnitude

            def compose(
                recipient: Recipient, quake: QuakeView = quake, magnitude: float = magnitude
            ) -> PushMessage:
                return seismic_message(
                    key=quake.key,
                    provider=quake.provider,
                    magnitude=magnitude,
                    distance_m=recipient.distance_m,
                    timestamp=quake.timestamp,
                    lat=quake.lat,
                    lon=quake.lon,
                    place=quake.place,
                    depth_km=quake.depth_km,
                )

            await self._deliver(
                kind="seismic",
                subject_key=quake.key,
                recipients=recipients,
                compose=compose,
                now=now,
                result=result,
            )

    # -- Envío ------------------------------------------------------------------

    async def _deliver(
        self,
        *,
        kind: str,
        subject_key: str,
        recipients: Sequence[Recipient],
        compose: Callable[[Recipient], PushMessage],
        now: datetime,
        result: NotifierPass,
    ) -> None:
        reserved = await self.repo.reserve_deliveries(
            kind=kind, subject_key=subject_key, recipients=recipients
        )
        # La reserva se confirma ANTES de enviar. Ver el docstring del módulo.
        await self.session.commit()
        targets = [r for r in recipients if r.subscription_id in reserved]
        if not targets:
            return

        semaphore = asyncio.Semaphore(SEND_CONCURRENCY)

        async def one(recipient: Recipient) -> DeliveryOutcome:
            message: PushMessage = compose(recipient)
            async with semaphore:
                sent = await self.sender.send(
                    PushSubscriptionKeys(
                        endpoint=recipient.endpoint,
                        p256dh=recipient.p256dh,
                        auth=recipient.auth,
                    ),
                    message.payload(now=now),
                    ttl_seconds=message.ttl_seconds,
                    topic=message.topic,
                )
            status = "sent" if sent.ok else ("gone" if sent.gone else "failed")
            if status == "failed":
                logger.warning(
                    "envío push fallido",
                    extra={
                        "kind": kind,
                        "subject": subject_key,
                        "http_status": sent.status_code,
                        "error": sent.error,
                    },
                )
            return DeliveryOutcome(
                delivery_id=reserved[recipient.subscription_id],
                subscription_id=recipient.subscription_id,
                status=status,
                http_status=sent.status_code,
            )

        outcomes = await asyncio.gather(*(one(r) for r in targets))
        counts = await self.repo.record_outcomes(outcomes, now=now, max_failures=self.max_failures)
        await self.session.commit()

        result.sent += counts["sent"]
        result.failed += counts["failed"]
        result.gone += counts["gone"]
        result.subscriptions_removed += counts["removed"]
        logger.info(
            "avisos enviados",
            extra={
                "kind": kind,
                "subject": subject_key,
                "destinatarios": len(targets),
                **counts,
            },
        )


__all__ = ["IncidentSnapshot", "NotifierPass", "PushNotifier"]
