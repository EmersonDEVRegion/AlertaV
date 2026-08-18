"""Dependencias compartidas de la API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.incident_service import IncidentService
from app.services.ingest_service import IngestService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_ingest_service(session: SessionDep) -> IngestService:
    return IngestService(session)


async def get_incident_service(session: SessionDep) -> IncidentService:
    return IncidentService(session)


IngestServiceDep = Annotated[IngestService, Depends(get_ingest_service)]
IncidentServiceDep = Annotated[IncidentService, Depends(get_incident_service)]
