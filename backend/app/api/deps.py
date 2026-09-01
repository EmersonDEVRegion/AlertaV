"""Dependencias compartidas de la API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.hazard_service import SeismicHazardService
from app.services.incident_service import IncidentService
from app.services.ingest_service import IngestService
from app.services.seismic_service import SeismicService
from app.services.weather_service import WeatherService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_ingest_service(session: SessionDep) -> IngestService:
    return IngestService(session)


async def get_incident_service(session: SessionDep) -> IncidentService:
    return IncidentService(session)


async def get_seismic_service(session: SessionDep) -> SeismicService:
    return SeismicService(session)


async def get_weather_service(session: SessionDep) -> WeatherService:
    return WeatherService(session)


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
