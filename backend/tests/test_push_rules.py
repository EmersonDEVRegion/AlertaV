"""Reglas del notificador: qué incidente despierta a alguien y qué sismo se sintió."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.enums import IncidentStatus, IncidentType
from app.services.push import rules
from app.services.push.rules import (
    QuakeView,
    group_quakes,
    incident_is_notifiable,
    perception_reach_km,
)

T0 = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)


def _notifiable(**overrides: object) -> bool:
    base: dict[str, object] = {
        "status": IncidentStatus.ACTIVE,
        "incident_type": IncidentType.WILDFIRE,
        "confidence": 0.45,
        "source_count": 2,
        "is_official_confirmed": False,
        "min_confidence": 0.30,
        "min_sources": 2,
    }
    base.update(overrides)
    return incident_is_notifiable(**base)  # type: ignore[arg-type]


class TestIncidentes:
    def test_corroborado_por_dos_fuentes_sobre_el_umbral_se_avisa(self) -> None:
        assert _notifiable()

    def test_una_sola_fuente_no_basta_aunque_supere_el_umbral(self) -> None:
        # Un píxel de FIRMS entra en 0.40 y una alerta de Waze también: es el
        # caso de la chimenea de Ventanas.
        assert not _notifiable(confidence=0.40, source_count=1)

    def test_bajo_el_umbral_no_se_avisa_aunque_haya_dos_fuentes(self) -> None:
        assert not _notifiable(confidence=0.29, source_count=2)

    def test_el_borde_del_umbral_cuenta_aunque_vuelva_de_la_base_como_float4(self) -> None:
        # 0.30 escrito como REAL vuelve como 0.30000001192…, pero 0.2999999 es
        # el caso que muerde: el epsilon lo absorbe.
        assert _notifiable(confidence=0.2999999)

    def test_confirmado_por_conaf_o_bomberos_se_avisa_solo(self) -> None:
        assert _notifiable(confidence=1.0, source_count=1, is_official_confirmed=True)

    def test_un_corte_de_luz_se_avisa_siempre(self) -> None:
        assert _notifiable(
            incident_type=IncidentType.POWER_OUTAGE,
            confidence=0.1,
            source_count=1,
            is_official_confirmed=False,
        )

    @pytest.mark.parametrize(
        "status",
        [
            IncidentStatus.CONTROLLED,
            IncidentStatus.EXTINGUISHED,
            IncidentStatus.STALE,
            IncidentStatus.MERGED,
            IncidentStatus.DISMISSED,
        ],
    )
    def test_solo_los_activos(self, status: IncidentStatus) -> None:
        assert not _notifiable(status=status, is_official_confirmed=True)

    def test_min_sources_1_deja_pasar_la_senal_aislada(self) -> None:
        assert _notifiable(confidence=0.40, source_count=1, min_sources=1)


class TestRadioDePercepcion:
    def test_sin_magnitud_no_se_afirma_nada(self) -> None:
        assert perception_reach_km(None, 10.0, max_reach_km=400) is None

    def test_un_m35_superficial_se_siente_a_unas_decenas_de_km(self) -> None:
        reach = perception_reach_km(3.5, 15.0, max_reach_km=400)
        assert reach is not None and 20 < reach < 35

    def test_un_m5_se_siente_en_toda_la_region(self) -> None:
        reach = perception_reach_km(5.0, 30.0, max_reach_km=400)
        assert reach is not None and reach > 150

    def test_la_profundidad_achica_el_radio(self) -> None:
        shallow = perception_reach_km(4.5, 10.0, max_reach_km=400)
        deep = perception_reach_km(4.5, 80.0, max_reach_km=400)
        assert shallow is not None and deep is not None and deep < shallow

    def test_un_microsismo_profundo_no_se_siente_en_ninguna_parte(self) -> None:
        assert perception_reach_km(3.0, 100.0, max_reach_km=400) is None

    def test_el_tope_recorta_los_grandes(self) -> None:
        assert perception_reach_km(8.0, 30.0, max_reach_km=400) == 400

    def test_sin_profundidad_supone_15_km(self) -> None:
        assert perception_reach_km(4.0, None, max_reach_km=400) == perception_reach_km(
            4.0, 15.0, max_reach_km=400
        )

    def test_las_constantes_son_las_del_mapa(self) -> None:
        """El círculo del mapa es la promesa de a quién se le avisa."""
        path = (
            Path(__file__).resolve().parents[2] / "frontend" / "src" / "domain" / "seismicReach.ts"
        )
        if not path.exists():  # pragma: no cover — checkout sólo del backend
            pytest.skip("no está el frontend al lado")
        source = path.read_text(encoding="utf-8")

        attenuation = re.search(
            r"ATTENUATION\s*=\s*\{\s*a:\s*([\d.]+),\s*b:\s*([\d.]+),\s*c:\s*([\d.]+)", source
        )
        assert attenuation is not None
        assert tuple(float(x) for x in attenuation.groups()) == (
            rules.ATTENUATION_A,
            rules.ATTENUATION_B,
            rules.ATTENUATION_C,
        )

        def constant(name: str) -> float:
            match = re.search(rf"{name}\s*=\s*([\d.]+)", source)
            assert match is not None, name
            return float(match.group(1))

        assert constant("PERCEPTION_INTENSITY") == rules.PERCEPTION_INTENSITY
        assert constant("ASSUMED_DEPTH_KM") == rules.ASSUMED_DEPTH_KM
        assert constant("MIN_DRAWABLE_KM") == rules.MIN_REACH_KM
        from app.core.config import settings

        assert constant("MAX_REACH_KM") == settings.PUSH_SEISMIC_MAX_REACH_KM


def _quake(key: str, provider: str, **overrides: object) -> QuakeView:
    base: dict[str, object] = {
        "key": key,
        "provider": provider,
        "timestamp": T0,
        "lat": -33.0,
        "lon": -71.8,
        "magnitude": 4.2,
        "depth_km": 30.0,
        "place": None,
    }
    base.update(overrides)
    return QuakeView(**base)  # type: ignore[arg-type]


class TestAgrupacionDeSismos:
    def test_el_mismo_sismo_del_csn_y_del_usgs_es_uno(self) -> None:
        groups = group_quakes(
            [
                _quake("usgs:us1", "usgs", timestamp=T0 + timedelta(seconds=20), lat=-33.2),
                _quake("csn:1", "csn"),
            ]
        )
        assert len(groups) == 1
        assert groups[0].representative.key == "csn:1"
        assert sorted(groups[0].keys) == ["csn:1", "usgs:us1"]

    def test_el_csn_gana_aunque_llegue_despues(self) -> None:
        groups = group_quakes(
            [
                _quake("usgs:us1", "usgs"),
                _quake("csn:1", "csn", timestamp=T0 + timedelta(seconds=40)),
            ]
        )
        assert groups[0].representative.provider == "csn"

    def test_dos_sismos_separados_en_el_tiempo_son_dos(self) -> None:
        groups = group_quakes(
            [_quake("csn:1", "csn"), _quake("csn:2", "csn", timestamp=T0 + timedelta(minutes=5))]
        )
        assert len(groups) == 2

    def test_dos_sismos_simultaneos_lejanos_son_dos(self) -> None:
        groups = group_quakes([_quake("csn:1", "csn"), _quake("csn:2", "csn", lat=-31.0)])
        assert len(groups) == 2

    def test_una_version_con_magnitud_le_gana_a_una_sin_ella(self) -> None:
        groups = group_quakes(
            [_quake("usgs:a", "usgs", magnitude=None), _quake("usgs:b", "usgs", magnitude=4.0)]
        )
        assert groups[0].representative.key == "usgs:b"
