"""Lugares guardados: contrato, redondeo, consulta de destinatarios y mensaje.

La consulta se compila contra el dialecto de Postgres sin conectarse: no prueba
el resultado (eso es `test_integracion_pg.py`) pero sí que la sentencia se arma
y que mide desde los dos orígenes —ubicación y lugares— con un solo aviso por
teléfono.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.dialects import postgresql

from app.models.enums import IncidentType
from app.repositories.push_repository import PushRepository, Recipient
from app.schemas.push import PushSubscribeRequest
from app.services.push.messages import format_location_age, incident_message
from app.services.push.subscriptions import PushSubscriptionService

P256DH = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123:APA91b"
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subscription": {"endpoint": ENDPOINT, "keys": {"p256dh": P256DH, "auth": AUTH}},
        "lat": -33.0245,
        "lon": -71.5512,
    }
    body.update(overrides)
    return body


class TestContrato:
    def test_sin_places_no_se_tocan(self) -> None:
        assert PushSubscribeRequest.model_validate(_body()).places is None

    def test_lista_vacia_es_borrar_todos(self) -> None:
        assert PushSubscribeRequest.model_validate(_body(places=[])).places == []

    def test_hasta_tres(self) -> None:
        place = {"name": "Casa", "lat": -33.04, "lon": -71.4}
        with pytest.raises(PydanticValidationError):
            PushSubscribeRequest.model_validate(_body(places=[place] * 4))

    def test_el_nombre_se_limpia_y_no_puede_quedar_vacio(self) -> None:
        request = PushSubscribeRequest.model_validate(
            _body(places=[{"name": "  Casa   de  mamá ", "lat": -33.04, "lon": -71.4}])
        )
        assert request.places is not None and request.places[0].name == "Casa de mamá"
        with pytest.raises(PydanticValidationError):
            PushSubscribeRequest.model_validate(
                _body(places=[{"name": "   ", "lat": -33.04, "lon": -71.4}])
            )


class FakeRepo:
    def __init__(self) -> None:
        self.replaced: list[tuple[int, list[tuple[str, float, float]]]] = []
        self.upserts: list[dict[str, Any]] = []

    async def upsert_subscription(self, **kwargs: Any) -> SimpleNamespace:
        self.upserts.append(kwargs)
        return SimpleNamespace(id=7)

    async def replace_places(self, subscription_id: int, places: list[Any]) -> list[Any]:
        self.replaced.append((subscription_id, list(places)))
        return [SimpleNamespace(name=n, lat=la, lon=lo) for n, la, lo in places]

    async def places_of(self, subscription_id: int) -> list[Any]:
        return [SimpleNamespace(name="Trabajo", lat=-33.0, lon=-71.6)]


class FakeSession:
    async def commit(self) -> None:
        return None


def _service() -> tuple[PushSubscriptionService, FakeRepo]:
    service = PushSubscriptionService.__new__(PushSubscriptionService)
    repo = FakeRepo()
    service.session = FakeSession()  # type: ignore[assignment]
    service.repo = repo  # type: ignore[assignment]
    return service, repo


class TestServicio:
    async def test_los_lugares_se_guardan_redondeados_como_la_ubicacion(self) -> None:
        service, repo = _service()
        located = NOW - timedelta(days=2)
        request = PushSubscribeRequest.model_validate(
            _body(
                located_at=located.isoformat(),
                places=[{"name": "Casa", "lat": -33.0456789, "lon": -71.4012345}],
            )
        )
        _, places = await service.subscribe(request)
        assert repo.replaced == [(7, [("Casa", -33.046, -71.401)])]
        assert places[0].name == "Casa"
        assert repo.upserts[0]["located_at"] == located

    async def test_sin_places_devuelve_los_que_habia(self) -> None:
        service, repo = _service()
        _, places = await service.subscribe(PushSubscribeRequest.model_validate(_body()))
        assert repo.replaced == []
        assert [p.name for p in places] == ["Trabajo"]


class CapturingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        return SimpleNamespace(all=lambda: [])


class TestConsulta:
    async def test_mide_desde_la_ubicacion_y_desde_los_lugares_con_un_aviso_por_telefono(
        self,
    ) -> None:
        session = CapturingSession()
        repo = PushRepository(session)  # type: ignore[arg-type]
        assert await repo.recipients_for_incident(
            incident_id=1, code="INC-2026-00001", lat=-33.03, lon=-71.55
        ) == []
        sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
        assert "push_places" in sql and "push_subscriptions" in sql
        assert "UNION ALL" in sql
        assert "DISTINCT ON" in sql
        assert sql.count("ST_DWithin(") == 2


class TestMensaje:
    def _msg(self, **overrides: Any) -> Any:
        base: dict[str, Any] = {
            "code": "INC-2026-00142",
            "incident_type": IncidentType.WILDFIRE,
            "distance_m": 1234.0,
            "commune": "Quilpué",
            "first_seen_at": NOW - timedelta(minutes=10),
            "confidence": 0.45,
            "source_count": 2,
            "sources": ["nasa_firms", "media"],
            "is_official_confirmed": False,
            "alert_level": None,
            "now": NOW,
        }
        base.update(overrides)
        return incident_message(**base)

    def test_desde_un_lugar_guardado_lo_nombra(self) -> None:
        message = self._msg(place="Casa", located_at=NOW - timedelta(days=9))
        assert message.title == "Incendio forestal a 1,2 km de Casa"
        # La edad de la ubicación no importa si se midió desde Casa.
        assert "ubicación" not in message.body

    def test_ubicacion_vieja_se_dice(self) -> None:
        message = self._msg(located_at=NOW - timedelta(days=3, hours=2))
        assert message.title == "Incendio forestal a 1,2 km"
        assert message.body.endswith("Distancia desde tu ubicación de hace 3 días")

    def test_ubicacion_de_hoy_no_agrega_nada(self) -> None:
        message = self._msg(located_at=NOW - timedelta(hours=5))
        assert "ubicación" not in message.body

    @pytest.mark.parametrize(
        ("age", "text"),
        [
            (timedelta(hours=23), None),
            (timedelta(hours=25), "de hace 1 día"),
            (timedelta(days=2, hours=1), "de hace 2 días"),
            (timedelta(days=45), "de hace más de un mes"),
        ],
    )
    def test_edad(self, age: timedelta, text: str | None) -> None:
        assert format_location_age(NOW - age, NOW) == text


def test_recipient_sin_lugar_por_defecto() -> None:
    r = Recipient(subscription_id=1, endpoint="https://x", p256dh="p", auth="a", distance_m=1.0)
    assert r.place is None and r.located_at is None
