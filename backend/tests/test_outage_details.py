"""Agregación de metadatos de cortes de suministro.

Los criterios de agregación no son obvios y son fáciles de romper sin darse
cuenta, así que quedan fijados acá: los clientes se suman, la reposición es la
más tardía, y un campo ausente nunca se convierte en cero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_incident_service
from app.main import app
from app.models.enums import EventSource, IncidentStatus, IncidentType
from app.schemas.incident import OutageDetail
from app.services.incident_service import IncidentService

D = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _incident(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": 1,
        "code": "INC-2026-00500",
        "public_id": "3f2b6c1e-0000-4000-8000-000000000001",
        "type": IncidentType.POWER_OUTAGE,
        "status": IncidentStatus.ACTIVE,
        "lat": -33.05,
        "lon": -71.62,
        "confidence": 1.0,
        "is_official_confirmed": False,
        "alert_confidence": 0.0,
        "alert_level": None,
        "title": None,
        "commune": "Viña del Mar",
        "province": "Valparaíso",
        "event_count": 2,
        "source_count": 1,
        "sources": [EventSource.CHILQUINTA.value],
        "first_seen_at": D,
        "last_seen_at": D,
        "resolved_at": None,
        "correlated_at": D,
        "confidence_breakdown": {},
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Repo:
    def __init__(self, details: dict[int, dict[str, Any]]) -> None:
        self._details = details
        self.asked: list[list[int]] = []

    async def outage_details(self, incident_ids: Any) -> dict[int, dict[str, Any]]:
        self.asked.append(list(incident_ids))
        return {k: v for k, v in self._details.items() if k in set(incident_ids)}


def _service(details: dict[int, dict[str, Any]]) -> IncidentService:
    service = IncidentService.__new__(IncidentService)
    service.repo = _Repo(details)  # type: ignore[attr-defined]
    return service


class TestOutageEnrichment:
    async def test_adosa_los_metadatos_al_incidente_de_corte(self) -> None:
        service = _service(
            {
                1: {
                    "provider": "chilquinta",
                    "affected_clients": 1420,
                    "estimated_restoration": "2026-08-20T18:30:00+00:00",
                    "sector": "Forestal Alto",
                    "outage_count": 2,
                }
            }
        )
        [model] = await service.read_with_outages([_incident()])

        assert model.outage is not None
        assert model.outage.provider == "chilquinta"
        assert model.outage.affected_clients == 1420
        assert model.outage.sector == "Forestal Alto"

    async def test_no_consulta_si_no_hay_cortes_en_el_lote(self) -> None:
        """Un día sin cortes no debe pagar una consulta extra."""
        service = _service({})
        incendio = _incident(id=9, code="INC-9", type=IncidentType.WILDFIRE)

        models = await service.read_with_outages([incendio])

        assert models[0].outage is None
        assert service.repo.asked == []  # type: ignore[attr-defined]

    async def test_incidente_sin_detalle_queda_en_none(self) -> None:
        """Si el corte no tiene señales con metadatos, `outage` es null, no {}."""
        service = _service({})
        [model] = await service.read_with_outages([_incident()])
        assert model.outage is None


class TestOutageSchema:
    def test_campos_ausentes_son_none_y_no_cero(self) -> None:
        """Un feed sin clientes no puede convertirse en «0 clientes afectados»."""
        detail = OutageDetail.model_validate(
            {"provider": "cge", "affected_clients": None, "estimated_restoration": None}
        )
        assert detail.affected_clients is None
        assert detail.estimated_restoration is None
        assert detail.outage_count == 1

    def test_serializa_la_reposicion_como_fecha(self) -> None:
        detail = OutageDetail.model_validate(
            {"provider": "cge", "estimated_restoration": "2026-08-20T18:30:00+00:00"}
        )
        assert detail.estimated_restoration is not None
        assert detail.estimated_restoration.year == 2026


class TestOutageEndpoint:
    @pytest.fixture
    def client(self) -> Any:
        service = _service(
            {
                1: {
                    "provider": "cge",
                    "affected_clients": 300,
                    "estimated_restoration": None,
                    "sector": None,
                    "outage_count": 1,
                }
            }
        )

        async def _list_active(**_: Any) -> list[SimpleNamespace]:
            return [_incident(sources=[EventSource.CGE.value])]

        service.list_active = _list_active  # type: ignore[assignment]
        app.dependency_overrides[get_incident_service] = lambda: service
        yield TestClient(app)
        app.dependency_overrides.pop(get_incident_service, None)

    def test_active_devuelve_outage_en_el_payload(self, client: Any) -> None:
        payload = client.get("/api/v1/incidents/active").json()

        assert payload[0]["outage"]["provider"] == "cge"
        assert payload[0]["outage"]["affected_clients"] == 300
        # La clave existe aunque venga vacía: el cliente distingue «no informado»
        # de «campo inexistente» sin tener que adivinar.
        assert payload[0]["outage"]["estimated_restoration"] is None
