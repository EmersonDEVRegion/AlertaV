"""Contrato de `/push/*` y validación de la puerta de entrada.

Sin base de datos: el servicio se reemplaza por un doble, igual que en
`test_seismic_endpoint.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_push_service
from app.api.v1.endpoints import push as push_endpoint
from app.core.exceptions import ValidationError
from app.main import app
from app.schemas.push import PushProbeResult, PushSubscribeRequest
from app.services.push.config import PushConfig, load_push_config
from app.services.push.subscriptions import (
    PushSubscriptionService,
    endpoint_host_allowed,
    round_location,
)
from app.services.push.webpush import VapidKeys

# Claves de navegador válidas (las del ejemplo del RFC 8291).
P256DH = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123:APA91b"

KEYS = VapidKeys.generate()


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subscription": {
            "endpoint": ENDPOINT,
            "expirationTime": None,
            "keys": {"p256dh": P256DH, "auth": AUTH},
        },
        "lat": -33.024567,
        "lon": -71.551234,
        "accuracy_m": 18.4,
    }
    body.update(overrides)
    return body


class FakeService:
    def __init__(self) -> None:
        self.subscribed: list[PushSubscribeRequest] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, request: PushSubscribeRequest) -> SimpleNamespace:
        self.subscribed.append(request)
        lat, lon = round_location(request.lat, request.lon)
        return SimpleNamespace(
            public_id=uuid.uuid4(),
            lat=lat,
            lon=lon,
            radius_m=request.radius_m or 5000.0,
            notify_incidents=request.notify_incidents,
            notify_seismic=request.notify_seismic,
            location_updated_at=datetime(2026, 9, 23, tzinfo=UTC),
        )

    async def unsubscribe(self, endpoint: str) -> bool:
        self.unsubscribed.append(endpoint)
        return True

    async def probe(self, endpoint: str, *, sender: Any) -> PushProbeResult:
        return PushProbeResult(sent=True, detail="ok")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    service = FakeService()
    app.dependency_overrides[get_push_service] = lambda: service
    config = load_push_config(
        private_key=KEYS.private_b64url, subject="mailto:equipo@alertav.cl", switch=True
    )
    monkeypatch.setattr(push_endpoint, "get_push_config", lambda: config)
    monkeypatch.setattr(push_endpoint, "_get_probe_sender", lambda: None)
    push_endpoint.probe_limiter.reset()
    yield TestClient(app), service
    app.dependency_overrides.pop(get_push_service, None)


class TestEstado:
    def test_entrega_la_clave_publica_derivada(self, client: Any) -> None:
        http, _ = client
        data = http.get("/api/v1/push/status").json()
        assert data["enabled"] is True
        assert data["public_key"] == KEYS.public_b64url
        assert data["incident_radius_m"] == 5000
        assert data["incident_min_sources"] == 2

    def test_sin_clave_dice_por_que(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = load_push_config(private_key="", subject="", switch=True)
        monkeypatch.setattr(push_endpoint, "get_push_config", lambda: config)
        data = TestClient(app).get("/api/v1/push/status").json()
        assert data["enabled"] is False
        assert data["public_key"] is None
        assert "VAPID_PRIVATE_KEY" in data["reason"]


class TestSuscripcion:
    def test_registra_y_devuelve_la_ubicacion_redondeada(self, client: Any) -> None:
        http, service = client
        response = http.post("/api/v1/push/subscriptions", json=_body())
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["lat"] == -33.025 and data["lon"] == -71.551
        assert data["notify_incidents"] is True and data["notify_seismic"] is True
        assert service.subscribed[0].subscription.endpoint == ENDPOINT

    def test_rechaza_endpoints_sin_https(self, client: Any) -> None:
        http, _ = client
        body = _body()
        body["subscription"]["endpoint"] = "http://fcm.googleapis.com/x"
        assert http.post("/api/v1/push/subscriptions", json=body).status_code == 422

    def test_rechaza_coordenadas_imposibles(self, client: Any) -> None:
        http, _ = client
        assert http.post("/api/v1/push/subscriptions", json=_body(lat=-120)).status_code == 422

    def test_rechaza_un_radio_fuera_de_rango(self, client: Any) -> None:
        http, _ = client
        assert (
            http.post("/api/v1/push/subscriptions", json=_body(radius_m=50_000)).status_code == 422
        )

    def test_sin_claves_vapid_no_acepta_suscripciones(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http, _ = client
        monkeypatch.setattr(push_endpoint, "get_push_config", lambda: PushConfig(enabled=False))
        assert http.post("/api/v1/push/subscriptions", json=_body()).status_code == 503

    def test_con_los_avisos_pausados_si_acepta(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        http, _ = client
        paused = load_push_config(
            private_key=KEYS.private_b64url, subject="mailto:a@b.cl", switch=False
        )
        monkeypatch.setattr(push_endpoint, "get_push_config", lambda: paused)
        assert http.post("/api/v1/push/subscriptions", json=_body()).status_code == 200

    def test_baja(self, client: Any) -> None:
        http, service = client
        response = http.post("/api/v1/push/unsubscribe", json={"endpoint": ENDPOINT})
        assert response.status_code == 204
        assert service.unsubscribed == [ENDPOINT]


class TestPrueba:
    def test_limitada_por_ip(self, client: Any) -> None:
        http, _ = client
        first = http.post("/api/v1/push/test", json={"endpoint": ENDPOINT})
        second = http.post("/api/v1/push/test", json={"endpoint": ENDPOINT})
        assert first.status_code == 200 and first.json()["sent"] is True
        assert second.status_code == 429
        assert "Retry-After" in second.headers


class TestPuertaDeEntrada:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://fcm.googleapis.com/fcm/send/x",
            "https://updates.push.services.mozilla.com/wpush/v2/x",
            "https://web.push.apple.com/QGx",
            "https://wns2-par02p.notify.windows.com/w/?token=x",
        ],
    )
    def test_servicios_conocidos(self, endpoint: str) -> None:
        from app.core.config import settings

        assert endpoint_host_allowed(endpoint, settings.PUSH_ALLOWED_ENDPOINT_HOSTS)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://169.254.169.254/latest/meta-data",
            "https://localhost:8000/api",
            "https://evilpush.apple.com.example.org/x",
            "https://notfcm.googleapis.com.attacker.net/x",
            "https://xfcm.googleapis.com/x",
        ],
    )
    def test_cualquier_otro_host_se_rechaza(self, endpoint: str) -> None:
        from app.core.config import settings

        assert not endpoint_host_allowed(endpoint, settings.PUSH_ALLOWED_ENDPOINT_HOSTS)

    async def test_el_servicio_rechaza_un_host_desconocido(self) -> None:
        service = PushSubscriptionService.__new__(PushSubscriptionService)
        request = PushSubscribeRequest.model_validate(
            _body(
                subscription={
                    "endpoint": "https://intranet.example/x",
                    "keys": {"p256dh": P256DH, "auth": AUTH},
                }
            )
        )
        with pytest.raises(ValidationError, match="no reconoce"):
            await service.subscribe(request)

    async def test_el_servicio_rechaza_claves_rotas(self) -> None:
        service = PushSubscriptionService.__new__(PushSubscriptionService)
        request = PushSubscribeRequest.model_validate(
            _body(
                subscription={
                    "endpoint": ENDPOINT,
                    "keys": {"p256dh": "B" + "A" * 86, "auth": AUTH},
                }
            )
        )
        with pytest.raises(ValidationError, match="Suscripción inválida"):
            await service.subscribe(request)

    def test_redondeo_de_la_ubicacion(self) -> None:
        assert round_location(-33.0245678, -71.5519999) == (-33.025, -71.552)


class TestConfiguracion:
    def test_clave_mal_pegada_apaga_el_push_sin_romper_nada(self) -> None:
        config = load_push_config(private_key="[abc]", subject="mailto:a@b.cl", switch=True)
        assert config.enabled is False and "inválida" in (config.reason or "")

    def test_contacto_invalido(self) -> None:
        config = load_push_config(
            private_key=KEYS.private_b64url, subject="alertav@gmail.com", switch=True
        )
        assert config.enabled is False and "VAPID_SUBJECT" in (config.reason or "")

    def test_pausado_conserva_la_clave_publica(self) -> None:
        config = load_push_config(
            private_key=KEYS.private_b64url, subject="mailto:a@b.cl", switch=False
        )
        assert config.enabled is False
        assert config.public_key == KEYS.public_b64url
