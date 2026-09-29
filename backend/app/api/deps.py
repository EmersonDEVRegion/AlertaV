"""Dependencias compartidas de la API."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.services.hazard_service import SeismicHazardService
from app.services.incident_service import IncidentService
from app.services.ingest_service import IngestService
from app.services.push.subscriptions import PushSubscriptionService
from app.services.seismic_service import SeismicService
from app.services.vehicle_feed_service import VehicleFeedService
from app.services.water_cut_service import WaterCutService
from app.services.weather_service import WeatherService

logger = logging.getLogger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_operator(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Rutas de operación: `Authorization: Bearer <OPERATOR_TOKEN>`.

    Hasta el 2026-09-23 los docstrings de estas rutas decían «debe quedar detrás
    de autenticación de operador» y nada lo imponía: cualquiera con la URL podía
    disparar collectors (gasto de Gemini, riesgo de bloqueo en Nominatim) o
    correr el motor. Ahora:

    * **Producción sin token configurado → 503.** Falla cerrada: la ruta no
      existe hasta que alguien la habilite a propósito. No bloquea el arranque
      —un 503 en una ruta de operación no deja a nadie sin mapa—, pero tampoco
      queda abierta.
    * **Local y staging sin token → abierta**, como antes, para que los scripts
      y los tests sigan funcionando sin ceremonia.
    * **Con token → comparación en tiempo constante**, sobre bytes para que un
      valor no ASCII no suba como 500 (el mismo cuidado que el webhook).
    """
    esperado = settings.OPERATOR_TOKEN.strip()
    if not esperado:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="operación deshabilitada: falta OPERATOR_TOKEN",
            )
        return

    recibido = (authorization or "").strip()
    if recibido.lower().startswith("bearer "):
        recibido = recibido[len("bearer ") :].strip()
    if not recibido or not secrets.compare_digest(
        recibido.encode("utf-8"), esperado.encode("utf-8")
    ):
        logger.info("ruta de operador rechazada", extra={"trae_credencial": bool(recibido)})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="no autorizado",
            headers={"WWW-Authenticate": "Bearer"},
        )


#: Para `dependencies=[OperatorDep]` en las rutas de operación.
OperatorDep = Depends(require_operator)


async def get_ingest_service(session: SessionDep) -> IngestService:
    return IngestService(session)


async def get_incident_service(session: SessionDep) -> IncidentService:
    return IncidentService(session)


async def get_seismic_service(session: SessionDep) -> SeismicService:
    return SeismicService(session)


async def get_weather_service(session: SessionDep) -> WeatherService:
    return WeatherService(session)


async def get_push_service(session: SessionDep) -> PushSubscriptionService:
    return PushSubscriptionService(session)


async def get_vehicle_feed_service(session: SessionDep) -> VehicleFeedService:
    return VehicleFeedService(session)


async def get_water_cut_service(session: SessionDep) -> WaterCutService:
    return WaterCutService(session)


def get_hazard_service() -> SeismicHazardService:
    """La capa de amenaza no toca la base: es un artefacto en disco.

    Sin `SessionDep` a propósito. Pedir una conexión para leer un archivo
    ataría la única capa que puede sobrevivir a una caída de Postgres
    justamente a Postgres.
    """
    return SeismicHazardService()


IngestServiceDep = Annotated[IngestService, Depends(get_ingest_service)]
IncidentServiceDep = Annotated[IncidentService, Depends(get_incident_service)]
SeismicServiceDep = Annotated[SeismicService, Depends(get_seismic_service)]
WeatherServiceDep = Annotated[WeatherService, Depends(get_weather_service)]
HazardServiceDep = Annotated[SeismicHazardService, Depends(get_hazard_service)]
PushServiceDep = Annotated[PushSubscriptionService, Depends(get_push_service)]
VehicleFeedServiceDep = Annotated[VehicleFeedService, Depends(get_vehicle_feed_service)]
WaterCutServiceDep = Annotated[WaterCutService, Depends(get_water_cut_service)]
