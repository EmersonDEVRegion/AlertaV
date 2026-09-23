"""Vínculo por sector en el motor de correlación.

El caso que lo motivó: el 2026-09-03 la nota de Pura Noticia y un tuit contaron
el mismo incendio de Miraflores Alto, cayeron a 2,5 km uno del otro y el radio de
1500 m no podía unirlos. Lo que las dos fuentes sí decían igual era el sector.

Como el resto del motor, la orquestación real se verifica contra PostGIS en
`scripts/smoke_test.py`. Acá se cubre la decisión —cuándo se une por sector,
con qué método y con qué ventana— con un repositorio falso, y el SQL generado.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import postgresql

from app.models.enums import EventSource, EventType, LinkMethod, family_of_event
from app.repositories.incident_repository import (
    ClusteredEvent,
    IncidentRepository,
    SectorSignal,
)
from app.services.correlation.engine import (
    LINK_CONFIDENCE_SECTOR,
    CorrelationEngine,
    CorrelationPass,
)

AHORA = datetime(2026, 9, 3, 16, 30, tzinfo=UTC)
CLAVE = "vina del mar|miraflores alto"

#: Puntos de PRUEBA, a ~2,5 km: la distancia que separó a las dos fuentes.
PUNTO_TUIT = (-33.0330, -71.5720)
PUNTO_NOTA = (-33.0301, -71.5452)


def render(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def señal(
    event_id: int,
    lat_lon: tuple[float, float],
    *,
    event_type: EventType = EventType.STRUCTURAL_FIRE,
    sector_clave: str | None = CLAVE,
    source: EventSource = EventSource.MEDIA,
) -> ClusteredEvent:
    return ClusteredEvent(
        event_id=event_id,
        cluster_id=0,
        lat=lat_lon[0],
        lon=lat_lon[1],
        confidence=0.6,
        timestamp=AHORA - timedelta(minutes=30),
        source=source,
        type=event_type,
        family=family_of_event(event_type),
        sector_clave=sector_clave,
    )


class RepoFalso:
    """Lo mínimo del repositorio que tocan el Paso A y el vínculo por sector."""

    def __init__(self, *, existentes: dict[tuple[str, str], Any] | None = None) -> None:
        #: (familia, clave) → incidente que `find_open_incident_by_sector` devuelve.
        self.existentes = existentes or {}
        self.racimos: list[ClusteredEvent] = []
        self.sin_punto: list[SectorSignal] = []
        self.creados: list[Any] = []
        self.enlaces: list[tuple[int, list[Any]]] = []
        self.asignados: list[tuple[int, list[int]]] = []
        self.busquedas_por_sector: list[dict[str, Any]] = []

    async def cluster_unassigned_events(self, **_: Any) -> list[ClusteredEvent]:
        return self.racimos

    async def find_nearest_open_incident(self, **_: Any) -> None:
        return None  # nada a menos del radio: es el caso de Miraflores Alto

    async def find_open_incident_by_sector(
        self, *, sector_clave: str, family: str, since: datetime
    ) -> Any:
        self.busquedas_por_sector.append(
            {"sector_clave": sector_clave, "family": family, "since": since}
        )
        return self.existentes.get((family, sector_clave))

    async def unlocated_sector_signals(self, **_: Any) -> list[SectorSignal]:
        return self.sin_punto

    async def create_incident(self, **values: Any) -> Any:
        incidente = SimpleNamespace(id=100 + len(self.creados), **values)
        self.creados.append(incidente)
        return incidente

    async def link_events(self, *, incident_id: int, links: list[Any]) -> int:
        self.enlaces.append((incident_id, list(links)))
        return len(links)

    async def assign_events_to_incident(
        self, *, incident_id: int, event_ids: list[int], processed_at: datetime
    ) -> int:
        self.asignados.append((incident_id, list(event_ids)))
        return len(event_ids)


def motor(repo: RepoFalso) -> tuple[CorrelationEngine, list[int]]:
    engine = CorrelationEngine(session=None, sector_window_hours=3)  # type: ignore[arg-type]
    engine.repo = repo  # type: ignore[assignment]
    refrescados: list[int] = []

    async def refrescar(incident: Any, *, now: datetime) -> None:
        refrescados.append(incident.id)

    engine._refresh = refrescar  # type: ignore[method-assign]
    return engine, refrescados


def incendio_existente() -> Any:
    return SimpleNamespace(id=7, lat=PUNTO_TUIT[0], lon=PUNTO_TUIT[1])


# --- Paso A: un racimo con punto, lejos, que nombra el mismo sector -----------


def test_la_nota_lejana_se_une_al_incendio_del_mismo_sector() -> None:
    incendio = incendio_existente()
    repo = RepoFalso(existentes={("fire", CLAVE): incendio})
    repo.racimos = [señal(2, PUNTO_NOTA)]
    engine, refrescados = motor(repo)
    resultado = CorrelationPass(started_at=AHORA)

    asyncio.run(engine._step_a_spatial(resultado, now=AHORA))

    assert repo.creados == [], "no se abre un segundo incidente para la misma casa"
    assert resultado.clusters_joined_by_sector == 1
    assert resultado.sector_links == 1
    assert resultado.spatial_links == 0

    (incident_id, links), = repo.enlaces
    assert incident_id == incendio.id
    (link,) = links
    assert link.link_method is LinkMethod.SECTOR_TEXT
    assert link.link_confidence == LINK_CONFIDENCE_SECTOR
    assert link.note == f"sector: {CLAVE}"
    # La distancia se guarda igual: dice cuánto discrepaban los dos puntos.
    assert 2_000 < link.distance_m < 3_000
    assert repo.asignados == [(incendio.id, [2])]
    assert refrescados == [incendio.id]


def test_la_ventana_del_sector_es_la_corta() -> None:
    """Tres horas y no las doce de `match_window_hours`: un sector es grande, y
    dos incendios en él con medio día de diferencia son dos incendios."""
    repo = RepoFalso()
    repo.racimos = [señal(2, PUNTO_NOTA)]
    engine, _ = motor(repo)

    asyncio.run(engine._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    (busqueda,) = repo.busquedas_por_sector
    assert busqueda["since"] == AHORA - timedelta(hours=3)
    assert busqueda["family"] == "fire"


def test_sin_incidente_en_el_sector_se_abre_uno_como_siempre() -> None:
    repo = RepoFalso()
    repo.racimos = [señal(2, PUNTO_NOTA)]
    engine, _ = motor(repo)
    resultado = CorrelationPass(started_at=AHORA)

    asyncio.run(engine._step_a_spatial(resultado, now=AHORA))

    assert len(repo.creados) == 1
    assert resultado.incidents_created == 1
    (_, (link,)), = repo.enlaces
    assert link.link_method is LinkMethod.SPATIAL
    assert link.link_confidence == 1.0


def test_sin_sector_no_se_busca_por_sector() -> None:
    repo = RepoFalso(existentes={("fire", CLAVE): incendio_existente()})
    repo.racimos = [señal(2, PUNTO_NOTA, sector_clave=None)]
    engine, _ = motor(repo)

    asyncio.run(engine._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    assert repo.busquedas_por_sector == []
    assert len(repo.creados) == 1


def test_un_choque_en_el_mismo_sector_no_se_une_al_incendio() -> None:
    """La cuarta puerta del aislamiento entre familias."""
    repo = RepoFalso(existentes={("fire", CLAVE): incendio_existente()})
    repo.racimos = [señal(3, PUNTO_NOTA, event_type=EventType.ACCIDENT)]
    engine, _ = motor(repo)

    asyncio.run(engine._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))

    assert repo.busquedas_por_sector[0]["family"] == "traffic"
    assert len(repo.creados) == 1, "el choque abre su propio incidente"


# --- Señales sin punto --------------------------------------------------------


def sin_punto(event_id: int, *, event_type: EventType = EventType.STRUCTURAL_FIRE) -> SectorSignal:
    return SectorSignal(
        event_id=event_id,
        type=event_type,
        family=family_of_event(event_type),
        sector_clave=CLAVE,
        timestamp=AHORA - timedelta(minutes=10),
    )


def test_la_nota_sin_punto_se_une_al_incendio_de_su_sector() -> None:
    """La nota de Pura Noticia no nombra ninguna calle. Si OSM no conoce el
    sector, se queda sin punto, y el Paso A no la ve nunca. Esto la une igual."""
    incendio = incendio_existente()
    repo = RepoFalso(existentes={("fire", CLAVE): incendio})
    repo.sin_punto = [sin_punto(5)]
    engine, refrescados = motor(repo)
    resultado = CorrelationPass(started_at=AHORA)

    asyncio.run(engine._step_a_sector(resultado, now=AHORA))

    assert resultado.unlocated_sector_signals == 1
    assert resultado.sector_links == 1
    (incident_id, (link,)), = repo.enlaces
    assert incident_id == incendio.id
    assert link.link_method is LinkMethod.SECTOR_TEXT
    assert link.distance_m is None
    assert repo.asignados == [(incendio.id, [5])]
    assert refrescados == [incendio.id]


def test_la_nota_sin_punto_no_crea_incidentes() -> None:
    """Como el Paso B: sin nadie en el sector, espera a la señal que la ubique."""
    repo = RepoFalso()
    repo.sin_punto = [sin_punto(5)]
    engine, refrescados = motor(repo)

    asyncio.run(engine._step_a_sector(CorrelationPass(started_at=AHORA), now=AHORA))

    assert repo.creados == []
    assert repo.enlaces == []
    assert repo.asignados == []
    assert refrescados == []


def test_la_nota_sin_punto_respeta_la_familia() -> None:
    repo = RepoFalso(existentes={("fire", CLAVE): incendio_existente()})
    repo.sin_punto = [sin_punto(6, event_type=EventType.ACCIDENT)]
    engine, _ = motor(repo)

    asyncio.run(engine._step_a_sector(CorrelationPass(started_at=AHORA), now=AHORA))

    assert repo.enlaces == []


def test_la_traza_de_la_pasada_cuenta_el_vinculo_por_sector() -> None:
    traza = CorrelationPass(started_at=AHORA).as_dict()
    assert {"clusters_joined_by_sector", "unlocated_sector_signals", "sector_links"} <= set(
        traza
    )


# --- El SQL -------------------------------------------------------------------


def test_la_busqueda_por_sector_usa_el_indice_y_filtra_por_familia() -> None:
    repo = IncidentRepository(session=None)  # type: ignore[arg-type]
    compilado = repo.open_incident_by_sector_stmt(
        sector_clave=CLAVE, family="fire", since=AHORA - timedelta(hours=3)
    ).compile(dialect=postgresql.dialect())
    sql = str(compilado)

    # `@>` es lo que puede usar el índice GIN `jsonb_path_ops` de `raw_data`.
    assert "raw_data @>" in sql
    assert {"_extraction": {"sector_clave": CLAVE}} in compilado.params.values()
    assert "fire" in compilado.params.values()
    assert "last_seen_at >=" in sql


def test_las_señales_sin_punto_son_las_que_el_paso_a_no_ve() -> None:
    repo = IncidentRepository(session=None)  # type: ignore[arg-type]
    sql = render(repo.unlocated_sector_signals_stmt(since=AHORA, limit=50))

    assert "geom IS NULL" in sql
    assert "incident_id IS NULL" in sql
    assert "-> '_extraction'" in sql and "->> 'sector_clave'" in sql


def test_la_clave_del_sector_viaja_en_el_racimo() -> None:
    """Sin esta columna en el SELECT del DBSCAN, el Paso A nunca sabría el
    sector de un racimo y el vínculo por sector no se dispararía jamás."""
    from app.repositories.incident_repository import sector_clave_sql

    assert "->> 'sector_clave'" in render(sector_clave_sql())
