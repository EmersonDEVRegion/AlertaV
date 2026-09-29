"""Agregación de metadatos de cortes de suministro.

Los criterios de agregación no son obvios y son fáciles de romper sin darse
cuenta, así que quedan fijados acá: los clientes se suman, la reposición es la
más tardía, y un campo ausente nunca se convierte en cero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_incident_service
from app.main import app
from app.models.enums import EventSource, IncidentStatus, IncidentType
from app.schemas.incident import OutageDetail
from app.services.incident_service import IncidentService, adosar_vigencia

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
    def __init__(
        self,
        details: dict[int, dict[str, Any]],
        lecturas: dict[str, datetime] | None = None,
    ) -> None:
        self._details = details
        self._lecturas = lecturas or {}
        self.asked: list[list[int]] = []
        self.lecturas_pedidas: list[list[str]] = []

    async def outage_details(self, incident_ids: Any) -> dict[int, dict[str, Any]]:
        self.asked.append(list(incident_ids))
        return {k: dict(v) for k, v in self._details.items() if k in set(incident_ids)}

    async def ultimas_lecturas(self, collectors: Any) -> dict[str, datetime]:
        self.lecturas_pedidas.append(list(collectors))
        return {k: v for k, v in self._lecturas.items() if k in set(collectors)}


def _service(
    details: dict[int, dict[str, Any]],
    lecturas: dict[str, datetime] | None = None,
) -> IncidentService:
    service = IncidentService.__new__(IncidentService)
    service.repo = _Repo(details, lecturas)  # type: ignore[attr-defined]
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


class TestVigenciaDelCorte:
    """Vigente = la empresa lo volvió a listar en su última lectura.

    Chilquinta y CGE no publican el fin de un corte: lo dejan de listar. El
    trigger de `raw_events` mueve `updated_at` en cada upsert, así que el
    `visto_en` del grupo dice cuándo lo publicaron por última vez.
    """

    LECTURA = D + timedelta(hours=3)

    def _payload(self, visto: datetime | None, **over: Any) -> dict[str, Any]:
        base = {"provider": "chilquinta", "visto_en": visto, "collector": "chilquinta_cortes"}
        base.update(over)
        return base

    def test_listado_en_la_ultima_lectura_es_vigente(self) -> None:
        payloads = {1: self._payload(self.LECTURA + timedelta(seconds=40))}
        adosar_vigencia(payloads, {"chilquinta_cortes": self.LECTURA})
        assert payloads[1]["vigente"] is True

    def test_el_margen_cubre_relojes_desfasados(self) -> None:
        payloads = {1: self._payload(self.LECTURA - timedelta(seconds=30))}
        adosar_vigencia(payloads, {"chilquinta_cortes": self.LECTURA})
        assert payloads[1]["vigente"] is True

    def test_si_la_empresa_ya_no_lo_lista_no_es_vigente(self) -> None:
        payloads = {1: self._payload(self.LECTURA - timedelta(minutes=10))}
        adosar_vigencia(payloads, {"chilquinta_cortes": self.LECTURA})
        assert payloads[1]["vigente"] is False

    def test_sin_lectura_no_se_afirma_nada(self) -> None:
        """Sin corrida con qué comparar, `null`: ni vigente ni repuesto."""
        payloads = {1: self._payload(self.LECTURA)}
        adosar_vigencia(payloads, {})
        assert payloads[1]["vigente"] is None

    def test_senal_vieja_sin_collector_usa_el_de_su_empresa(self) -> None:
        payloads = {1: self._payload(self.LECTURA, provider="cge", collector=None)}
        adosar_vigencia(payloads, {"cge_cortes": self.LECTURA})
        assert payloads[1]["vigente"] is True

    def test_fechas_sin_zona_se_leen_en_utc(self) -> None:
        payloads = {1: self._payload(self.LECTURA.replace(tzinfo=None))}
        adosar_vigencia(payloads, {"chilquinta_cortes": self.LECTURA})
        assert payloads[1]["vigente"] is True

    async def test_el_listado_trae_vigente_con_una_consulta_por_lote(self) -> None:
        service = _service(
            {
                1: self._payload(self.LECTURA + timedelta(seconds=5)),
                2: self._payload(D, provider="cge", collector="cge_cortes"),
            },
            {"chilquinta_cortes": self.LECTURA, "cge_cortes": self.LECTURA},
        )
        models = await service.read_with_outages(
            [_incident(), _incident(id=2, code="INC-2026-00501")]
        )
        assert [m.outage.vigente for m in models if m.outage] == [True, False]
        assert service.repo.lecturas_pedidas == [["cge_cortes", "chilquinta_cortes"]]  # type: ignore[attr-defined]

    def test_el_esquema_expone_visto_en_y_vigente(self) -> None:
        detail = OutageDetail.model_validate(
            {"provider": "cge", "visto_en": self.LECTURA, "vigente": False, "collector": "x"}
        )
        dumped = detail.model_dump(mode="json")
        assert dumped["vigente"] is False
        assert dumped["visto_en"].startswith("2026-08-20T15:00")
        assert "collector" not in dumped
