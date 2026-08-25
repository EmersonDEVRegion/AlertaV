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

from app.api.v1.router import api_router
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
