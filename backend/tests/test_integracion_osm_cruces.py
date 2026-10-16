"""El índice de cruces contra Postgres de verdad: `COPY`, PostGIS y la búsqueda.

Saltado por defecto, igual que `test_integracion_pg.py`. **Nunca contra
producción**: vacía `osm_cruces` y `osm_indice`.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("ALERTAV_PG_INTEGRACION") != "1",
    reason="integración con Postgres: ALERTAV_PG_INTEGRACION=1 y una base desechable",
)

FILAS = [
    (1, -33.0245, -71.553, "Arlegui", "Traslaviña", "arlegui", "traslavina", ["vina del mar"]),
    (2, -32.92800, -71.52000, "Avenida Borgoño", "Los Pescadores", "borgono", "pescadores", ["concon"]),
    (3, -33.0430, -71.5900, "Arlegui", "Traslaviña", "arlegui", "traslavina", ["valparaiso"]),
]


def correr(corrutina_factory):
    from app.core.database import dispose_engine

    async def envoltura():
        try:
            return await corrutina_factory()
        finally:
            await dispose_engine()

    return asyncio.run(envoltura())


async def _vaciar() -> None:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE alertav.osm_cruces"))
        await session.execute(text("DELETE FROM alertav.osm_indice"))
        await session.commit()


def test_cargar_y_buscar(monkeypatch):
    from app.collectors import cruces_locales
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.services import osm_cruces_service as svc

    monkeypatch.setattr(settings, "CRUCES_LOCALES_ENABLED", True)

    async def caso():
        await _vaciar()
        async with AsyncSessionLocal() as session:
            cargadas = await svc.reemplazar(
                session, iter(FILAS), version="osm-cruces-prueba", datos_de="2026-10-15T20:00:00Z"
            )
        async with AsyncSessionLocal() as session:
            version = await svc.version_cargada(session)
        en_vina = await cruces_locales.buscar("ARLEGUI", "TRASLAVINA", ["Viña del Mar"])
        en_concon = await cruces_locales.buscar("BORGOÑO", "LOS PESCADORES", ["Concón", "Viña del Mar"])
        return cargadas, version, en_vina, en_concon

    cargadas, version, en_vina, en_concon = correr(caso)
    assert cargadas == 3
    assert version == "osm-cruces-prueba"
    assert [(c.lat, c.lon) for c in en_vina] == [(-33.0245, -71.553)]
    assert [c.comunas for c in en_concon] == [("concon",)]


def test_un_indice_chico_no_reemplaza_al_cargado():
    from app.core.database import AsyncSessionLocal
    from app.services import osm_cruces_service as svc

    async def caso():
        await _vaciar()
        async with AsyncSessionLocal() as session:
            await svc.reemplazar(session, iter(FILAS), version="bueno", datos_de=None)
        async with AsyncSessionLocal() as session:
            with pytest.raises(svc.IndiceInvalidoError):
                await svc.reemplazar(session, iter(FILAS[:1]), version="roto", datos_de=None, minimo=2)
            await session.rollback()
        async with AsyncSessionLocal() as session:
            filas = (await session.execute(text("SELECT count(*) FROM alertav.osm_cruces"))).scalar_one()
            version = await svc.version_cargada(session)
        await _vaciar()
        return filas, version

    assert correr(caso) == (3, "bueno")
