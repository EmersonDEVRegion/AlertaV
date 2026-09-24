"""Perfiles por familia del motor de correlación (auditoría 2026-09-23).

La decisión —qué radio, qué brecha, qué se le pide al repositorio— se prueba acá
con un repositorio falso. El SQL (eps por familia, ventana por ingesta, filtro
temporal, polígonos) se prueba contra PostGIS en `test_integracion_pg.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.models.enums import EventSource, EventType, family_of_event
from app.repositories.incident_repository import ClusteredEvent
from app.services.correlation.communes import extract_commune
from app.services.correlation.engine import CorrelationEngine, CorrelationPass
from app.services.correlation.perfiles import (
    EDAD_MAX_GLOBAL,
    HOLGURA_M,
    PERFILES,
    holgura,
    partir_por_brecha,
    perfil,
)

AHORA = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
PUNTO = (-33.0170, -71.5532)


def choque(
    event_id: int,
    *,
    hace: timedelta,
    precision: str | None = None,
    event_type: EventType = EventType.ACCIDENT,
    cluster_id: int = 0,
) -> ClusteredEvent:
    return ClusteredEvent(
        event_id=event_id,
        cluster_id=cluster_id,
        lat=PUNTO[0],
        lon=PUNTO[1],
        confidence=0.6,
        timestamp=AHORA - hace,
        source=EventSource.TRANSPORTE_INFORMA,
        type=event_type,
        family=family_of_event(event_type),
        precision=precision,
    )


# --- Los perfiles --------------------------------------------------------------


def test_cada_familia_del_motor_tiene_perfil():
    from app.models.enums import INCIDENT_FAMILY

    assert set(INCIDENT_FAMILY.values()) <= set(PERFILES)


def test_una_familia_desconocida_usa_el_perfil_por_defecto():
    assert perfil("inventada") is PERFILES["other"]
    assert perfil(None) is PERFILES["other"]


def test_un_choque_se_agrupa_mas_cerca_que_un_incendio_o_un_corte():
    assert perfil("traffic").radio_m < perfil("fire").radio_m <= perfil("power").radio_m


def test_la_edad_maxima_global_cubre_la_de_todas_las_familias():
    assert all(p.edad_max <= EDAD_MAX_GLOBAL for p in PERFILES.values())
    assert all(p.edad_max >= timedelta(hours=settings.CORRELATION_WINDOW_HOURS) for p in PERFILES.values())


@pytest.mark.parametrize(
    ("precisiones", "esperada"),
    [
        ([None, None], 0.0),
        (["intersection"], 0.0),
        (["street", None], HOLGURA_M["street"]),
        (["street", "sector"], HOLGURA_M["sector"]),
        (["desconocida"], 0.0),
        ([], 0.0),
    ],
)
def test_la_holgura_es_la_del_punto_menos_preciso(precisiones, esperada):
    assert holgura(precisiones) == esperada


def test_partir_por_brecha_corta_donde_se_abre_el_tiempo():
    """A–B–C encadenados por DBSCAN, con un hueco de 3 h entre B y C."""
    a = choque(1, hace=timedelta(hours=4))
    b = choque(2, hace=timedelta(hours=3, minutes=30))
    c = choque(3, hace=timedelta(minutes=10))

    tramos = partir_por_brecha([c, a, b], timedelta(hours=2))

    assert [[m.event_id for m in t] for t in tramos] == [[1, 2], [3]]


def test_partir_por_brecha_sin_huecos_devuelve_el_racimo_entero():
    miembros = [choque(i, hace=timedelta(minutes=10 * i)) for i in range(1, 5)]
    assert len(partir_por_brecha(miembros, timedelta(hours=2))) == 1
    assert partir_por_brecha([], timedelta(hours=2)) == []


# --- Lo que el motor le pide al repositorio -----------------------------------


class RepoFalso:
    def __init__(self, racimos: list[ClusteredEvent]) -> None:
        self.racimos = racimos
        self.pedido_racimos: dict[str, Any] = {}
        self.busquedas: list[dict[str, Any]] = []
        self.creados: list[Any] = []

    async def cluster_unassigned_events(self, **kwargs: Any) -> list[ClusteredEvent]:
        self.pedido_racimos = kwargs
        return self.racimos

    async def find_nearest_open_incident(self, **kwargs: Any) -> None:
        self.busquedas.append(kwargs)
        return None

    async def find_open_incident_by_sector(self, **_: Any) -> None:
        return None

    async def create_incident(self, **values: Any) -> Any:
        incidente = SimpleNamespace(id=100 + len(self.creados), **values)
        self.creados.append(incidente)
        return incidente

    async def link_events(self, *, incident_id: int, links: list[Any]) -> int:
        return len(links)

    async def assign_events_to_incident(self, **_: Any) -> int:
        return 0


def motor(repo: RepoFalso, *, perfiles: bool) -> CorrelationEngine:
    engine = CorrelationEngine(session=None, perfiles=perfiles)  # type: ignore[arg-type]
    engine.repo = repo  # type: ignore[assignment]

    async def sin_refrescar(_incident: Any, *, now: datetime) -> None:
        return None

    engine._refresh = sin_refrescar  # type: ignore[method-assign]
    return engine


def test_con_perfiles_el_dbscan_recibe_un_radio_y_una_edad_por_familia():
    repo = RepoFalso([])
    asyncio.run(motor(repo, perfiles=True)._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    assert repo.pedido_racimos["radios"]["traffic"] == PERFILES["traffic"].radio_m
    assert repo.pedido_racimos["edades_desde"]["fire"] == AHORA - PERFILES["fire"].edad_max


def test_sin_perfiles_la_consulta_es_la_de_antes():
    repo = RepoFalso([choque(1, hace=timedelta(minutes=5))])
    asyncio.run(motor(repo, perfiles=False)._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    assert "radios" not in repo.pedido_racimos
    assert "edades_desde" not in repo.pedido_racimos
    (busqueda,) = repo.busquedas
    assert busqueda["radius_m"] == settings.CORRELATION_RADIUS_M
    assert "desde" not in busqueda


def test_la_busqueda_del_incidente_lleva_radio_con_holgura_y_ventana_temporal():
    repo = RepoFalso(
        [
            choque(1, hace=timedelta(minutes=50), precision="street"),
            choque(2, hace=timedelta(minutes=20)),
        ]
    )
    asyncio.run(motor(repo, perfiles=True)._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    (busqueda,) = repo.busquedas
    p = PERFILES["traffic"]
    assert busqueda["radius_m"] == p.radio_m + HOLGURA_M["street"]
    assert busqueda["desde"] == AHORA - timedelta(minutes=50) - p.brecha
    assert busqueda["hasta"] == AHORA - timedelta(minutes=20) + p.brecha
    assert busqueda["family"] == "traffic"


def test_un_racimo_con_un_hueco_de_horas_abre_dos_incidentes():
    """C2: el encadenamiento de DBSCAN ya no junta la mañana con la tarde."""
    repo = RepoFalso(
        [choque(1, hace=timedelta(hours=5)), choque(2, hace=timedelta(minutes=10))]
    )
    resultado = CorrelationPass(started_at=AHORA)
    asyncio.run(motor(repo, perfiles=True)._step_a_spatial(resultado, now=AHORA))

    assert len(repo.creados) == 2
    assert resultado.clusters == 1
    assert resultado.clusters_split == 1


def test_sin_perfiles_el_mismo_racimo_abre_uno_solo():
    repo = RepoFalso(
        [choque(1, hace=timedelta(hours=5)), choque(2, hace=timedelta(minutes=10))]
    )
    asyncio.run(motor(repo, perfiles=False)._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))
    assert len(repo.creados) == 1


def test_el_interruptor_viene_encendido_por_defecto():
    assert settings.CORRELATION_PERFILES is True
    assert CorrelationEngine(session=None).perfiles is True  # type: ignore[arg-type]


# --- La comuna -------------------------------------------------------------------


def test_la_comuna_geocodificada_se_lee_con_su_nombre_canonico():
    raw = {"_geocoding": {"comuna": "Vina del Mar", "precision": "street"}}
    assert extract_commune(commune=None, raw_data=raw, text=None) == "Viña del Mar"


def test_el_campo_de_la_fuente_gana_a_la_geocodificada():
    raw = {"comuna": "Quilpué", "_geocoding": {"comuna": "Villa Alemana"}}
    assert extract_commune(commune=None, raw_data=raw, text=None) == "Quilpué"


def test_una_comuna_geocodificada_desconocida_se_conserva_tal_cual():
    raw = {"_geocoding": {"comuna": "Santiago"}}
    assert extract_commune(commune=None, raw_data=raw, text=None) == "Santiago"


def test_sin_tabla_de_poligonos_no_se_consulta_nada():
    class SinTabla:
        consultas = 0

        async def comunas_disponibles(self) -> bool:
            return False

        async def comuna_por_punto(self, *_: Any) -> tuple[str, str]:
            SinTabla.consultas += 1
            return ("X", "Y")

    engine = CorrelationEngine(session=None)  # type: ignore[arg-type]
    engine.repo = SinTabla()  # type: ignore[assignment]

    assert asyncio.run(engine._commune_by_polygon(-33.0, -71.5)) == (None, None)
    assert asyncio.run(engine._commune_by_polygon(None, None)) == (None, None)
    assert SinTabla.consultas == 0
