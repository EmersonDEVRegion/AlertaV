"""Tests de las piezas del motor que no necesitan base de datos.

La orquestación se verifica contra PostGIS real en `scripts/smoke_test.py`. Lo
que se cubre acá es la aritmética y las decisiones que no deberían depender de
tener una base levantada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import EventSource, EventType
from app.repositories.incident_repository import ClusteredEvent
from app.services.correlation.engine import (
    CorrelationEngine,
    CorrelationPass,
    haversine_m,
    weighted_centroid,
)

T0 = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)

# Plaza Sucre, Viña del Mar y Plaza Sotomayor, Valparaíso: ~7,5 km en línea recta.
VINA = (-33.0245, -71.5518)
VALPO = (-33.0367, -71.6297)


def evento(
    source: EventSource,
    lat: float,
    lon: float,
    confidence: float,
    *,
    event_id: int = 1,
    minutes: int = 0,
) -> ClusteredEvent:
    return ClusteredEvent(
        event_id=event_id,
        cluster_id=0,
        lat=lat,
        lon=lon,
        confidence=confidence,
        timestamp=T0 + timedelta(minutes=minutes),
        source=source,
        type=EventType.THERMAL_ANOMALY,
    )


class TestDistancia:
    def test_un_grado_de_latitud(self) -> None:
        assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_195, rel=0.001)

    def test_distancia_conocida_en_la_region(self) -> None:
        distancia = haversine_m(*VINA, *VALPO)
        assert 7_000 < distancia < 8_500

    def test_un_punto_consigo_mismo(self) -> None:
        assert haversine_m(*VINA, *VINA) == pytest.approx(0.0, abs=1e-6)

    def test_es_simetrica(self) -> None:
        assert haversine_m(*VINA, *VALPO) == pytest.approx(haversine_m(*VALPO, *VINA))


class TestCentroide:
    def test_una_sola_señal_se_queda_donde_esta(self) -> None:
        lat, lon = weighted_centroid([evento(EventSource.CONAF, *VINA, 1.0)])
        assert (lat, lon) == pytest.approx(VINA)

    def test_el_centro_se_va_hacia_la_fuente_mas_creible(self) -> None:
        """Con un punto de CONAF y varios píxeles de VIIRS repartidos por la
        ladera, el centro sin ponderar se iría cerro arriba y el mapa dejaría de
        coincidir con el lugar que el organismo reportó."""
        miembros = [
            evento(EventSource.CONAF, -33.00, -71.50, 1.0, event_id=1),
            evento(EventSource.NASA_FIRMS, -33.10, -71.50, 0.3, event_id=2),
            evento(EventSource.NASA_FIRMS, -33.10, -71.50, 0.3, event_id=3),
        ]
        lat, _ = weighted_centroid(miembros)
        promedio_simple = (-33.00 + -33.10 + -33.10) / 3
        assert lat > promedio_simple  # más al norte: más cerca de CONAF
        assert -33.00 > lat > -33.06

    def test_confianza_cero_no_rompe_la_division(self) -> None:
        lat, lon = weighted_centroid(
            [
                evento(EventSource.OTHER, -33.0, -71.5, 0.0, event_id=1),
                evento(EventSource.OTHER, -33.2, -71.5, 0.0, event_id=2),
            ]
        )
        assert (lat, lon) == pytest.approx((-33.1, -71.5))


class TestAperturaDeIncidentes:
    def _engine(self, *, min_signals: int) -> CorrelationEngine:
        engine = CorrelationEngine.__new__(CorrelationEngine)
        engine.min_signals = min_signals
        return engine

    def test_una_fuente_confirmatoria_abre_incidente_aunque_venga_sola(self) -> None:
        """Si CONAF dice que hay un incendio, no hace falta que nadie corrobore."""
        engine = self._engine(min_signals=3)
        assert engine._should_open_incident([evento(EventSource.CONAF, *VINA, 1.0)])

    def test_una_señal_aislada_no_confirmatoria_espera_corroboracion(self) -> None:
        engine = self._engine(min_signals=2)
        assert not engine._should_open_incident(
            [evento(EventSource.NASA_FIRMS, *VINA, 0.55)]
        )

    def test_con_el_minimo_alcanzado_si_abre(self) -> None:
        engine = self._engine(min_signals=2)
        assert engine._should_open_incident(
            [
                evento(EventSource.NASA_FIRMS, *VINA, 0.55, event_id=1),
                evento(EventSource.CITIZEN, *VINA, 0.50, event_id=2),
            ]
        )

    def test_el_minimo_por_defecto_deja_pasar_todo(self) -> None:
        engine = self._engine(min_signals=1)
        assert engine._should_open_incident(
            [evento(EventSource.CITIZEN, *VINA, 0.50)]
        )


class TestFusionEncadenada:
    """Si A absorbe a B y luego B aparece como superviviente de otro par, hay que
    seguir la cadena hasta el incidente que realmente sigue vivo."""

    def test_sigue_la_cadena(self) -> None:
        redirect = {3: 2, 2: 1}
        assert CorrelationEngine._resolve_redirect(redirect, 3) == 1

    def test_no_se_cuelga_con_un_ciclo(self) -> None:
        redirect = {1: 2, 2: 1}
        assert CorrelationEngine._resolve_redirect(redirect, 1) in (1, 2)

    def test_sin_redirecciones_devuelve_el_mismo(self) -> None:
        assert CorrelationEngine._resolve_redirect({}, 7) == 7


class TestTrazaDeLaPasada:
    def test_serializa_para_el_log_y_la_api(self) -> None:
        result = CorrelationPass(started_at=T0)
        result.finished_at = T0 + timedelta(seconds=3)
        result.incidents_created = 2

        payload = result.as_dict()
        assert payload["duration_seconds"] == 3.0
        assert payload["incidents_created"] == 2
        assert payload["warnings"] == []

    def test_una_pasada_sin_terminar_no_miente_sobre_su_duracion(self) -> None:
        assert CorrelationPass(started_at=T0).duration_seconds == 0.0
