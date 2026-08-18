"""Health checks."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get(
    "/health/ready",
    summary="Readiness",
    description=(
        "Verifica conexión a la base y que PostGIS esté instalado. Sin PostGIS "
        "la columna `geom` no existe y toda la ingesta falla, así que conviene "
        "detectarlo antes de recibir tráfico. Devuelve 503 si algo falta."
    ),
    responses={503: {"description": "Dependencia no disponible"}},
    response_model=None,
)
async def readiness(session: SessionDep) -> Response | dict[str, object]:
    try:
        await session.execute(text("SELECT 1"))
        postgis_version = (
            await session.execute(text("SELECT PostGIS_Version()"))
        ).scalar_one()
    except Exception as exc:
        logger.warning("readiness falló", extra={"error": str(exc)})
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": False, "postgis": False,
                     "error": str(exc)[:300]},
        )

    return {"status": "ok", "database": True, "postgis": postgis_version}
