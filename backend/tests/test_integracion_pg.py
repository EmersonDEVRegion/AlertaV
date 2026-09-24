"""Lo que sólo se puede probar contra Postgres de verdad.

Saltados por defecto: la suite corre sin base. Para ejercitarlos, contra una
base desechable con `alembic upgrade head` aplicado:

    ALERTAV_PG_INTEGRACION=1 POSTGRES_PORT=5433 POSTGRES_DB=alertav_test \\
        pytest tests/test_integracion_pg.py

Cubren el SQL crudo que los dobles de los demás tests no pueden validar: el
reclamo del inbox con `FOR UPDATE SKIP LOCKED`, el abandono tras
`INBOX_MAX_INTENTOS` y el upsert que no borra coordenadas.

**Nunca contra producción**: cada test borra las filas que toca.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("ALERTAV_PG_INTEGRACION") != "1",
    reason="integración con Postgres: ALERTAV_PG_INTEGRACION=1 y una base desechable",
)

DATASET = "datasetDeIntegracion"


def correr(corrutina_factory):
    """Una base de eventos por test, y el pool cerrado dentro del mismo loop."""
    from app.core.database import dispose_engine

    async def envoltura():
        try:
            return await corrutina_factory()
        finally:
            await dispose_engine()

    return asyncio.run(envoltura())


async def _limpiar() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource
    from app.models.event import CollectorRun, RawEvent
    from app.services import apify_webhook_service as svc

    async with AsyncSessionLocal() as session:
        await session.execute(delete(CollectorRun).where(CollectorRun.collector == svc.COLLECTOR_NAME))
        await session.execute(
            delete(RawEvent).where(
                RawEvent.source == EventSource.TRANSPORTE_INFORMA,
                RawEvent.external_id.like("integracion:%"),
            )
        )
        await session.commit()


async def _fila(run_id: int):
    from app.core.database import AsyncSessionLocal
    from app.models.event import CollectorRun

    async with AsyncSessionLocal() as session:
        return await session.get(CollectorRun, run_id)


async def _envejecer(run_id: int, *, minutos: int, intentos: int | None = None) -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.event import CollectorRun

    tabla = CollectorRun.__table__.fullname
    extra = f", 'intentos', {int(intentos)}" if intentos is not None else ""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                f"UPDATE {tabla} SET params = params || jsonb_build_object("
                f"'reclamado_en', now() - make_interval(mins => {int(minutos)}){extra}) "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )
        await session.commit()


# --- El inbox -----------------------------------------------------------------


def test_encolar_es_idempotente_por_dataset():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        primera = await svc.encolar_dataset(DATASET, {"resource": {"id": "r1"}})
        segunda = await svc.encolar_dataset(DATASET, {"resource": {"id": "r1"}})
        return primera, segunda

    assert correr(caso) == (True, False)


def test_dos_reclamos_simultaneos_no_toman_la_misma_entrega():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        return await asyncio.gather(svc.reclamar_siguiente(), svc.reclamar_siguiente())

    a, b = correr(caso)
    tomados = [r for r in (a, b) if r is not None]
    assert len(tomados) == 1, "SKIP LOCKED: uno la toma, el otro sigue de largo"
    run_id, dataset, _traza, intento = tomados[0]
    assert dataset == DATASET
    assert intento == 1

    fila = correr(lambda: _fila(run_id))
    assert fila.params["inbox"] == svc.INBOX_EN_PROCESO
    assert fila.status == "running"


def test_una_entrega_reclamada_y_viva_no_se_vuelve_a_reclamar():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        await svc.reclamar_siguiente()
        return await svc.reclamar_siguiente()

    assert correr(caso) is None


def test_una_entrega_huerfana_se_reclama_de_nuevo():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        await _envejecer(run_id, minutos=20)
        return await svc.reclamar_siguiente()

    reclamo = correr(caso)
    assert reclamo is not None
    assert reclamo[3] == 2, "segundo intento"


def test_tras_agotar_los_intentos_la_entrega_se_cierra_failed():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        await _envejecer(run_id, minutos=20, intentos=svc.INBOX_MAX_INTENTOS)
        otro = await svc.reclamar_siguiente()
        return run_id, otro

    run_id, otro = correr(caso)
    assert otro is None
    fila = correr(lambda: _fila(run_id))
    assert fila.status == "failed"
    assert fila.params["inbox"] == svc.INBOX_ABANDONADO
    assert fila.finished_at is not None
    assert "se abandona" in (fila.error or "")


def test_retomar_la_corrida_conserva_los_params_del_inbox():
    from app.core.database import AsyncSessionLocal
    from app.services import apify_webhook_service as svc
    from app.services.ingest_service import IngestService

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        async with AsyncSessionLocal() as session:
            run = await IngestService(session).resume_run(run_id, {"keys": ["5-1"]})
            return dict(run.params)

    params = correr(caso)
    assert params["keys"] == ["5-1"]
    assert params["inbox"] == svc.INBOX_EN_PROCESO
    assert params["dataset_id"] == DATASET


# --- El upsert no borra coordenadas -------------------------------------------


def test_reingerir_sin_punto_conserva_el_punto_anterior():
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.models.event import RawEvent
    from app.repositories.event_repository import EventRepository
    from app.schemas.event import EventCreate

    def evento(lat, lon, raw):
        return EventCreate(
            timestamp=datetime.now(UTC),
            source=EventSource.TRANSPORTE_INFORMA,
            type=EventType.ACCIDENT,
            lat=lat,
            lon=lon,
            text="Accidente en Av. España con Uno Norte",
            external_id="integracion:1",
            confidence=0.6,
            raw_data=raw,
        )

    async def caso():
        await _limpiar()
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            await repo.upsert_many(
                [evento(-33.02, -71.55, {"_geocoding": {"comuna": "Viña del Mar"}})]
            )
            await session.commit()
            await repo.upsert_many([evento(None, None, {"otra": 1})])
            await session.commit()
            fila = await session.scalar(
                select(RawEvent).where(RawEvent.external_id == "integracion:1")
            )
            conocidos = await repo.puntos_conocidos(
                EventSource.TRANSPORTE_INFORMA, ["integracion:1", "integracion:no"]
            )
            return fila.lat, fila.lon, dict(fila.raw_data), conocidos

    lat, lon, raw, conocidos = correr(caso)
    assert (lat, lon) == (-33.02, -71.55)
    assert raw["_punto_heredado"] is True
    assert raw["_geocoding"] == {"comuna": "Viña del Mar"}
    assert raw["otra"] == 1, "el resto del raw_data nuevo sí entra"
    assert set(conocidos) == {"integracion:1"}
    assert conocidos["integracion:1"].lat == -33.02
