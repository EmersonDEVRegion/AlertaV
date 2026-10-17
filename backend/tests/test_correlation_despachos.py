"""La regla «mismo llamado» con despachos reales de producción.

Los casos salen de los 23 incidentes con más de un despacho de los 30 días
anteriores al 17-10-2026: los refuerzos repiten al menos una calle; los
llamados distintos que el motor había fundido, nunca.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import EventSource, EventType
from app.services.correlation.despachos import (
    calles_compartidas,
    compatibles,
    mismo_llamado,
    partir_por_llamado,
)
from app.services.correlation.perfiles import PERFIL_ESTRUCTURAL, PERFILES, perfil_para

T0 = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
RADIO = 500.0
BRECHA = timedelta(hours=3)


@dataclass(frozen=True)
class S:
    lat: float
    lon: float
    minutos: int
    calles: tuple[str, ...] = ()
    source: EventSource = EventSource.BOMBEROS
    type: EventType = EventType.STRUCTURAL_FIRE

    @property
    def timestamp(self) -> datetime:
        return T0 + timedelta(minutes=self.minutos)


def llamado(a: S, b: S) -> bool:
    return mismo_llamado(a, b, radio_m=RADIO, brecha=BRECHA)


# INC 1365, Quilpué: el refuerzo CJ-3 sale a los 22 min a otra esquina de la misma
# calle, a 870 m.
QUILPUE_1 = S(-33.0361048, -71.4056021, 44, ("MATEO DE TORO Y ZAMBRANO", "ALMIRANTE GOMEZ CARRENO"))
QUILPUE_2 = S(-33.0363881, -71.4149094, 66, ("CALLE 3", "MATEO DE TORO Y ZAMBRANO"))
# INC 1366, Valparaíso: un 1-1 y, cinco horas y media después, un 2-1 a 437 m.
URRIOLA = S(-33.0409796, -71.6284078, 83, ("URRIOLA", "ALVARO BESA"))
SERRANO = S(-33.037337, -71.6302049, 409, ("SERRANO", "ALMIRANTE GOÑI"), type=EventType.WILDFIRE)
# INC 1358, Viña: tres despachos a Donegal / Dublin.
DONEGAL = S(-33.0, -71.5, 0, ("DONEGAL", "DUBLIN"))


class TestMismoLlamado:
    def test_el_refuerzo_que_sale_enseguida_por_la_misma_calle_es_el_mismo_llamado(self) -> None:
        assert llamado(QUILPUE_1, QUILPUE_2)

    def test_la_misma_calle_a_870_m_horas_despues_es_otro_llamado(self) -> None:
        tarde = S(QUILPUE_2.lat, QUILPUE_2.lon, QUILPUE_1.minutos + 90, QUILPUE_2.calles)
        assert not llamado(QUILPUE_1, tarde)

    def test_otras_calles_cerca_son_otro_llamado(self) -> None:
        cerca = S(URRIOLA.lat + 0.002, URRIOLA.lon, URRIOLA.minutos + 20, SERRANO.calles)
        assert not llamado(URRIOLA, cerca)

    def test_fuera_de_la_brecha_es_otro_llamado(self) -> None:
        assert not llamado(URRIOLA, SERRANO)  # 326 minutos

    def test_otras_calles_mismo_minuto_son_otro_llamado(self) -> None:
        otra = S(URRIOLA.lat + 0.001, URRIOLA.lon, URRIOLA.minutos, ("SERRANO", "COCHRANE"))
        assert not llamado(URRIOLA, otra)

    def test_la_misma_esquina_repetida_es_el_mismo_llamado(self) -> None:
        assert llamado(DONEGAL, S(DONEGAL.lat, DONEGAL.lon, 8, ("DONEGAL", "DUBLIN")))

    def test_la_misma_calle_lejos_y_tarde_es_otro_llamado(self) -> None:
        """INC 879: dos 5-1 en la Ruta 68, kilómetros distintos, a 927 m y 96 min."""
        a = S(-33.10984, -71.55851, 0, ("RUTA 68", "SECTOR HITO A INFRAESTRUCTURA KM 101.150"))
        b = S(-33.10648, -71.54941, 96, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        assert not llamado(a, b)

    def test_la_misma_esquina_repetida_lejos_es_error_de_geocodificacion(self) -> None:
        """INC 879: «PASARELA PLACILLA KM 99» repetido a los 7 min cayó a 927 m."""
        a = S(-33.10648, -71.54941, 96, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        b = S(-33.10984, -71.55851, 103, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        assert llamado(a, b)

    def test_la_misma_esquina_horas_despues_es_otro_llamado(self) -> None:
        assert not llamado(DONEGAL, S(DONEGAL.lat, DONEGAL.lon, 240, ("DONEGAL", "DUBLIN")))

    def test_sin_calles_no_hay_como_saber_y_no_se_juntan(self) -> None:
        assert not llamado(S(-33.0, -71.5, 0), S(-33.0, -71.5, 5))

    def test_solo_entre_despachos(self) -> None:
        firms = S(-33.0, -71.5, 0, ("DONEGAL",), source=EventSource.NASA_FIRMS)
        assert not llamado(DONEGAL, firms)


class TestCallesCompartidas:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (("ALVAREZ",), ("DOCTOR ALVAREZ",)),
            (("DOCTOR ALVAREZ",), ("ALVAREZ",)),
            (("AVENIDA ALEMANIA",), ("Alemania",)),
            (("UNO NORTE",), ("1 NORTE",)),
            (("ALMIRANTE GOÑI",), ("ALMIRANTE GONI",)),
        ],
    )
    def test_calzan_en_los_dos_sentidos(self, a, b) -> None:
        assert calles_compartidas(a, b)

    def test_andres_bello_no_es_bello(self) -> None:
        assert not calles_compartidas(("ANDRES BELLO",), ("PEDRO BELLO",))


class TestCompatibles:
    def test_sin_despachos_de_un_lado_decide_la_geometria(self) -> None:
        firms = S(-33.0, -71.5, 0, source=EventSource.NASA_FIRMS)
        assert compatibles([firms], [URRIOLA], radio_m=RADIO, brecha=BRECHA)
        assert compatibles([URRIOLA], [firms], radio_m=RADIO, brecha=BRECHA)

    def test_basta_un_par_del_mismo_llamado(self) -> None:
        assert compatibles([QUILPUE_2], [URRIOLA, QUILPUE_1], radio_m=RADIO, brecha=BRECHA)

    def test_ningun_par_del_mismo_llamado(self) -> None:
        assert not compatibles([SERRANO], [URRIOLA], radio_m=RADIO, brecha=BRECHA)


class TestPartirPorLlamado:
    def _partir(self, miembros, radio_ajenas=1500.0):
        return partir_por_llamado(
            miembros, radio_m=RADIO, brecha=BRECHA, radio_ajenas_m=radio_ajenas
        )

    def test_los_andes_cuatro_llamados_distintos(self) -> None:
        """INC 1158: clima, eléctrico, árboles y persona encerrada, en 49 min."""
        miembros = [
            S(-32.82745, -70.61195, 0, ("DOCTOR OSVALDO RODRIGUEZ",), type=EventType.OTHER),
            S(-32.83075, -70.59832, 9, ("TRES CARRERA", "DIEGO DE ALMAGRO"), type=EventType.OTHER),
            S(-32.83776, -70.60497, 47, ("OHIGGINS", "MEMBRILLAR"), type=EventType.OTHER),
            S(-32.82875, -70.59544, 49, ("AVENIDA HERMANOS CLARK",), type=EventType.RESCUE),
        ]
        assert len(self._partir(miembros)) == 4

    def test_los_refuerzos_quedan_juntos(self) -> None:
        a = S(-33.0, -71.5, 0, ("ESTADIO", "JULIO ZEGERS"))
        b = S(-33.0, -71.5, 3, ("ESTADIO", "JULIO ZEGERS"))
        c = S(-33.0, -71.5, 3, ("ESTADIO", "JULIO ZEGERS"))
        assert self._partir([a, b, c]) == [[a, b, c]]

    def test_la_cadena_de_refuerzos_es_un_solo_llamado(self) -> None:
        """A y C no comparten calle, pero los dos la comparten con B."""
        a = S(-33.0, -71.5, 0, ("UNO", "DOS"))
        b = S(-33.0, -71.5, 10, ("DOS", "TRES"))
        c = S(-33.0, -71.5, 20, ("TRES", "CUATRO"))
        assert len(self._partir([a, b, c])) == 1

    def test_otra_fuente_va_con_el_llamado_mas_cercano(self) -> None:
        firms = S(SERRANO.lat, SERRANO.lon + 0.0005, 400, source=EventSource.NASA_FIRMS)
        tramos = self._partir([URRIOLA, SERRANO, firms])
        assert [URRIOLA] in tramos
        assert sorted(next(t for t in tramos if SERRANO in t), key=id) == sorted(
            [SERRANO, firms], key=id
        )

    def test_otra_fuente_lejos_de_todo_despacho_queda_aparte(self) -> None:
        """Un píxel a 1,2 km de una casa que se quema no es esa casa."""
        firms = S(URRIOLA.lat + 0.011, URRIOLA.lon, 90, source=EventSource.NASA_FIRMS)
        tramos = self._partir([URRIOLA, firms], radio_ajenas=PERFIL_ESTRUCTURAL.radio_m)
        assert tramos == [[URRIOLA], [firms]]

    def test_sin_despachos_vuelve_entero(self) -> None:
        a = S(-33.0, -71.5, 0, source=EventSource.NASA_FIRMS)
        b = S(-33.01, -71.5, 5, source=EventSource.CONAF)
        assert self._partir([a, b]) == [[a, b]]


class TestPerfilEstructural:
    def test_solo_estructural(self) -> None:
        assert perfil_para("fire", [EventType.STRUCTURAL_FIRE]) is PERFIL_ESTRUCTURAL

    def test_con_algo_forestal_manda_fire(self) -> None:
        tipos = [EventType.STRUCTURAL_FIRE, EventType.WILDFIRE]
        assert perfil_para("fire", tipos) is PERFILES["fire"]

    def test_satelite_solo_es_fire(self) -> None:
        assert perfil_para("fire", [EventType.THERMAL_ANOMALY]) is PERFILES["fire"]

    def test_otras_familias_no_cambian(self) -> None:
        assert perfil_para("traffic", [EventType.STRUCTURAL_FIRE]) is PERFILES["traffic"]


class TestKilometros:
    def test_kilometros_distintos_en_el_mismo_punto_son_otro_llamado(self) -> None:
        """Replay del 13-10: «KILOMETRO 3» y «KILOMETRO 7» geocodificados igual."""
        a = S(
            -33.08109,
            -71.54646,
            0,
            ("Ruta las Palmas", "Kilometro 3"),
            EventSource.BOMBEROS,
            EventType.ACCIDENT,
        )
        b = S(
            -33.08109,
            -71.54646,
            60,
            ("Ruta las Palmas", "Kilometro 7"),
            EventSource.BOMBEROS,
            EventType.ACCIDENT,
        )
        assert not llamado(a, b)

    def test_el_mismo_kilometro_sigue_siendo_el_mismo_llamado(self) -> None:
        a = S(-33.1, -71.5, 0, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        b = S(-33.1, -71.5, 7, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        assert llamado(a, b)

    def test_el_kilometro_de_101_150_no_es_el_99(self) -> None:
        a = S(-33.1, -71.5, 0, ("RUTA 68", "SECTOR HITO A INFRAESTRUCTURA VIAL KM 101.150"))
        b = S(-33.1, -71.5, 20, ("RUTA 68", "SECTOR PASARELA PLACILLA KM 99.000"))
        assert not llamado(a, b)

    @pytest.mark.parametrize(
        ("calles", "km"),
        [
            (("Ruta las Palmas", "Kilometro 4 -"), 4.0),
            (("RUTA 68", "SECTOR X KM 101.150"), 101.15),
            (("Ruta F-30-E", "km. 12,5"), 12.5),
            (("ALVAREZ", "QUILLOTA"), None),
        ],
    )
    def test_lee_el_kilometro(self, calles, km) -> None:
        from app.services.correlation.despachos import kilometro

        assert kilometro(calles) == km
