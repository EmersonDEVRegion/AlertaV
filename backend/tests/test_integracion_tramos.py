"""Tramos del MTT contra Postgres de verdad: `osm_vias`, PostGIS y el vínculo
semáforo ↔ corte de luz.

Saltado por defecto, igual que `test_integracion_pg.py`. **Nunca contra
producción**: vacía `osm_cruces`, `osm_vias`, `osm_indice` y el motor.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("ALERTAV_PG_INTEGRACION") != "1",
    reason="integración con Postgres: ALERTAV_PG_INTEGRACION=1 y una base desechable",
)

#: Esquinas reales de Viana (índice de OSM, 17-10-2026).
SUCRE = (-33.02607, -71.55201)
ETCHEVERS = (-33.02567, -71.55579)
VON_SCHROEDERS = (-33.02482, -71.56155)
LA_MARINA = (-33.02369, -71.56826)
LIMACHE = (-33.02984, -71.53766)

ESQUINAS = [
    (1, *SUCRE, "Plaza Sucre", "Viana", "sucre", "viana", ["vina del mar"]),
    (2, *ETCHEVERS, "Etchevers", "Viana", "etchevers", "viana", ["vina del mar"]),
    (3, *VON_SCHROEDERS, "Von Schroeders", "Viana", "schroeders", "viana", ["vina del mar"]),
]


def _wkt(*puntos: tuple[float, float]) -> str:
    return "MULTILINESTRING((" + ",".join(f"{lon} {lat}" for lat, lon in puntos) + "))"


VIAS = [
    (
        "Viana",
        "viana",
        ["vina del mar"],
        _wkt(LIMACHE, SUCRE, ETCHEVERS, VON_SCHROEDERS, LA_MARINA),
    ),
]


def correr(corrutina_factory):
    from app.core.database import dispose_engine

    async def envoltura():
        try:
            return await corrutina_factory()
        finally:
            await dispose_engine()

    return asyncio.run(envoltura())


async def _cargar_osm() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services import osm_cruces_service as svc

    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE alertav.osm_cruces"))
        await session.execute(text("TRUNCATE alertav.osm_vias"))
        await session.execute(text("DELETE FROM alertav.osm_indice"))
        await session.commit()
    async with AsyncSessionLocal() as session:
        await svc.reemplazar(session, iter(ESQUINAS), version="v-prueba", datos_de=None)
    async with AsyncSessionLocal() as session:
        await svc.reemplazar_vias(session, iter(VIAS), version="v-prueba")


@pytest.fixture
def osm(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CRUCES_LOCALES_ENABLED", True)


def test_las_vias_se_cargan_con_su_version(osm):
    from app.core.database import AsyncSessionLocal
    from app.services import osm_cruces_service as svc

    async def caso():
        await _cargar_osm()
        async with AsyncSessionLocal() as session:
            version = await svc.version_vias_cargada(session)
            filas = (await session.execute(text("SELECT count(*) FROM alertav.osm_vias"))).scalar()
        return version, filas

    assert correr(caso) == ("v-prueba", 1)


def test_el_tramo_sigue_la_calle_entre_los_dos_extremos(osm):
    from app.collectors import tramos

    async def caso():
        await _cargar_osm()
        return await tramos.ubicar(
            {"via": "Viana", "desde": "Sucre", "hasta": "Von Schroeders", "ciudad": "ViñadelMar"},
            nominatim=False,
        )

    ubicacion, sin_ubicar = correr(caso)
    assert sin_ubicar == []
    assert ubicacion is not None
    assert ubicacion.metodo == tramos.TRAMO
    assert ubicacion.geometria["type"] == "LineString"
    coords = ubicacion.geometria["coordinates"]
    assert len(coords) == 3, "Sucre, Etchevers y Von Schroeders: el tramo pasa por la esquina"
    assert 850 < ubicacion.largo_m < 950


def test_un_punto_en_la_esquina_y_lo_que_no_se_encuentra(osm):
    from app.collectors import tramos

    async def caso():
        await _cargar_osm()
        esquina = await tramos.ubicar(
            {"via": "Viana", "punto": "Etchevers", "ciudad": "Viña del Mar"}, nominatim=False
        )
        perdido = await tramos.ubicar(
            {"via": "Viana", "punto": "Escuela de Operaciones", "ciudad": "ViñadelMar"},
            nominatim=False,
        )
        otra_via = await tramos.ubicar({"via": "Calle Inexistente"}, nominatim=False)
        return esquina, perdido, otra_via

    (esquina, _), (perdido, sin_ubicar), (otra_via, sin_via) = correr(caso)
    assert esquina is not None and esquina.metodo == tramos.PUNTO
    assert esquina.lat == pytest.approx(ETCHEVERS[0], abs=1e-4)
    assert perdido is None and sin_ubicar == ["Escuela de Operaciones"]
    assert otra_via is None and sin_via == ["Calle Inexistente"]


def test_toda_la_via_solo_si_se_permite(osm):
    from app.collectors import tramos

    extraccion = {"via": "Viana", "ciudad": "ViñadelMar", "toda_la_via": True}

    async def caso():
        await _cargar_osm()
        return (
            await tramos.ubicar(extraccion, nominatim=False),
            await tramos.ubicar(extraccion, permitir_via_completa=True, nominatim=False),
        )

    (sin_permiso, _), (completa, _) = correr(caso)
    assert sin_permiso is None
    assert completa is not None and completa.metodo == tramos.VIA_COMPLETA


def test_el_semaforo_apagado_queda_vinculado_al_corte_de_luz():
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.models.incident import IncidentRelation
    from app.repositories.event_repository import EventRepository
    from app.schemas.event import EventCreate
    from app.services.correlation.engine import CorrelationEngine

    ahora = datetime.now(UTC)

    def evento(external_id, punto, minutos, fuente, tipo, raw):
        return EventCreate(
            timestamp=ahora - timedelta(minutes=minutos),
            source=EventSource(fuente),
            type=EventType(tipo),
            lat=punto[0],
            lon=punto[1],
            text=f"integración {external_id}",
            external_id=f"integracion:{external_id}",
            raw_data=raw,
        )

    async def caso():
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("TRUNCATE alertav.incidents, alertav.raw_events RESTART IDENTITY CASCADE")
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await EventRepository(session).upsert_many(
                [
                    evento("corte", SUCRE, 40, "cge", "power_outage", {}),
                    evento(
                        "semaforo",
                        ETCHEVERS,
                        20,
                        "transporte_informa",
                        "traffic_condition",
                        {"_mtt": {"tipo": "semaforo"}},
                    ),
                    evento(
                        "taco",
                        ETCHEVERS,
                        20,
                        "transporte_informa",
                        "traffic_condition",
                        {"_mtt": {"tipo": "congestion"}},
                    ),
                ]
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await CorrelationEngine(session, relaciones="sombra").run()
        async with AsyncSessionLocal() as session:
            return (await session.execute(select(IncidentRelation))).scalars().all()

    [vinculo] = correr(caso)
    assert vinculo.regla == "corte_luz_semaforo"
    assert vinculo.otro_event_id is not None
    assert 19 * 60 <= vinculo.desfase_s <= 21 * 60


def test_el_notificador_encuentra_el_transito_que_avisa():
    """El SQL de `recent_transito` (`has_key` sobre JSONB) contra Postgres."""
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.repositories.event_repository import EventRepository
    from app.repositories.push_repository import PushRepository
    from app.schemas.event import EventCreate

    ahora = datetime.now(UTC)

    def evento(external_id, tipo_evento, mtt):
        return EventCreate(
            timestamp=ahora - timedelta(minutes=5),
            source=EventSource.TRANSPORTE_INFORMA,
            type=EventType(tipo_evento),
            lat=ETCHEVERS[0],
            lon=ETCHEVERS[1],
            text=f"integración {external_id}",
            external_id=f"integracion:{external_id}",
            raw_data={"_mtt": mtt, "comuna": "Viña del Mar"} if mtt else {},
        )

    tramo = {"metodo": "tramo", "largo_m": 900}

    async def caso():
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("TRUNCATE alertav.incidents, alertav.raw_events RESTART IDENTITY CASCADE")
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await EventRepository(session).upsert_many(
                [
                    evento(
                        "taco",
                        "traffic_condition",
                        {"tipo": "congestion", "via": "Viana", "ubicacion": tramo},
                    ),
                    evento(
                        "lento",
                        "traffic_condition",
                        {"tipo": "lento", "via": "Viana", "ubicacion": tramo},
                    ),
                    evento("marcha", "road_closure", {"tipo": "marcha", "via": "Viana"}),
                    evento("portal", "road_closure", None),
                ]
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            return await PushRepository(session).recent_transito(
                since=ahora - timedelta(minutes=30)
            )

    vistas = correr(caso)
    assert sorted(v.mtt["tipo"] for v in vistas) == ["congestion", "marcha"]
    assert {v.comuna for v in vistas} == {"Viña del Mar"}
