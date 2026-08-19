"""Tests del Confidence Engine.

Todo lo que se verifica acá es una regla de negocio explícita, no un detalle de
implementación. Si algún día se recalibran los pesos, estos tests deben seguir
pasando: lo que fijan es la *forma* de la política —qué corrobora, qué confirma,
qué nunca llega a 100 %— y no los números exactos, salvo donde el número exacto
ES la regla (CONAF = 1.0).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.models.enums import EventSource, EventType, IncidentStatus, IncidentType
from app.services.correlation.confidence import (
    CONFIRMED_THRESHOLD,
    OFFICIAL_DISPATCH_WEIGHT,
    POLICY_VERSION,
    UNCONFIRMED_CEILING,
    UNSAFE_THRESHOLD,
    ConfidenceLevel,
    SignalView,
    build_title,
    level_for,
    resolve_status,
    resolve_type,
    rule_for,
    score,
)

T0 = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)


def signal(
    source: EventSource,
    confidence: float,
    *,
    type: EventType = EventType.THERMAL_ANOMALY,
    minutes: int = 0,
    raw_data: dict | None = None,
) -> SignalView:
    return SignalView(
        source=source,
        type=type,
        confidence=confidence,
        timestamp=T0 + timedelta(minutes=minutes),
        raw_data=raw_data or {},
    )


def firms(n: int, confidence: float = 0.55) -> list[SignalView]:
    return [signal(EventSource.NASA_FIRMS, confidence, minutes=i) for i in range(n)]


def citizens(n: int, confidence: float = 0.50) -> list[SignalView]:
    return [
        signal(EventSource.CITIZEN, confidence, type=EventType.SMOKE, minutes=i)
        for i in range(n)
    ]


def dispatches(n: int, confidence: float = 0.65) -> list[SignalView]:
    return [
        signal(EventSource.BROADCASTIFY, confidence, type=EventType.DISPATCH, minutes=i)
        for i in range(n)
    ]


ALERTA_ROJA = signal(
    EventSource.SENAPRED,
    1.0,
    type=EventType.ALERT,
    raw_data={"_alert_level": "roja", "Evento": "Incendio Forestal"},
)

CONAF_EN_COMBATE = signal(
    EventSource.CONAF,
    1.0,
    type=EventType.WILDFIRE,
    raw_data={"estado": "En Combate", "comuna": "Viña del Mar"},
)


class TestFuentesConfirmatorias:
    def test_conaf_sola_confirma_al_instante(self) -> None:
        """CONAF combate el incendio: su registro ES la confirmación."""
        result = score([CONAF_EN_COMBATE])
        assert result.confidence == 1.0
        assert result.is_official_confirmed is True

    def test_bomberos_tambien_confirma(self) -> None:
        result = score([signal(EventSource.BOMBEROS, 1.0, type=EventType.DISPATCH)])
        assert result.confidence == 1.0
        assert result.is_official_confirmed is True

    def test_conaf_arrastra_al_incidente_completo(self) -> None:
        result = score([CONAF_EN_COMBATE, *firms(3), *citizens(2)])
        assert result.confidence == 1.0
        assert result.breakdown["ceiling_applied"] == "confirming_source"


class TestSatelite:
    def test_el_peso_base_es_40(self) -> None:
        """La recalibración v2: una anomalía térmica vale 0.40, ni más ni menos.

        La banda está colapsada a propósito. La confianza que trae el píxel mide
        la certeza del algoritmo sobre la anomalía, no la probabilidad de que la
        anomalía sea un incendio: una lectura excelente de una chimenea sigue
        siendo una chimenea.
        """
        for entrada in (0.10, 0.55, 0.99):
            result = score(firms(1, confidence=entrada))
            assert result.confidence == pytest.approx(0.40)
            assert result.is_official_confirmed is False

    def test_una_deteccion_aislada_es_solo_posible_emergencia(self) -> None:
        assert score(firms(1)).level is ConfidenceLevel.POSSIBLE

    def test_ningun_racimo_satelital_llega_a_incendio_confirmado(self) -> None:
        """La regla dura del proyecto, y la razón de esta recalibración.

        Sin techo bajo 0.60, veinte píxeles de la misma pasada de VIIRS —que son
        casi una sola observación— cruzarían solos a `CONFIRMED`. En la V Región
        eso significaría rotular la fundición de Ventanas como incendio.
        """
        ceiling = rule_for(EventSource.NASA_FIRMS).ceiling
        assert ceiling < CONFIRMED_THRESHOLD

        for count in (5, 20, 100):
            result = score(firms(count, confidence=0.80))
            assert result.confidence <= ceiling
            assert result.level is ConfidenceLevel.POSSIBLE
            assert result.is_official_confirmed is False

    def test_mas_detecciones_nunca_bajan_la_confianza(self) -> None:
        serie = [score(firms(n)).confidence for n in range(1, 8)]
        assert serie == sorted(serie)

    def test_cruzarse_con_otra_fuente_sube_por_encima_del_techo_propio(self) -> None:
        """Para esto existe el motor: dos indicios de origen distinto valen más
        que muchos del mismo origen."""
        solo = score(firms(3)).confidence
        cruzado = score([*firms(3), *citizens(2)]).confidence
        assert cruzado > solo
        assert cruzado > rule_for(EventSource.NASA_FIRMS).ceiling


class TestSumaEntreFuentes:
    """La combinación es aditiva desde la v2.0.0: `min(Σ wᵢ, 1.0)`."""

    def test_satelite_mas_ciudadano_suma_los_porcentajes(self) -> None:
        """El caso de calibración: 40 % + 25 % = 65 %.

        Es el ejemplo con el que se fijó la política. Si este test cambia, la
        política cambió.
        """
        result = score([*firms(1), *citizens(1, confidence=0.10)])
        assert result.confidence == pytest.approx(0.65)
        assert result.level is ConfidenceLevel.CONFIRMED

    def test_el_breakdown_permite_rehacer_la_cuenta_a_mano(self) -> None:
        result = score([*firms(1), *citizens(1, confidence=0.10)])
        aportes = [
            entry["contribution"] for entry in result.breakdown["by_source"].values()
        ]
        assert sum(aportes) == pytest.approx(result.breakdown["combined"])
        assert result.breakdown["combination"] == "additive_capped"

    def test_la_suma_satura_en_uno_sin_confirmacion_no_llega_a_la_certeza(self) -> None:
        result = score([*firms(4), *citizens(4), *dispatches(3)])
        assert result.confidence == pytest.approx(UNCONFIRMED_CEILING)
        assert result.is_official_confirmed is False


class TestDespachosOficiales:
    def test_un_despacho_por_radio_pesa_80(self) -> None:
        result = score(dispatches(1))
        assert result.confidence == pytest.approx(OFFICIAL_DISPATCH_WEIGHT)

    def test_un_solo_despacho_basta_para_confirmar_el_tramo(self) -> None:
        """0.80 > 0.60: cuando la central manda carros, hay una decisión humana
        con autoridad detrás del punto en el mapa."""
        assert score(dispatches(1)).level is ConfidenceLevel.CONFIRMED

    def test_pero_no_es_confirmacion_institucional(self) -> None:
        """`CONFIRMED` es un juicio del motor; `is_official_confirmed` es un
        hecho: alguien fue al lugar. La UI tiene que poder distinguirlos."""
        result = score(dispatches(2))
        assert result.level is ConfidenceLevel.CONFIRMED
        assert result.is_official_confirmed is False

    def test_el_peso_no_depende_de_la_calidad_del_audio(self) -> None:
        flojo = score(dispatches(1, confidence=0.10)).confidence
        nitido = score(dispatches(1, confidence=0.95)).confidence
        assert flojo == pytest.approx(nitido) == pytest.approx(OFFICIAL_DISPATCH_WEIGHT)

    def test_bomberos_sigue_confirmando_al_100(self) -> None:
        result = score([signal(EventSource.BOMBEROS, 1.0, type=EventType.DISPATCH)])
        assert result.confidence == 1.0
        assert result.is_official_confirmed is True


class TestTramos:
    def test_los_cortes_son_30_y_60(self) -> None:
        assert level_for(0.0) is ConfidenceLevel.UNSAFE
        assert level_for(0.2999) is ConfidenceLevel.UNSAFE
        # 0.30 exacto ya es "posible"; 0.60 exacto TODAVÍA lo es.
        assert level_for(UNSAFE_THRESHOLD) is ConfidenceLevel.POSSIBLE
        assert level_for(CONFIRMED_THRESHOLD) is ConfidenceLevel.POSSIBLE
        assert level_for(0.6001) is ConfidenceLevel.CONFIRMED
        assert level_for(1.0) is ConfidenceLevel.CONFIRMED

    def test_una_señal_suelta_de_baja_calidad_queda_en_unsafe(self) -> None:
        assert score(citizens(1, confidence=0.10)).level is ConfidenceLevel.UNSAFE
        assert score([signal(EventSource.OTHER, 0.1)]).level is ConfidenceLevel.UNSAFE

    def test_sin_señales_el_tramo_es_unsafe(self) -> None:
        assert score([]).level is ConfidenceLevel.UNSAFE

    def test_el_tramo_es_consistente_con_la_confianza(self) -> None:
        casos = [firms(1), citizens(3), dispatches(1), [CONAF_EN_COMBATE], []]
        for señales in casos:
            result = score(señales)
            assert result.level is level_for(result.confidence)
            assert result.breakdown["level"] == result.level.value


class TestReportesCiudadanos:
    def test_parten_en_la_banda_25_40(self) -> None:
        for entrada, esperado in ((0.10, 0.25), (0.30, 0.30), (0.99, 0.40)):
            result = score(citizens(1, confidence=entrada))
            assert result.confidence == pytest.approx(esperado, abs=1e-6)

    def test_un_reporte_suelto_sin_verificar_no_afirma_nada(self) -> None:
        """0.25 < 0.30: queda en `unsafe`. Se registra, pero el mapa no lo
        sostiene. Es la defensa contra el reporte falso o el spam."""
        assert score(citizens(1, confidence=0.10)).level is ConfidenceLevel.UNSAFE

    def test_suben_progresivamente_con_rendimientos_decrecientes(self) -> None:
        serie = [score(citizens(n)).confidence for n in range(1, 6)]
        saltos = [b - a for a, b in pairwise(serie)]
        assert serie == sorted(serie)
        assert all(saltos)
        assert saltos == sorted(saltos, reverse=True)

    def test_por_muchos_que_sean_no_confirman(self) -> None:
        result = score(citizens(50, confidence=0.60))
        assert result.confidence <= rule_for(EventSource.CITIZEN).ceiling
        assert result.is_official_confirmed is False


class TestAlertasDeSenapred:
    def test_el_estado_de_alerta_es_cierto_al_100(self) -> None:
        result = score([ALERTA_ROJA])
        assert result.alert_confidence == 1.0
        assert result.alert_level == "roja"

    def test_pero_no_confirma_el_fenomeno(self) -> None:
        """La distinción que ordena todo el proyecto.

        SENAPRED declara la respuesta del Estado, no observa el fuego. Además su
        vínculo con este incidente se estableció por comuna, que es texto.
        Marcar `is_official_confirmed` acá sería afirmar que hay fuego en un
        punto que ninguna fuente miró.
        """
        result = score([ALERTA_ROJA])
        assert result.is_official_confirmed is False
        assert result.confidence < 1.0

    def test_con_satelite_sube_mucho_pero_no_a_la_certeza(self) -> None:
        result = score([ALERTA_ROJA, *firms(6, confidence=0.80)])
        assert result.confidence == pytest.approx(UNCONFIRMED_CEILING)
        assert result.breakdown["ceiling_applied"] == "unconfirmed_ceiling"
        assert result.is_official_confirmed is False

    def test_con_conaf_si_llega_a_la_certeza(self) -> None:
        result = score([ALERTA_ROJA, CONAF_EN_COMBATE])
        assert result.confidence == 1.0
        assert result.is_official_confirmed is True
        assert result.alert_level == "roja"

    def test_gana_el_nivel_mas_severo(self) -> None:
        amarilla = signal(
            EventSource.SENAPRED,
            1.0,
            type=EventType.ALERT,
            raw_data={"_alert_level": "amarilla"},
        )
        assert score([amarilla, ALERTA_ROJA]).alert_level == "roja"
        assert score([ALERTA_ROJA, amarilla]).alert_level == "roja"

    def test_sin_alerta_el_eje_queda_en_cero(self) -> None:
        result = score(firms(2))
        assert result.alert_confidence == 0.0
        assert result.alert_level is None


class TestContexto:
    def test_el_clima_no_es_evidencia(self) -> None:
        """Que haya viento y 34 °C no prueba que algo se esté quemando."""
        solo_clima = score(
            [signal(EventSource.WEATHER, 0.9, type=EventType.WEATHER_OBSERVATION)]
        )
        assert solo_clima.confidence == 0.0

        con_clima = score([*firms(2), signal(EventSource.WEATHER, 0.9)])
        assert con_clima.confidence == pytest.approx(score(firms(2)).confidence)


class TestBreakdown:
    def test_la_derivacion_queda_auditable(self) -> None:
        result = score([CONAF_EN_COMBATE, *firms(2), ALERTA_ROJA])
        breakdown = result.breakdown

        assert breakdown["policy_version"] == POLICY_VERSION
        assert breakdown["signals"] == 4
        assert set(breakdown["by_source"]) == {"conaf", "nasa_firms", "senapred"}
        assert breakdown["by_source"]["nasa_firms"]["signals"] == 2
        assert breakdown["by_source"]["conaf"]["confirming"] is True
        assert breakdown["alert"] == {"level": "roja", "confidence": 1.0}

    def test_lista_las_fuentes_ordenadas(self) -> None:
        result = score([*citizens(1), CONAF_EN_COMBATE, *firms(1)])
        assert result.sources == (
            EventSource.CITIZEN,
            EventSource.CONAF,
            EventSource.NASA_FIRMS,
        )

    def test_sin_senales_no_hay_confianza(self) -> None:
        result = score([])
        assert result.confidence == 0.0
        assert result.sources == ()


class TestTipoDelIncidente:
    def test_el_satelite_solo_produce_posible_incendio(self) -> None:
        """Un racimo de anomalías térmicas NO es un incendio forestal.

        Rotularlo `wildfire` sería exactamente el falso positivo que este
        proyecto existe para no cometer.
        """
        assert resolve_type(firms(5)) is IncidentType.POSSIBLE_FIRE

    def test_el_humo_reportado_tampoco(self) -> None:
        assert resolve_type(citizens(4)) is IncidentType.POSSIBLE_FIRE

    def test_la_fuente_confirmatoria_manda(self) -> None:
        assert resolve_type([CONAF_EN_COMBATE, *firms(9)]) is IncidentType.WILDFIRE

    def test_sin_señales_asume_lo_menos_comprometido(self) -> None:
        assert resolve_type([]) is IncidentType.POSSIBLE_FIRE


class TestEstadoDelIncidente:
    def test_solo_una_fuente_confirmatoria_cierra_la_emergencia(self) -> None:
        status, resolved = resolve_status(firms(10))
        assert status is IncidentStatus.ACTIVE
        assert resolved is None

    def test_sigue_el_ciclo_de_vida_de_conaf(self) -> None:
        combate = signal(
            EventSource.CONAF, 1.0, type=EventType.WILDFIRE, raw_data={"estado": "En Combate"}
        )
        controlado = signal(
            EventSource.CONAF,
            1.0,
            type=EventType.WILDFIRE,
            minutes=60,
            raw_data={"estado": "Controlado"},
        )
        extinguido = signal(
            EventSource.CONAF,
            1.0,
            type=EventType.WILDFIRE,
            minutes=180,
            raw_data={"estado": "Extinguido"},
        )

        assert resolve_status([combate])[0] is IncidentStatus.ACTIVE
        assert resolve_status([combate, controlado])[0] is IncidentStatus.CONTROLLED

        status, resolved = resolve_status([extinguido, combate, controlado])
        assert status is IncidentStatus.EXTINGUISHED
        assert resolved == extinguido.timestamp

    def test_una_alerta_no_cambia_el_estado_del_fenomeno(self) -> None:
        assert resolve_status([ALERTA_ROJA])[0] is IncidentStatus.ACTIVE


class TestTitulo:
    def test_incluye_la_comuna_si_se_conoce(self) -> None:
        assert build_title(IncidentType.WILDFIRE, "Casablanca") == (
            "Incendio forestal — Casablanca"
        )

    def test_no_inventa_ubicacion(self) -> None:
        assert build_title(IncidentType.POSSIBLE_FIRE, None) == "Posible incendio"
