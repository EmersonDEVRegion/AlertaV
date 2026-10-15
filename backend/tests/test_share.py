"""Enlace compartible `/share/i/{code}`: vista previa Open Graph y redirección."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_incident_service
from app.api.v1.endpoints.share import render_share_page, share_card
from app.main import app
from app.models.enums import IncidentStatus, IncidentType


def _incident(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "code": "INC-2026-00142",
        "type": IncidentType.WILDFIRE,
        "status": IncidentStatus.ACTIVE,
        "commune": "Viña del Mar",
        "first_seen_at": datetime(2026, 1, 10, 17, 32, tzinfo=UTC),
        "source_count": 3,
        "is_official_confirmed": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Service:
    def __init__(self, incident: Any | None) -> None:
        self.incident = incident
        self.calls: list[str] = []

    async def get_detail(self, *, code: str | None = None, public_id: Any = None) -> Any:
        self.calls.append(code or "")
        return self.incident if self.incident and code == self.incident.code else None


@pytest.fixture
def make_client():
    def _make(incident: Any | None) -> tuple[TestClient, _Service]:
        service = _Service(incident)
        app.dependency_overrides[get_incident_service] = lambda: service
        return TestClient(app), service

    yield _make
    app.dependency_overrides.pop(get_incident_service, None)


class TestShareCard:
    def test_titulo_con_tipo_y_comuna(self) -> None:
        card = share_card(_incident(), "INC-2026-00142")
        assert card["title"] == "Incendio forestal — Viña del Mar"

    def test_descripcion_en_hora_de_chile(self) -> None:
        card = share_card(_incident(), "INC-2026-00142")
        # 17:32 UTC en enero = 14:32 en Chile (UTC-3, horario de verano).
        assert "desde 10-01 14:32" in card["description"]
        assert "3 fuentes" in card["description"]
        assert "confirmado por fuente oficial" in card["description"]

    def test_destino_es_el_deep_link_de_la_app(self) -> None:
        card = share_card(_incident(), "INC-2026-00142")
        assert card["target"].endswith("/?incidente=INC-2026-00142")
        assert card["url"].endswith("/i/INC-2026-00142")

    def test_imagen_por_tipo(self) -> None:
        assert share_card(_incident(), "x")["image"].endswith("/og/incendio.png")
        assert share_card(_incident(type=IncidentType.ACCIDENT), "x")["image"].endswith(
            "/og/accidente.png"
        )

    def test_sin_comuna_dice_la_region(self) -> None:
        card = share_card(_incident(commune=None), "x")
        assert card["title"].endswith("Región de Valparaíso")

    def test_folio_desconocido_va_al_mapa(self) -> None:
        card = share_card(None, "INC-2026-99999")
        assert card["target"].endswith("/")
        assert "?incidente" not in card["target"]

    def test_escapa_html(self) -> None:
        html = render_share_page(share_card(_incident(commune='"><script>'), "x"))
        assert "<script>" not in html
        assert "&quot;&gt;&lt;script&gt;" in html


class TestShareEndpoint:
    def test_responde_html_con_open_graph(self, make_client) -> None:
        client, service = make_client(_incident())
        r = client.get("/api/v1/share/i/inc-2026-00142")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert 'property="og:title" content="Incendio forestal — Viña del Mar"' in r.text
        assert 'http-equiv="refresh"' in r.text
        assert service.calls == ["INC-2026-00142"]

    def test_folio_inexistente_no_es_error(self, make_client) -> None:
        client, _ = make_client(None)
        r = client.get("/api/v1/share/i/INC-2026-99999")
        assert r.status_code == 200
        assert "AlertaV — Emergencias de la Región de Valparaíso" in r.text

    def test_basura_no_consulta_la_base(self, make_client) -> None:
        client, service = make_client(None)
        r = client.get("/api/v1/share/i/hola")
        assert r.status_code == 200
        assert service.calls == []
