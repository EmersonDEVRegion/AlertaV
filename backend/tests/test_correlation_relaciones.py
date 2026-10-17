"""Vínculos entre familias (`services/correlation/relaciones.py`): reglas puras."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import EventType, IncidentType
from app.services.correlation.engine import haversine_m
from app.services.correlation.relaciones import (
    NOMBRE_DESTINO,
    REGLAS,
    Punto,
    emparejar,
    margen,
    tipos_de_destino,
    tipos_de_origen,
)

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
#: Avenida Libertad con 5 Norte, Viña del Mar.
CHOQUE = (-33.01700, -71.55320)
#: ~150 m al norte.
CERCA = (-33.01565, -71.55320)
#: ~450 m al norte.
LEJOS = (-33.01295, -71.55320)


def punto(id_: int, tipo: object, lugar: tuple[float, float], minutos: float) -> Punto:
    return Punto(
        id=id_,
        tipo=str(getattr(tipo, "value", tipo)),
        lat=lugar[0],
        lon=lugar[1],
        hora=T0 + timedelta(minutes=minutos),
    )


def pares(origenes, incidentes=(), eventos=()):
    return emparejar(list(origenes), list(incidentes), list(eventos), distancia=haversine_m)


def test_choque_y_corte_de_luz_cerca_y_despues_se_vinculan():
    choque = punto(1, IncidentType.ACCIDENT, CHOQUE, 0)
    corte = punto(2, IncidentType.POWER_OUTAGE, CERCA, 12)
    [par] = pares([choque], [choque, corte])
    assert par.regla == "choque_corte_luz"
    assert (par.origen_id, par.otro_id, par.otro_es_incidente) == (1, 2, True)
    assert 140 < par.distancia_m < 160
    assert par.desfase_s == 12 * 60


def test_el_corte_publicado_un_poco_antes_tambien_cuenta():
    """La distribuidora a veces publica antes de que llegue el despacho."""
    choque = punto(1, IncidentType.ACCIDENT, CHOQUE, 0)
    corte = punto(2, IncidentType.POWER_OUTAGE, CERCA, -8)
    [par] = pares([choque], [corte])
    assert par.desfase_s == -8 * 60


def test_un_corte_lejos_o_fuera_de_la_ventana_no_se_vincula():
    choque = punto(1, IncidentType.ACCIDENT, CHOQUE, 0)
    assert pares([choque], [punto(2, IncidentType.POWER_OUTAGE, LEJOS, 12)]) == []
    assert pares([choque], [punto(3, IncidentType.POWER_OUTAGE, CERCA, 100)]) == []
    assert pares([choque], [punto(4, IncidentType.POWER_OUTAGE, CERCA, -30)]) == []


def test_incendio_estructural_y_corte_de_luz():
    incendio = punto(1, IncidentType.STRUCTURAL_FIRE, CHOQUE, 0)
    [par] = pares([incendio], [punto(2, IncidentType.POWER_OUTAGE, CERCA, 110)])
    assert par.regla == "incendio_corte_luz"


def test_un_incendio_forestal_o_un_rescate_no_son_origen():
    for tipo in (IncidentType.WILDFIRE, IncidentType.RESCUE, IncidentType.POWER_OUTAGE):
        origen = punto(1, tipo, CHOQUE, 0)
        assert pares([origen], [punto(2, IncidentType.POWER_OUTAGE, CERCA, 5)]) == []


def test_dos_choques_juntos_no_se_vinculan_entre_si():
    """Lo mismo con lo mismo es tarea del motor, no de los vínculos."""
    a = punto(1, IncidentType.ACCIDENT, CHOQUE, 0)
    b = punto(2, IncidentType.ACCIDENT, CERCA, 5)
    assert pares([a, b], [a, b]) == []


def test_deslizamiento_y_corte_de_ruta_que_es_una_senal():
    derrumbe = punto(1, IncidentType.LANDSLIDE, CHOQUE, 0)
    corte = punto(77, EventType.ROAD_CLOSURE, LEJOS, -90)
    [par] = pares([derrumbe], [], [corte])
    assert par.regla == "deslizamiento_corte_ruta"
    assert par.otro_es_incidente is False
    assert par.otro_id == 77


def test_tipos_y_margen_salen_de_las_reglas():
    assert tipos_de_origen() == {
        IncidentType.ACCIDENT,
        IncidentType.STRUCTURAL_FIRE,
        IncidentType.LANDSLIDE,
        IncidentType.FLOOD,
    }
    incidentes, eventos = tipos_de_destino()
    assert incidentes == {IncidentType.POWER_OUTAGE}
    assert eventos == {EventType.ROAD_CLOSURE}
    assert margen() == timedelta(hours=3)


def test_cada_destino_tiene_nombre_para_la_ficha():
    _, eventos = tipos_de_destino()
    destinos = {t.value for r in REGLAS for t in r.destino_incidentes} | {e.value for e in eventos}
    origenes = {t.value for t in tipos_de_origen()}
    assert destinos | origenes <= set(NOMBRE_DESTINO)
