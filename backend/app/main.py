"""Punto de entrada de la aplicación FastAPI.

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.endpoints.push import close_probe_sender
from app.api.v1.router import api_router
from app.core.cache_respuestas import CacheDeLectura, ServerTiming
from app.core.config import settings
from app.core.database import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info(
        "AlertaV iniciado",
        extra={"version": settings.VERSION, "environment": settings.ENVIRONMENT},
    )
    yield
    await close_probe_sender()
    await dispose_engine()
    logger.info("AlertaV detenido")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Backend de recolección y correlación de señales de emergencia de la "
        "Región de Valparaíso.\n\n"
        "**Esta API expone señales crudas, no incidentes confirmados.** Una "
        "anomalía térmica satelital o un reporte ciudadano son evidencia "
        "parcial; la confirmación exige corroboración de fuentes oficiales."
    ),
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Caché de 20 s de las lecturas que la PWA sondea y `Server-Timing`. Se
# registran ANTES que CORS a propósito: `add_middleware` apila hacia afuera, así
# que CORS queda por fuera y pone las cabeceras de origen de cada petición sobre
# la respuesta guardada. Al revés, la caché devolvería las del primero que pidió.
# Ver `app/core/cache_respuestas.py`.
app.add_middleware(CacheDeLectura)
app.add_middleware(ServerTiming)

# El frontend vive en Vercel, en otro origen: sin esto el navegador descarta
# toda respuesta de la API. Dos listas complementarias:
#   - CORS_ORIGINS: dominios exactos (producción y localhost).
#   - CORS_ORIGIN_REGEX: los previews de Vercel, cuyo subdominio cambia con cada
#     rama y no se pueden enumerar de antemano.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    # Cabeceras que el navegador puede LEER desde otro origen. Por defecto no
    # expone ninguna, y sin `ETag` la revalidación condicional de la capa de
    # amenaza no funciona entre dominios: el navegador no puede guardar lo que
    # no ve, así que se bajaría el artefacto entero en cada carga del mapa.
    # `X-AlertaV-Hazard-Stale` avisa de que la capa salió de la caché de
    # respaldo — un dato viejo servido sin marcarlo es una mentira silenciosa.
    # `Retry-After` acompaña a los 429 (reporte ciudadano, prueba de push): sin
    # exponerla, el cliente no puede esperar lo que el servidor pidió y
    # reintenta a ciegas con su propio backoff.
    expose_headers=[
        "ETag",
        "Retry-After",
        "X-AlertaV-Hazard-Stale",
        "X-AlertaV-Hazard-Generated-At",
        "X-AlertaV-Cache",
        "Server-Timing",
    ],
    # El preflight de un endpoint que no cambia de forma no necesita repetirse
    # en cada carga del mapa.
    max_age=3600,
)

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# --- Artefactos geoespaciales estáticos --------------------------------------
#
# Capas de referencia que no cambian cada cinco minutos: hoy el mapa de amenaza
# sísmica del CSN, mañana los polígonos comunales. Se generan a mano con los
# scripts de `scripts/`, se versionan en el repositorio y se sirven como
# archivos.
#
# Por qué no un endpoint que lea la base: porque no hay nada que consultar. Son
# bytes idénticos en cada petición, así que un `StaticFiles` con su `ETag` y su
# `Last-Modified` hace que el navegador los pida una vez y después responda 304
# — algo que un endpoint tendría que reimplementar para igualar.
#
# El montaje es condicional: si el directorio no existe —un checkout parcial, un
# contenedor mal armado— la aplicación arranca igual y sólo pierde esta capa. Un
# `RuntimeError` en el import por una capa de referencia dejaría sin API a quien
# consulta incendios activos, que es un intercambio malo.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
else:  # pragma: no cover — sólo en despliegues incompletos
    logger.warning(
        "no se montó /static: el directorio no existe",
        extra={"esperado": str(_STATIC_DIR)},
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "api": settings.API_V1_PREFIX,
    }
