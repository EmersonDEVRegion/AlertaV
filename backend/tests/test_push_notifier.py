"""Orquestación del notificador, con la base y el servicio de push simulados.

El SQL de `PushRepository` —el anti-join de «ya avisado», el filtro por radio en
`geography`— necesita PostGIS y se verifica en `scripts/smoke_push.py`. Acá se
prueba lo que el notificador decide con lo que el repositorio le entrega: qué
se reserva, qué se envía, qué se registra y qué se borra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.models.enums import IncidentStatus, IncidentType
from app.repositories.push_repository import DeliveryOutcome, Recipient
from app.services.push.notifier import PushNotifier
from app.services.push.rules import QuakeView
from app.services.push.webpush import PushResult, PushSubscriptionKeys

NOW = datetime(2026, 9, 23, 17, 40, tzinfo=UTC)


def _incident(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": 1,
        "code": "INC-2026-00142",
        "type": IncidentType.WILDFIRE,
        "status": IncidentStatus.ACTIVE,
        "lat": -33.03,
        "lon": -71.55,
        "confidence": 0.45,
        "source_count": 2,
        "sources": ["nasa_firms", "media"],
        "is_official_confirmed": False,
        "alert_level": None,
        "commune": "Viña del Mar",
        "first_seen_at": NOW - timedelta(minutes=12),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _recipient(sub_id: int, distance_m: float = 1200.0) -> Recipient:
    return Recipient(
        subscription_id=sub_id,
        endpoint=f"https://fcm.googleapis.com/fcm/send/{sub_id}",
        p256dh="p",
        auth="a",
        distance_m=distance_m,
    )


@dataclass
class FakeRepo:
    incidents: list[SimpleNamespace] = field(default_factory=list)
    quakes: list[QuakeView] = field(default_factory=list)
    incident_recipients: dict[str, list[Recipient]] = field(default_factory=dict)
    quake_recipients: list[Recipient] = field(default_factory=list)
    already_reserved: set[tuple[int, str]] = field(default_factory=set)
    reserved: list[tuple[str, str, int]] = field(default_factory=list)
    outcomes: list[DeliveryOutcome] = field(default_factory=list)
    quake_queries: list[dict[str, Any]] = field(default_factory=list)

    async def recent_active_incidents(self, *, since: datetime) -> list[SimpleNamespace]:
        return [i for i in self.incidents if i.first_seen_at >= since]

    async def recent_quakes(self, *, since: datetime) -> list[QuakeView]:
        return [q for q in self.quakes if q.timestamp >= since]

    async def recipients_for_incident(self, *, code: str, **_: Any) -> list[Recipient]:
        return self.incident_recipients.get(code, [])

    async def recipients_for_quake(self, **kwargs: Any) -> list[Recipient]:
        self.quake_queries.append(kwargs)
        return self.quake_recipients

    async def reserve_deliveries(
        self, *, kind: str, subject_key: str, recipients: list[Recipient]
    ) -> dict[int, int]:
        out: dict[int, int] = {}
        for r in recipients:
            if (r.subscription_id, subject_key) in self.already_reserved:
                continue  # otro notificador llegó primero
            self.reserved.append((kind, subject_key, r.subscription_id))
            out[r.subscription_id] = 1000 + r.subscription_id
        return out

    async def record_outcomes(
        self, outcomes: list[DeliveryOutcome], *, now: datetime, max_failures: int
    ) -> dict[str, int]:
        self.outcomes.extend(outcomes)
        counts = {"sent": 0, "failed": 0, "gone": 0, "removed": 0}
        for o in outcomes:
            counts[o.status] += 1
        counts["removed"] = counts["gone"]
        return counts

    async def prune_deliveries(self, *, before: datetime) -> int:
        return 0


class FakeIncidents:
    def __init__(self, outages: dict[int, dict[str, Any]] | None = None) -> None:
        self.outages = outages or {}

    async def outage_details(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        return {i: self.outages[i] for i in ids if i in self.outages}


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:  # pragma: no cover
        pass


class FakeSender:
    def __init__(self, results: dict[str, PushResult] | None = None) -> None:
        self.results = results or {}
        self.sent: list[tuple[PushSubscriptionKeys, dict[str, Any], dict[str, Any]]] = []

    async def send(
        self, subscription: PushSubscriptionKeys, payload: dict[str, Any], **kwargs: Any
    ) -> PushResult:
        self.sent.append((subscription, payload, kwargs))
        return self.results.get(subscription.endpoint, PushResult(status_code=201, ok=True))


def _notifier(
    repo: FakeRepo, sender: FakeSender, incidents: FakeIncidents | None = None
) -> tuple[PushNotifier, FakeSession]:
    session = FakeSession()
    notifier = PushNotifier(
        session,  # type: ignore[arg-type]
        sender,  # type: ignore[arg-type]
        min_confidence=0.30,
        min_sources=2,
        seismic_min_magnitude=3.5,
        seismic_max_age_minutes=30,
        incident_max_age_minutes=180,
    )
    notifier.repo = repo  # type: ignore[assignment]
    notifier.incidents = incidents or FakeIncidents()  # type: ignore[assignment]
    return notifier, session


class TestIncidentes:
    async def test_avisa_a_cada_suscripcion_cercana_con_su_distancia(self) -> None:
        repo = FakeRepo(
            incidents=[_incident()],
            incident_recipients={"INC-2026-00142": [_recipient(1, 800), _recipient(2, 4100)]},
        )
        sender = FakeSender()
        notifier, _ = _notifier(repo, sender)

        result = await notifier.run(now=NOW)

        assert result.sent == 2
        titles = sorted(payload["title"] for _, payload, _ in sender.sent)
        assert titles == ["Incendio forestal a 4,1 km", "Incendio forestal a 800 m"]
        assert {kwargs["topic"] for _, _, kwargs in sender.sent} == {"INC-2026-00142"}
        assert all(o.status == "sent" for o in repo.outcomes)

    async def test_una_senal_aislada_no_avisa_a_nadie(self) -> None:
        repo = FakeRepo(
            incidents=[_incident(confidence=0.40, source_count=1)],
            incident_recipients={"INC-2026-00142": [_recipient(1)]},
        )
        sender = FakeSender()
        notifier, _ = _notifier(repo, sender)

        result = await notifier.run(now=NOW)

        assert result.incidents_considered == 1
        assert result.incidents_notifiable == 0
        assert sender.sent == []

    async def test_no_reenvia_lo_que_otro_notificador_ya_reservo(self) -> None:
        repo = FakeRepo(
            incidents=[_incident()],
            incident_recipients={"INC-2026-00142": [_recipient(1), _recipient(2)]},
            already_reserved={(1, "INC-2026-00142")},
        )
        sender = FakeSender()
        notifier, _ = _notifier(repo, sender)

        await notifier.run(now=NOW)

        assert [s.endpoint for s, _, _ in sender.sent] == ["https://fcm.googleapis.com/fcm/send/2"]

    async def test_la_reserva_se_confirma_antes_de_enviar(self) -> None:
        repo = FakeRepo(
            incidents=[_incident()],
            incident_recipients={"INC-2026-00142": [_recipient(1)]},
        )
        session_commits_at_send: list[int] = []

        class SpySender(FakeSender):
            async def send(self, *args: Any, **kwargs: Any) -> PushResult:
                session_commits_at_send.append(session.commits)
                return await super().send(*args, **kwargs)

        notifier, session = _notifier(repo, SpySender())
        await notifier.run(now=NOW)

        assert session_commits_at_send == [1]

    async def test_suscripcion_muerta_y_fallo_pasajero(self) -> None:
        repo = FakeRepo(
            incidents=[_incident()],
            incident_recipients={"INC-2026-00142": [_recipient(1), _recipient(2)]},
        )
        sender = FakeSender(
            {
                "https://fcm.googleapis.com/fcm/send/1": PushResult(
                    status_code=410, ok=False, gone=True
                ),
                "https://fcm.googleapis.com/fcm/send/2": PushResult(status_code=503, ok=False),
            }
        )
        notifier, _ = _notifier(repo, sender)

        result = await notifier.run(now=NOW)

        by_sub = {o.subscription_id: o for o in repo.outcomes}
        assert by_sub[1].status == "gone" and by_sub[1].http_status == 410
        assert by_sub[2].status == "failed" and by_sub[2].http_status == 503
        assert result.gone == 1 and result.failed == 1 and result.sent == 0

    async def test_un_corte_lleva_los_datos_de_la_distribuidora(self) -> None:
        outage = _incident(
            id=7,
            code="INC-2026-00300",
            type=IncidentType.POWER_OUTAGE,
            confidence=1.0,
            source_count=1,
            sources=["cge"],
            is_official_confirmed=True,
            commune="Quilpué",
        )
        repo = FakeRepo(
            incidents=[outage],
            incident_recipients={"INC-2026-00300": [_recipient(3, 600)]},
        )
        sender = FakeSender()
        notifier, _ = _notifier(
            repo,
            sender,
            FakeIncidents(
                {
                    7: {
                        "provider": "CGE",
                        "affected_clients": 320,
                        "estimated_restoration": "2026-09-23T22:00:00+00:00",
                    }
                }
            ),
        )

        await notifier.run(now=NOW)

        ((_, payload, _),) = sender.sent
        assert payload["title"] == "Corte de luz a 600 m"
        assert "CGE · 320 clientes sin luz" in payload["body"]
        assert "Reposición estimada: 19:00" in payload["body"]


def _q(key: str, provider: str, **overrides: Any) -> QuakeView:
    base: dict[str, Any] = {
        "key": key,
        "provider": provider,
        "timestamp": NOW - timedelta(minutes=6),
        "lat": -32.9,
        "lon": -71.7,
        "magnitude": 4.6,
        "depth_km": 40.0,
        "place": "18 km al NO de Quintero",
    }
    base.update(overrides)
    return QuakeView(**base)


class TestSismos:
    async def test_el_mismo_sismo_de_dos_redes_se_avisa_una_vez_con_la_del_csn(self) -> None:
        repo = FakeRepo(
            quakes=[_q("usgs:us1", "usgs", magnitude=4.7), _q("csn:9", "csn")],
            quake_recipients=[_recipient(1, 45_000)],
        )
        sender = FakeSender()
        notifier, _ = _notifier(repo, sender)

        result = await notifier.run(now=NOW)

        assert result.quakes_considered == 1
        assert len(sender.sent) == 1
        _, payload, kwargs = sender.sent[0]
        assert payload["title"] == "Sismo de magnitud 4,6 a 45 km"
        assert payload["body"].endswith("Fuente: CSN")
        assert kwargs["ttl_seconds"] == 1800
        # La consulta de destinatarios excluye a quien ya supo por CUALQUIERA
        # de las dos versiones.
        assert sorted(repo.quake_queries[0]["keys"]) == ["csn:9", "usgs:us1"]
        assert repo.reserved == [("seismic", "csn:9", 1)]

    async def test_el_radio_de_busqueda_es_el_de_percepcion(self) -> None:
        repo = FakeRepo(quakes=[_q("csn:9", "csn", magnitude=4.0, depth_km=15.0)])
        notifier, _ = _notifier(repo, FakeSender())

        await notifier.run(now=NOW)

        reach_m = repo.quake_queries[0]["reach_m"]
        assert 45_000 < reach_m < 60_000

    async def test_bajo_la_magnitud_minima_no_se_busca_a_nadie(self) -> None:
        repo = FakeRepo(quakes=[_q("csn:9", "csn", magnitude=3.4)])
        notifier, _ = _notifier(repo, FakeSender())

        result = await notifier.run(now=NOW)

        assert result.quakes_notifiable == 0
        assert repo.quake_queries == []

    async def test_la_magnitud_que_cuenta_es_la_del_csn(self) -> None:
        # El USGS dice 3,6 y el CSN 3,4: manda la red oficial.
        repo = FakeRepo(
            quakes=[_q("usgs:us1", "usgs", magnitude=3.6), _q("csn:9", "csn", magnitude=3.4)]
        )
        notifier, _ = _notifier(repo, FakeSender())

        result = await notifier.run(now=NOW)

        assert result.quakes_notifiable == 0

    async def test_un_sismo_viejo_ya_no_se_avisa(self) -> None:
        repo = FakeRepo(
            quakes=[_q("csn:9", "csn", timestamp=NOW - timedelta(minutes=45))],
            quake_recipients=[_recipient(1)],
        )
        sender = FakeSender()
        notifier, _ = _notifier(repo, sender)

        await notifier.run(now=NOW)

        assert sender.sent == []
