"""Router agregador de la v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import apify, collectors, events, health, incidents

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(events.router)
api_router.include_router(incidents.router)
api_router.include_router(collectors.router)
# Entrada empujada desde afuera, no una vista de lectura como las de arriba. Su
# prefijo (`/apify`) no se solapa con ninguno de los otros, así que el orden de
# inclusión no la afecta — a diferencia de las rutas fijas de `/events`, que
# tienen que ir antes que su `/{public_id}`.
api_router.include_router(apify.router)
