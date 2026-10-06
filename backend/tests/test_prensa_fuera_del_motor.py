"""La prensa fuera del mapa (2026-10-06).

Una noticia llega con horas de atraso: con `CORRELATION_MIN_SIGNALS_FOR_INCIDENT
= 1` una sola nota abría un pin que ya no describía el presente. Ahora la prensa
no entra al motor y se lee en `GET /feed/noticias`. Lo que corre contra PostGIS
de verdad está en `test_integracion_pg.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models.enums import EventSource, EventType, IncidentStatus
from app.repositories.event_repository import EventRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.correlation.engine import CorrelationEngine, CorrelationPass
from app.services.news_feed_service import NewsFeedService, to_item

AHORA = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def render(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# --- El motor ----------------------------------------------------------------


def test_por_defecto_la_prensa_no_entra_al_motor():
    assert settings.CORRELATION_PRENSA is False
    engine = CorrelationEngine(session=None)  # type: ignore[arg-type]
    assert engine.fuentes_excluidas() == (EventSource.MEDIA,)


def test_el_interruptor_la_devuelve():
    engine = CorrelationEngine(session=None, prensa=True)  # type: ignore[arg-type]
    assert engine.fuentes_excluidas() == ()


def test_el_sql_del_paso_por_sector_excluye_la_prensa():
    repo = IncidentRepository(session=None)  # type: ignore[arg-type]
    sql = render(
        repo.unlocated_sector_signals_stmt(
            since=AHORA, limit=10, excluir_fuentes=(EventSource.MEDIA,)
        )
    )
    assert "source NOT IN ('MEDIA')" in sql or "source NOT IN ('media')" in sql
    sin = render(repo.unlocated_sector_signals_stmt(since=AHORA, limit=10))
    assert "NOT IN" not in sin


def test_el_retiro_mira_solo_incidentes_abiertos():
    repo = IncidentRepository(session=None)  # type: ignore[arg-type]
    sql = render(repo.vinculos_de_fuente_stmt(EventSource.MEDIA))
    assert "incident_events" in sql
    assert "status IN" in sql


class RepoRetiro:
    def __init__(self, *, ids: list[int], senales: dict[int, list[Any]]) -> None:
        self.ids = ids
        self.senales = senales
        self.actualizados: list[tuple[int, dict[str, Any]]] = []
        self.retirados: list[EventSource] = []

    async def retirar_fuente_de_abiertos(self, source: EventSource) -> list[int]:
        self.retirados.append(source)
        return list(self.ids)

    async def signals_of(self, incident_id: int) -> list[Any]:
        return self.senales.get(incident_id, [])

    async def update_incident(self, incident_id: int, **values: Any) -> None:
        self.actualizados.append((incident_id, values))

    async def get_by_id(self, incident_id: int) -> Any:
        return SimpleNamespace(id=incident_id)


def motor(repo: RepoRetiro, *, prensa: bool = False) -> tuple[CorrelationEngine, list[int]]:
    engine = CorrelationEngine(session=None, prensa=prensa)  # type: ignore[arg-type]
    engine.repo = repo  # type: ignore[assignment]
    refrescados: list[int] = []

    async def refrescar(incident: Any, *, now: datetime) -> None:
        refrescados.append(incident.id)

    engine._refresh = refrescar  # type: ignore[method-assign]
    return engine, refrescados


def test_lo_solo_prensa_se_descarta_y_lo_mixto_se_recalcula():
    repo = RepoRetiro(ids=[1, 2], senales={2: [object()]})
    engine, refrescados = motor(repo)
    resultado = CorrelationPass(started_at=AHORA)

    asyncio.run(engine._retirar_prensa(resultado, now=AHORA))

    assert repo.retirados == [EventSource.MEDIA]
    assert repo.actualizados == [(1, {"status": IncidentStatus.DISMISSED})]
    assert refrescados == [2]
    assert resultado.prensa_desvinculada == 2
    assert resultado.solo_prensa_descartados == 1
    assert resultado.as_dict()["solo_prensa_descartados"] == 1


def test_con_la_prensa_dentro_no_se_retira_nada():
    repo = RepoRetiro(ids=[1], senales={})
    engine, _ = motor(repo, prensa=True)
    asyncio.run(engine._retirar_prensa(CorrelationPass(started_at=AHORA), now=AHORA))
    assert repo.retirados == []


def test_el_paso_a_pide_excluir_la_prensa():
    pedidos: list[dict[str, Any]] = []

    class RepoPasoA:
        async def comunas_disponibles(self) -> bool:
            return False

        async def cluster_unassigned_events(self, **kwargs: Any) -> list[Any]:
            pedidos.append(kwargs)
            return []

    for perfiles in (True, False):
        engine = CorrelationEngine(session=None, perfiles=perfiles)  # type: ignore[arg-type]
        engine.repo = RepoPasoA()  # type: ignore[assignment]
        asyncio.run(engine._step_a_spatial(CorrelationPass(started_at=AHORA), now=AHORA))
    assert [p["excluir_fuentes"] for p in pedidos] == [(EventSource.MEDIA,)] * 2


# --- El feed -----------------------------------------------------------------


def fila_noticia(**cambios: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "public_id": uuid4(),
        "type": EventType.STRUCTURAL_FIRE,
        "text": "Incendio afecta vivienda en cerro Placeres",
        "timestamp": AHORA - timedelta(hours=2),
        "ingested_at": AHORA - timedelta(hours=1),
        "raw_data": {
            "titular": "Incendio afecta vivienda en cerro Placeres",
            "bajada": "Bomberos trabajó dos horas.",
            "url": "https://ejemplo.cl/nota",
            "comuna": "Valparaíso",
            "_prensa": {
                "portal": "puranoticia",
                "medio": "Pura Noticia",
                "fecha_declarada": "2026-10-06T13:00:00+00:00",
                "resolucion_dia": False,
            },
        },
    }
    base.update(cambios)
    return SimpleNamespace(**base)


def test_una_fila_de_prensa_se_vuelve_item():
    item = to_item(fila_noticia())  # type: ignore[arg-type]
    assert item is not None
    assert item.titular.startswith("Incendio")
    assert item.medio == "Pura Noticia"
    assert item.comuna == "Valparaíso"
    assert item.tipo is EventType.STRUCTURAL_FIRE
    assert item.hora_aproximada is False


def test_sin_hora_declarada_la_hora_es_aproximada():
    fila = fila_noticia()
    fila.raw_data["_prensa"]["fecha_declarada"] = None
    item = to_item(fila)  # type: ignore[arg-type]
    assert item is not None and item.hora_aproximada


def test_sin_titular_ni_texto_no_hay_item():
    assert to_item(fila_noticia(text="", raw_data={})) is None  # type: ignore[arg-type]


def test_la_consulta_del_feed_es_solo_prensa_y_por_fecha_de_publicacion():
    repo = EventRepository(session=None)  # type: ignore[arg-type]
    sql = render(repo.news_feed_stmt(since=AHORA, limit=5))
    assert "source = 'MEDIA'" in sql or "source = 'media'" in sql
    assert "timestamp >=" in sql
    assert "ORDER BY" in sql and "DESC" in sql


@pytest.fixture
def cliente():
    from app.api.deps import get_news_feed_service
    from app.main import app

    pedidos: list[dict[str, Any]] = []

    class RepoFeed:
        async def list_news_feed(self, **kwargs: Any) -> list[Any]:
            pedidos.append(kwargs)
            return [fila_noticia(), fila_noticia(text="", raw_data={})]

    servicio = NewsFeedService.__new__(NewsFeedService)
    servicio.repo = RepoFeed()  # type: ignore[assignment]

    async def _sin_corridas() -> None:
        return None

    servicio._ultima_corrida = _sin_corridas  # type: ignore[method-assign]
    app.dependency_overrides[get_news_feed_service] = lambda: servicio
    yield TestClient(app), pedidos
    app.dependency_overrides.pop(get_news_feed_service, None)


def test_el_endpoint_sirve_las_noticias_con_la_salud_de_la_fuente(cliente):
    client, pedidos = cliente
    respuesta = client.get("/api/v1/feed/noticias")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["horas"] == 24
    assert cuerpo["total"] == 1, "la fila vacía se omite en vez de tumbar el feed"
    assert cuerpo["items"][0]["medio"] == "Pura Noticia"
    assert cuerpo["fuente"]["collector"] == "prensa_local"
    assert cuerpo["fuente"]["estado"] == "never"
    assert pedidos[0]["limit"] == 60


def test_la_ventana_tiene_techo(cliente):
    client, _ = cliente
    assert client.get("/api/v1/feed/noticias?horas=49").status_code == 422
    assert client.get("/api/v1/feed/noticias?horas=6").json()["horas"] == 6
