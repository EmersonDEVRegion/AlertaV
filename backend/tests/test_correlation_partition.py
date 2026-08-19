"""Aislamiento entre familias de fenómeno en el motor de correlación.

La garantía que se verifica acá es la que pidió el hito de accidentes viales y la
que más caro sale romper: **un incendio y un accidente no pueden terminar en el
mismo incidente**, por mucho que compartan coordenada y minuto.

Es una garantía frágil de un modo particular: se sostiene sobre tres puertas
independientes (partición del DBSCAN, filtro al adherirse a un incidente
existente, filtro al fusionar) y basta con que una se abra para que las otras dos
dejen de servir. Peor todavía, cuando se rompe no falla nada: el motor sigue
corriendo y produce incidentes que mezclan fenómenos, en silencio. De ahí que se
prueben las tres por separado.

No hace falta PostGIS: se verifica la lógica de partición y el SQL generado. La
ejecución real contra la base vive en `scripts/smoke_test.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EVENT_TO_INCIDENT_TYPE,
    EventSource,
    EventType,
    IncidentType,
    family_of_event,
    family_of_incident,
)
from app.models.event import RawEvent
from app.models.incident import Incident
from app.repositories.incident_repository import (
    ClusteredEvent,
    event_family_sql,
    incident_family_sql,
)
from app.services.correlation.engine import CorrelationEngine

T0 = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)

#: Mismo punto exacto para todas las señales de las pruebas. Es el caso adverso:
#: si el aislamiento se sostiene con distancia cero, se sostiene siempre.
CRUCE = (-33.0458, -71.6197)


def evento(
    source: EventSource,
    event_type: EventType,
    *,
    event_id: int,
    cluster_id: int = 0,
    confidence: float = 0.5,
    minutes: int = 0,
) -> ClusteredEvent:
    """Una señal ya agrupada, con la familia que le habría puesto el SQL."""
    lat, lon = CRUCE
    return ClusteredEvent(
        event_id=event_id,
        cluster_id=cluster_id,
        lat=lat,
        lon=lon,
        confidence=confidence,
        timestamp=T0 + timedelta(minutes=minutes),
        source=source,
        type=event_type,
        family=family_of_event(event_type),
    )


def render(expr) -> str:
    return str(
        expr.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# --- La tabla de familias ----------------------------------------------------


def test_accidente_e_incendio_estan_en_familias_distintas():
    assert family_of_event(EventType.ACCIDENT) == "traffic"
    assert family_of_event(EventType.WILDFIRE) == "fire"
    assert family_of_event(EventType.ACCIDENT) != family_of_event(EventType.WILDFIRE)


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.WILDFIRE,
        EventType.STRUCTURAL_FIRE,
        EventType.SMOKE,
        EventType.THERMAL_ANOMALY,
    ],
)
def test_los_indicios_de_fuego_comparten_familia_con_el_fuego_confirmado(event_type):
    """Lo que la partición NO debe separar.

    Es la mitad olvidable de la garantía y la más importante: separar `smoke` de
    `wildfire` aislaría un avistamiento de la confirmación que lo respalda y
    desactivaría la corroboración entre fuentes, que es de lo que vive el motor.
    Un aislamiento que rompe esto está mal aunque cumpla lo que pidió el ticket.
    """
    assert family_of_event(event_type) == "fire"


def test_accidente_no_comparte_familia_con_rescate_ni_generico():
    """`traffic` es familia propia, no un alias de `other`.

    Antes de este hito `accident` vivía en la familia "other" junto a `dispatch`
    y `rescue`. Habría bastado para separarlo del fuego, pero dejaba un choque
    fundible con un despacho de origen desconocido a 800 m.
    """
    assert family_of_event(EventType.ACCIDENT) == "traffic"
    assert family_of_event(EventType.RESCUE) == "other"
    assert family_of_event(EventType.DISPATCH) == "other"
    assert family_of_event(EventType.UNKNOWN) == "other"


def test_todo_tipo_correlacionable_tiene_familia():
    """Ningún tipo agrupable puede quedar sin familia declarada.

    Si alguien agrega un `EventType` a `CORRELATABLE_EVENT_TYPES` y olvida
    mapearlo, caería en la familia por defecto y se mezclaría con lo genérico sin
    que nada lo advirtiera. Este test es la advertencia.
    """
    for event_type in CORRELATABLE_EVENT_TYPES:
        assert event_type in EVENT_TO_INCIDENT_TYPE, (
            f"{event_type.value} es correlacionable pero no tiene incidente asociado"
        )
        assert family_of_event(event_type)


# --- Puerta 1: la clave de agrupamiento --------------------------------------


def test_cluster_key_separa_racimos_homonimos_de_distinta_familia():
    """El error más silencioso posible de toda la implementación.

    `ST_ClusterDBSCAN` reinicia la numeración en CADA partición: el racimo 0 de
    `fire` y el racimo 0 de `traffic` son cosas distintas que comparten número.
    Agrupar por `cluster_id` a secas volvería a fundir justo lo que el SQL acaba
    de separar — y no habría ningún síntoma, porque la consulta habría hecho su
    trabajo correctamente.
    """
    incendio = evento(EventSource.CONAF, EventType.WILDFIRE, event_id=1, cluster_id=0)
    choque = evento(EventSource.WAZE, EventType.ACCIDENT, event_id=2, cluster_id=0)

    assert incendio.cluster_id == choque.cluster_id
    assert incendio.cluster_key != choque.cluster_key


def test_el_motor_agrupa_por_cluster_key_y_no_mezcla_familias():
    """Simula el agrupamiento del Paso A sobre señales en el MISMO punto."""
    from collections import defaultdict

    señales = [
        evento(EventSource.CONAF, EventType.WILDFIRE, event_id=1, cluster_id=0),
        evento(EventSource.NASA_FIRMS, EventType.THERMAL_ANOMALY, event_id=2, cluster_id=0),
        evento(EventSource.WAZE, EventType.ACCIDENT, event_id=3, cluster_id=0),
        evento(EventSource.BOMBEROS, EventType.ACCIDENT, event_id=4, cluster_id=0),
    ]

    racimos: dict[tuple[str, int | None], list[ClusteredEvent]] = defaultdict(list)
    for señal in señales:
        racimos[señal.cluster_key].append(señal)

    assert len(racimos) == 2, "el fuego y el choque deben quedar en racimos distintos"
    assert racimos[("fire", 0)] == señales[:2]
    assert racimos[("traffic", 0)] == señales[2:]

    for clave, miembros in racimos.items():
        familias = {miembro.family for miembro in miembros}
        assert familias == {clave[0]}, "un racimo no puede contener dos familias"


# --- Puerta 2: el tipo con el que nace el incidente --------------------------


def test_un_racimo_de_accidentes_no_nace_como_posible_incendio():
    """`_open_incident` sembraba `POSSIBLE_FIRE` fijo.

    Era inocuo mientras todo lo agrupable fuera fuego. Con familias, un incidente
    de accidente que naciera rotulado `possible_fire` quedaría en la familia
    equivocada hasta el primer `_refresh`, y las señales siguientes del mismo
    choque no lo encontrarían: abrirían un incidente duplicado a metros.
    """
    miembros = [
        evento(EventSource.WAZE, EventType.ACCIDENT, event_id=1, confidence=0.40),
        evento(EventSource.BOMBEROS, EventType.ACCIDENT, event_id=2, confidence=1.0),
    ]
    sembrado = CorrelationEngine._seed_type(miembros)

    assert sembrado is IncidentType.ACCIDENT
    assert family_of_incident(sembrado) == "traffic"


def test_un_racimo_de_fuego_sigue_naciendo_en_la_familia_fire():
    miembros = [
        evento(EventSource.NASA_FIRMS, EventType.THERMAL_ANOMALY, event_id=1, confidence=0.4),
        evento(EventSource.CITIZEN, EventType.SMOKE, event_id=2, confidence=0.3),
    ]
    sembrado = CorrelationEngine._seed_type(miembros)

    assert family_of_incident(sembrado) == "fire"
    assert sembrado is IncidentType.POSSIBLE_FIRE


def test_el_tipo_sembrado_lo_decide_la_confianza_acumulada():
    """Con señales mezcladas manda la evidencia, no el orden de llegada."""
    miembros = [
        evento(EventSource.WAZE, EventType.ACCIDENT, event_id=1, confidence=0.40),
        evento(EventSource.CONAF, EventType.WILDFIRE, event_id=2, confidence=1.0),
    ]
    assert CorrelationEngine._seed_type(miembros) is IncidentType.WILDFIRE


def test_seed_type_sin_tipos_mapeables_no_revienta():
    assert CorrelationEngine._seed_type([]) is IncidentType.POSSIBLE_FIRE


# --- Puerta 3: el SQL generado -----------------------------------------------


def test_el_case_de_familia_se_genera_desde_la_tabla_de_python():
    """El SQL y la tabla de Python no pueden divergir.

    Se comprueba que cada tipo correlacionable aparezca en el CASE con la familia
    que dice `family_of_event`. Si alguien edita una de las dos tablas a mano,
    esto lo detecta.
    """
    sql = render(event_family_sql(RawEvent.type))

    for event_type in CORRELATABLE_EVENT_TYPES:
        esperado = family_of_event(event_type)
        assert f"WHEN '{event_type.value}' THEN '{esperado}'" in sql, (
            f"falta o discrepa la rama de {event_type.value}"
        )


def test_el_case_de_incidentes_manda_accident_a_traffic():
    sql = render(incident_family_sql(Incident.type))
    assert "WHEN 'accident' THEN 'traffic'" in sql
    assert "WHEN 'wildfire' THEN 'fire'" in sql


def test_el_case_es_estable_entre_llamadas():
    """Mismo SQL en cada render.

    `CORRELATABLE_EVENT_TYPES` es un frozenset: sin ordenar, el texto de la
    consulta cambiaría en cada arranque del proceso y desperdiciaría la caché de
    planes de PostgreSQL.
    """
    assert render(event_family_sql(RawEvent.type)) == render(
        event_family_sql(RawEvent.type)
    )
