"""Redacción de los avisos: lo que se lee en la pantalla bloqueada."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import IncidentType
from app.services.push.messages import (
    OutageFacts,
    format_distance,
    incident_message,
    probe_message,
    safe_topic,
    seismic_message,
)
from app.services.push.webpush import MAX_PLAINTEXT_BYTES

# 17:32 UTC = 14:32 en Chile (UTC-3 en septiembre).
T0 = datetime(2026, 9, 23, 17, 32, tzinfo=UTC)


def _incident(**overrides: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "code": "INC-2026-00142",
        "incident_type": IncidentType.WILDFIRE,
        "distance_m": 1234.0,
        "commune": "Viña del Mar",
        "first_seen_at": T0,
        "confidence": 0.45,
        "source_count": 2,
        "sources": ["nasa_firms", "media"],
        "is_official_confirmed": False,
        "alert_level": None,
    }
    base.update(overrides)
    return incident_message(**base)  # type: ignore[arg-type]


class TestDistancia:
    @pytest.mark.parametrize(
        ("meters", "text"),
        [
            (20, "50 m"),
            (347, "350 m"),
            (949, "950 m"),
            (974, "950 m"),
            (975, "1 km"),
            (5000, "5 km"),
            (1234, "1,2 km"),
            (9_940, "9,9 km"),
            (9_960, "10 km"),
            (48_400, "48 km"),
        ],
    )
    def test_formato_chileno(self, meters: float, text: str) -> None:
        assert format_distance(meters) == text


class TestIncidente:
    def test_el_titulo_dice_que_y_a_que_distancia(self) -> None:
        assert _incident().title == "🔥 Incendio forestal a 1,2 km de tu última ubicación"

    def test_el_cuerpo_es_una_linea(self) -> None:
        assert _incident().body == "Viña del Mar · desde 14:32 · Posible emergencia (2 fuentes)"

    def test_sin_porcentaje(self) -> None:
        assert "%" not in _incident(confidence=0.72, source_count=3).body

    def test_el_payload_dice_el_origen(self) -> None:
        payload = _incident().payload(now=T0)
        assert payload["v"] == 2
        assert payload["origin"] == "location"
        assert "place" not in payload
        casa = _incident(place="Casa").payload(now=T0)
        assert casa["origin"] == "place" and casa["place"] == "Casa"

    def test_confirmado_por_quien_fue_al_lugar(self) -> None:
        body = _incident(
            confidence=1.0,
            source_count=2,
            sources=["bomberos", "media"],
            is_official_confirmed=True,
        ).body
        assert "Confirmado por Bomberos" in body

    def test_tramo_alto_sin_verificacion_no_dice_confirmado(self) -> None:
        body = _incident(confidence=0.72, source_count=3).body
        assert "confirmado" not in body.lower()
        assert body.endswith("Sin verificar (3 fuentes)")

    def test_la_alerta_de_senapred_va_primero(self) -> None:
        lines = _incident(alert_level="roja").body.split("\n")
        assert lines[0] == "🔴 Alerta roja de SENAPRED"
        assert lines[1].startswith("Viña del Mar")

    def test_sin_comuna_igual_dice_la_hora(self) -> None:
        assert _incident(commune=None).body.startswith("desde 14:32")

    def test_el_enlace_abre_el_incidente(self) -> None:
        message = _incident()
        assert message.url == "/?incidente=INC-2026-00142"
        assert message.tag == "INC-2026-00142"
        assert message.topic == "INC-2026-00142"

    def test_corte_de_luz_con_clientes_y_reposicion(self) -> None:
        message = _incident(
            incident_type=IncidentType.POWER_OUTAGE,
            distance_m=800,
            commune="Quilpué",
            confidence=1.0,
            source_count=1,
            sources=["chilquinta"],
            is_official_confirmed=True,
            outage=OutageFacts(
                provider="chilquinta",
                affected_clients=1250,
                estimated_restoration=datetime(2026, 9, 23, 21, 30, tzinfo=UTC),
            ),
        )
        assert message.title == "⚡ Corte de luz a 800 m de tu última ubicación"
        assert message.body == "Quilpué · desde 14:32 · 1.250 clientes · vuelve ~18:30 · Chilquinta"

    def test_corte_programado_dice_hasta_cuando_y_no_desde(self) -> None:
        message = _incident(
            incident_type=IncidentType.POWER_OUTAGE,
            distance_m=800,
            commune="Quilpué",
            confidence=1.0,
            source_count=1,
            sources=["chilquinta"],
            is_official_confirmed=True,
            outage=OutageFacts(
                provider="chilquinta",
                affected_clients=120,
                estimated_restoration=datetime(2026, 9, 23, 21, 30, tzinfo=UTC),
                programado=True,
            ),
        )
        assert message.title == "⚡ Corte de luz programado a 800 m de tu última ubicación"
        assert message.body == "Quilpué · 120 clientes · hasta ~18:30 · Chilquinta"

    def test_accidente(self) -> None:
        assert _incident(incident_type=IncidentType.ACCIDENT).title.startswith(
            "🚗 Accidente de tránsito"
        )


class TestSismo:
    def _message(self, **overrides: object):  # type: ignore[no-untyped-def]
        base: dict[str, object] = {
            "key": "csn:379889",
            "provider": "csn",
            "magnitude": 4.8,
            "distance_m": 62_300,
            "timestamp": T0,
            "lat": -33.02,
            "lon": -71.9,
            "place": "25 km al O de Valparaíso",
            "depth_km": 34.6,
        }
        base.update(overrides)
        return seismic_message(**base)  # type: ignore[arg-type]

    def test_titulo_con_magnitud_y_distancia(self) -> None:
        assert self._message().title == "🌎 Sismo 4,8 a 62 km de tu última ubicación"

    def test_cuerpo(self) -> None:
        assert self._message().body == "25 km al O de Valparaíso · 14:32 · 35 km de prof. · CSN"

    def test_desde_un_lugar_guardado(self) -> None:
        message = self._message(saved_place="Casa")
        assert message.title == "🌎 Sismo 4,8 a 62 km de Casa"
        assert message.payload(now=T0)["place"] == "Casa"

    def test_ubicacion_vieja(self) -> None:
        message = self._message(located_at=T0 - timedelta(days=2, hours=1), now=T0)
        assert message.body.endswith("\n📍 Ubicación de hace 2 días")

    def test_tag_y_topic_validos(self) -> None:
        message = self._message(key="usgs:us6000tlm3")
        assert message.tag == "sismo-usgs:us6000tlm3"
        assert message.topic == "sismo-usgs-us6000tlm3"
        assert "sismo=usgs%3Aus6000tlm3" in message.url


def test_topic_se_recorta_a_32_caracteres() -> None:
    assert len(safe_topic("x" * 50)) == 32


def test_prueba() -> None:
    message = probe_message(radius_m=5000)
    assert message.title == "✅ Avisos activados"
    assert "AlertaV" not in message.title  # el teléfono ya agrega «from AlertaV»
    assert "a menos de 5 km" in message.body


def test_la_prueba_cuenta_los_radios_elegidos() -> None:
    message = probe_message(radios={"fire": 5000, "traffic": 2000, "power": 0, "water": 1000})
    first = message.body.split("\n")[0]
    assert first == "🔥 5 km · 🚗 2 km · 💧 1 km · 🌎 sismos que se sientan"


def test_el_payload_cabe_con_holgura() -> None:
    message = _incident(alert_level="temprana_preventiva", commune="X" * 120)
    raw = json.dumps(message.payload(now=T0), ensure_ascii=False).encode()
    assert len(raw) < MAX_PLAINTEXT_BYTES / 4
