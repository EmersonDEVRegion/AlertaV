"""Caché corta de lecturas y `Server-Timing` (`app/core/cache_respuestas.py`)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.cache_respuestas import CacheDeLectura, ServerTiming, cacheable
from app.core.config import settings

P = settings.API_V1_PREFIX


def armar_app() -> tuple[FastAPI, dict[str, int]]:
    """Mismo orden que `app/main.py`: caché y timing por dentro de CORS."""
    llamadas = {"n": 0}
    app = FastAPI()
    app.add_middleware(CacheDeLectura)
    app.add_middleware(ServerTiming)
    app.add_middleware(
        CORSMiddleware, allow_origins=["https://a.cl", "https://b.cl"], allow_methods=["GET"]
    )

    @app.get(f"{P}/incidents/active")
    async def activos(limit: int = 10) -> dict[str, int]:
        llamadas["n"] += 1
        await asyncio.sleep(0.05)
        return {"n": llamadas["n"], "limit": limit}

    @app.get(f"{P}/incidents/stats")
    async def stats() -> dict[str, int]:
        llamadas["n"] += 1
        return {"n": llamadas["n"]}

    @app.get(f"{P}/events/seismic")
    async def falla() -> Response:
        llamadas["n"] += 1
        return Response(status_code=503)

    @app.get(f"{P}/events/water-cuts/geojson")
    async def con_etag() -> Response:
        llamadas["n"] += 1
        return Response(content="{}", media_type="application/json", headers={"ETag": '"x"'})

    return app, llamadas


@pytest.fixture
def encendida(monkeypatch):
    monkeypatch.setattr(settings, "API_RESPONSE_CACHE_SECONDS", 20)


def test_la_segunda_lectura_sale_de_la_cache(encendida):
    app, llamadas = armar_app()
    cliente = TestClient(app)
    primera = cliente.get(f"{P}/incidents/active")
    segunda = cliente.get(f"{P}/incidents/active")
    assert primera.json() == segunda.json() == {"n": 1, "limit": 10}
    assert primera.headers["x-alertav-cache"] == "miss"
    assert segunda.headers["x-alertav-cache"] == "hit"
    assert llamadas["n"] == 1


def test_la_query_string_separa_entradas(encendida):
    app, llamadas = armar_app()
    cliente = TestClient(app)
    cliente.get(f"{P}/incidents/active?limit=5")
    cliente.get(f"{P}/incidents/active?limit=6")
    assert llamadas["n"] == 2


def test_cada_origen_recibe_su_propia_cabecera_cors(encendida):
    """Si la caché quedara por fuera de CORS, devolvería el origen del primero."""
    app, _ = armar_app()
    cliente = TestClient(app)
    a = cliente.get(f"{P}/incidents/active", headers={"Origin": "https://a.cl"})
    b = cliente.get(f"{P}/incidents/active", headers={"Origin": "https://b.cl"})
    assert b.headers["x-alertav-cache"] == "hit"
    assert a.headers["access-control-allow-origin"] == "https://a.cl"
    assert b.headers["access-control-allow-origin"] == "https://b.cl"


def test_no_guarda_errores_ni_respuestas_con_etag_ni_rutas_fuera_de_la_lista(encendida):
    app, llamadas = armar_app()
    cliente = TestClient(app)
    for ruta in (f"{P}/events/seismic", f"{P}/events/water-cuts/geojson", f"{P}/incidents/stats"):
        cliente.get(ruta)
        cliente.get(ruta)
    assert llamadas["n"] == 6


def test_vence(monkeypatch):
    monkeypatch.setattr(settings, "API_RESPONSE_CACHE_SECONDS", 1)
    app, llamadas = armar_app()
    cliente = TestClient(app)
    cliente.get(f"{P}/incidents/active")
    import time

    time.sleep(1.1)
    cliente.get(f"{P}/incidents/active")
    assert llamadas["n"] == 2


def test_apagada_no_guarda_nada():
    app, llamadas = armar_app()
    cliente = TestClient(app)
    cliente.get(f"{P}/incidents/active")
    cliente.get(f"{P}/incidents/active")
    assert llamadas["n"] == 2


def test_un_solo_vuelo_por_clave(encendida):
    """Diez peticiones simultáneas con la caché vacía son una consulta."""
    app, llamadas = armar_app()

    async def diez() -> list[httpx.Response]:
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
            return await asyncio.gather(*(c.get(f"{P}/incidents/active") for _ in range(10)))

    respuestas = asyncio.run(diez())
    assert {r.json()["n"] for r in respuestas} == {1}
    assert llamadas["n"] == 1


def test_server_timing_en_toda_respuesta():
    app, _ = armar_app()
    respuesta = TestClient(app).get(f"{P}/incidents/stats")
    assert respuesta.headers["server-timing"].startswith("app;dur=")


@pytest.mark.parametrize(
    ("ruta", "esperado"),
    [
        (f"{P}/incidents/active", True),
        (f"{P}/incidents/INC-2026-00001", True),
        (f"{P}/incidents/stats", False),
        (f"{P}/collectors/health", True),
        (f"{P}/events/cuarteles", False),
        (f"{P}/events/citizen-report", False),
        (f"{P}/push/status", False),
    ],
)
def test_que_rutas_se_guardan(ruta, esperado):
    assert cacheable(ruta) is esperado


def test_la_app_real_trae_la_caché_dentro_de_cors():
    from app.main import app

    nombres = [m.cls.__name__ for m in app.user_middleware]
    assert nombres.index("CORSMiddleware") < nombres.index("CacheDeLectura")
