"""Router agregador de la v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import collectors, events, health, incidents

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(events.router)
api_router.include_router(incidents.router)
api_router.include_router(collectors.router)
