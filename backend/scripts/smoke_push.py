"""Smoke test de los avisos push contra una base PostGIS real.

    python scripts/smoke_push.py

Complementa a `tests/test_push_*.py`, que simulan la base: acá corre el SQL de
verdad —el filtro por radio en `geography`, el anti-join de «ya avisado», la
reserva con `ON CONFLICT`, el borrado de suscripciones muertas—. El envío sí se
simula: no sale nada hacia Google ni Apple.

Deja la base como la encontró: todo lo que crea lleva la marca `smoke-push` y se
borra al final, pase lo que pase.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, text

from app.core.database import AsyncSessionLocal, dispose_engine
from app.models.enums import EventSource, EventType, IncidentStatus, IncidentType
from app.models.event import RawEvent
from app.models.incident import Incident
from app.models.push import PushDelivery, PushSubscription
from app.models.seismic import SeismicDetail
from app.repositories.incident_repository import IncidentRepository
from app.schemas.push import PushSubscribeRequest
from app.services.push.notifier import PushNotifier
from app.services.push.subscriptions import PushSubscriptionService
from app.services.push.webpush import PushResult, PushSubscriptionKeys

MARK = "smoke-push"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/" + MARK
# Claves válidas de navegador (las del ejemplo del RFC 8291).
P256DH = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"

# Plaza de Viña del Mar y alrededores.
VINA = (-33.0245, -71.5518)
A_1200_M = (-33.0353, -71.5518)  # ~1,2 km al sur
QUILPUE = (-33.0470, -71.4420)  # ~10 km al este
RANCAGUA = (-34.1701, -70.7444)  # ~150 km

ok = 0
fail = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  [FALLA] {label}" + (f" — {detail}" if detail else ""))


class RecordingSender:
    """Registra los envíos. `gone` simula una suscripción desinstalada."""

    def __init__(self, gone: set[str] | None = None) -> None:
        self.gone = gone or set()
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(
        self, subscription: PushSubscriptionKeys, payload: dict[str, Any], **_: Any
    ) -> PushResult:
        self.sent.append((subscription.endpoint, payload))
        if subscription.endpoint in self.gone:
            return PushResult(status_code=410, ok=False, gone=True)
        return PushResult(status_code=201, ok=True)


def _request(suffix: str, lat: float, lon: float, **extra: Any) -> PushSubscribeRequest:
    return PushSubscribeRequest.model_validate(
        {
            "subscription": {
                "endpoint": f"{ENDPOINT}/{suffix}",
                "keys": {"p256dh": P256DH, "auth": AUTH},
            },
            "lat": lat,
            "lon": lon,
            "accuracy_m": 25.0,
            **extra,
        }
    )


async def _incident(
    session: Any,
    *,
    at: tuple[float, float],
    type_: IncidentType,
    confidence: float,
    sources: list[str],
    official: bool = False,
    age_minutes: int = 10,
    status: IncidentStatus = IncidentStatus.ACTIVE,
) -> Incident:
    now = datetime.now(UTC)
    incident = await IncidentRepository(session).create_incident(
        lat=at[0],
        lon=at[1],
        type=type_,
        status=status,
        first_seen_at=now - timedelta(minutes=age_minutes),
        last_seen_at=now - timedelta(minutes=1),
        confidence=confidence,
        source_count=len(sources),
        sources=sources,
        is_official_confirmed=official,
        commune="Viña del Mar",
        title=MARK,
    )
    return incident


async def _quake(
    session: Any, *, provider: str, qid: str, at: tuple[float, float], magnitude: float, age_s: int
) -> None:
    source = EventSource.CSN if provider == "csn" else EventSource.USGS
    event = RawEvent(
        timestamp=datetime.now(UTC) - timedelta(seconds=age_s),
        source=source,
        type=EventType.EARTHQUAKE,
        lat=at[0],
        lon=at[1],
        external_id=f"{provider}:{MARK}-{qid}",
        confidence=1.0,
        text=MARK,
        raw_data={},
    )
    session.add(event)
    await session.flush()
    session.add(
        SeismicDetail(
            raw_event_id=event.id,
            provider=provider,
            usgs_id=f"{MARK}-{qid}",
            magnitude=magnitude,
            depth_km=35.0,
            place="30 km al O de Valparaíso" if provider == "csn" else "30 km W of Valparaiso",
            review_status="reviewed",
        )
    )
    await session.flush()


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(PushSubscription).where(PushSubscription.endpoint.like(f"{ENDPOINT}%"))
        )
        await session.execute(delete(Incident).where(Incident.title == MARK))
        await session.execute(delete(RawEvent).where(RawEvent.text == MARK))
        await session.commit()


async def main() -> int:
    await _cleanup()
    try:
        await _run()
    finally:
        await _cleanup()
        await dispose_engine()
    print(f"\n{ok} OK, {fail} fallas")
    return 1 if fail else 0


async def _run() -> None:
    print("\n1. Suscripciones")
    async with AsyncSessionLocal() as session:
        service = PushSubscriptionService(session)
        near = await service.subscribe(_request("cerca", *VINA))
        check("la ubicación se guarda redondeada", (near.lat, near.lon) == (-33.025, -71.552))
        again = await service.subscribe(_request("cerca", *A_1200_M, notify_seismic=False))
        check("reenviar la suscripción no duplica", again.id == near.id)
        check("y actualiza la ubicación", again.lat == -33.035, str(again.lat))
        await service.subscribe(_request("cerca", *VINA))  # vuelve a la plaza

        await service.subscribe(_request("quilpue", *QUILPUE))
        await service.subscribe(_request("rancagua", *RANCAGUA))
        await service.subscribe(_request("muerta", *VINA))
        await service.subscribe(_request("solo-sismos", *VINA, notify_incidents=False))

        total = (
            await session.execute(
                select(func.count())
                .select_from(PushSubscription)
                .where(PushSubscription.endpoint.like(f"{ENDPOINT}%"))
            )
        ).scalar_one()
        check("cinco suscripciones", total == 5, str(total))

    print("\n2. Incidentes")
    async with AsyncSessionLocal() as session:
        fire = await _incident(
            session,
            at=A_1200_M,
            type_=IncidentType.WILDFIRE,
            confidence=0.45,
            sources=["nasa_firms", "media"],
        )
        lone = await _incident(
            session,
            at=A_1200_M,
            type_=IncidentType.POSSIBLE_FIRE,
            confidence=0.40,
            sources=["nasa_firms"],
        )
        old = await _incident(
            session,
            at=A_1200_M,
            type_=IncidentType.ACCIDENT,
            confidence=1.0,
            sources=["bomberos"],
            official=True,
            age_minutes=600,
        )
        absorbed = await _incident(
            session,
            at=VINA,
            type_=IncidentType.STRUCTURAL_FIRE,
            confidence=1.0,
            sources=["bomberos"],
            official=True,
            age_minutes=30,
        )
        await session.commit()
        codes = {"fire": fire.code, "lone": lone.code, "old": old.code, "absorbed": absorbed.code}

    sender = RecordingSender(gone={f"{ENDPOINT}/muerta"})
    async with AsyncSessionLocal() as session:
        first = await PushNotifier(session, sender).run()  # type: ignore[arg-type]

    by_endpoint: dict[str, list[str]] = {}
    for endpoint, payload in sender.sent:
        by_endpoint.setdefault(endpoint.rsplit("/", 1)[1], []).append(payload["title"])

    fire_titles = [t for t in by_endpoint.get("cerca", []) if t.startswith("Incendio forestal")]
    # La suscripción quedó redondeada a (-33.025, -71.552): el incendio está a
    # ~1,15 km, y el título lo dice con un decimal.
    check(
        "el incendio corroborado llega a quien está a ~1 km, con su distancia",
        fire_titles == ["Incendio forestal a 1,1 km"],
        str(by_endpoint.get("cerca")),
    )
    check(
        "el confirmado por Bomberos llega aunque venga de una sola fuente",
        any(t.startswith("Incendio estructural") for t in by_endpoint.get("cerca", [])),
    )
    check(
        "no llega a Quilpué (10 km, fuera del radio)",
        not any(t.startswith("Incendio forestal") for t in by_endpoint.get("quilpue", [])),
        str(by_endpoint.get("quilpue")),
    )
    check(
        "no llega a quien apagó las emergencias",
        "solo-sismos" not in by_endpoint,
        str(by_endpoint.get("solo-sismos")),
    )
    check(
        "la señal aislada no avisa",
        not any("Posible incendio" in t for titles in by_endpoint.values() for t in titles),
    )
    check(
        "el incidente de hace 10 horas no avisa",
        not any("Accidente" in t for titles in by_endpoint.values() for t in titles),
    )
    check("la suscripción muerta recibió el intento", "muerta" in by_endpoint)
    check("hubo envíos", first.sent >= 2, str(first.as_dict()))

    async with AsyncSessionLocal() as session:
        dead = (
            await session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == f"{ENDPOINT}/muerta")
            )
        ).scalar_one_or_none()
        check("la suscripción que respondió 410 se borró", dead is None)
        statuses = (
            await session.execute(
                select(PushDelivery.status, func.count())
                .join(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
                .where(PushSubscription.endpoint.like(f"{ENDPOINT}%"))
                .group_by(PushDelivery.status)
            )
        ).all()
        check(
            "los envíos quedan registrados como enviados",
            dict(statuses).get("sent", 0) >= 2,
            str(dict(statuses)),
        )
        near = (
            await session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == f"{ENDPOINT}/cerca")
            )
        ).scalar_one()
        check("se registra el último éxito", near.last_success_at is not None)

    print("\n3. Idempotencia y fusiones")
    sender_2 = RecordingSender()
    async with AsyncSessionLocal() as session:
        await PushNotifier(session, sender_2).run()  # type: ignore[arg-type]
    check("una segunda pasada no repite nada", sender_2.sent == [], str(sender_2.sent))

    async with AsyncSessionLocal() as session:
        survivor = await _incident(
            session,
            at=VINA,
            type_=IncidentType.STRUCTURAL_FIRE,
            confidence=1.0,
            sources=["bomberos", "media"],
            official=True,
            age_minutes=5,
        )
        await session.execute(
            text(
                "UPDATE alertav.incidents SET status = 'merged', merged_into_id = :keep "
                "WHERE code = :drop"
            ),
            {"keep": survivor.id, "drop": codes["absorbed"]},
        )
        await session.commit()

    sender_3 = RecordingSender()
    async with AsyncSessionLocal() as session:
        await PushNotifier(session, sender_3).run()  # type: ignore[arg-type]
    check(
        "quien supo del incidente absorbido no se entera de nuevo por el que lo absorbió",
        not any(e.endswith("/cerca") for e, _ in sender_3.sent),
        str([e for e, _ in sender_3.sent]),
    )

    print("\n4. Sismos")
    async with AsyncSessionLocal() as session:
        # El mismo sismo por las dos redes, 20 s de diferencia.
        await _quake(
            session, provider="usgs", qid="a", at=(-33.05, -71.95), magnitude=4.9, age_s=400
        )
        await _quake(
            session, provider="csn", qid="a", at=(-33.02, -71.90), magnitude=4.7, age_s=420
        )
        # Un microsismo que nadie sintió.
        await _quake(session, provider="csn", qid="b", at=(-32.5, -71.5), magnitude=2.4, age_s=300)
        await session.commit()

    sender_4 = RecordingSender()
    async with AsyncSessionLocal() as session:
        result = await PushNotifier(session, sender_4).run()  # type: ignore[arg-type]
    seismic = [(e.rsplit("/", 1)[1], p) for e, p in sender_4.sent if p["kind"] == "seismic"]
    receivers = sorted(name for name, _ in seismic)
    check(
        "un M4,7 frente a Valparaíso llega a Viña y Quilpué, no a Rancagua",
        receivers == ["cerca", "quilpue", "solo-sismos"],
        str(receivers),
    )
    check("se agrupó en un solo sismo", result.quakes_notifiable == 1, str(result.as_dict()))
    if seismic:
        payload = seismic[0][1]
        check(
            "se avisa con la versión del CSN",
            payload["body"].endswith("Fuente: CSN"),
            payload["body"],
        )
        check(
            "el título lleva magnitud y distancia",
            payload["title"].startswith("Sismo de magnitud 4,7 a "),
        )

    sender_5 = RecordingSender()
    async with AsyncSessionLocal() as session:
        await PushNotifier(session, sender_5).run()  # type: ignore[arg-type]
    check("el sismo no se repite", sender_5.sent == [], str(sender_5.sent))

    print("\n5. Baja")
    async with AsyncSessionLocal() as session:
        service = PushSubscriptionService(session)
        removed = await service.unsubscribe(f"{ENDPOINT}/quilpue")
        check("la baja borra la suscripción", removed)
        orphans = (
            await session.execute(
                select(func.count())
                .select_from(PushDelivery)
                .outerjoin(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
                .where(PushSubscription.id.is_(None))
            )
        ).scalar_one()
        check("y su registro de envíos, en cascada", orphans == 0, str(orphans))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
