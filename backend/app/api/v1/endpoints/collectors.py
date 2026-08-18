"""Disparo manual y estado de los collectors.

En producción estos endpoints deben quedar detrás de autenticación de operador:
lanzan tráfico saliente hacia APIs de terceros con cuota.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.deps import IngestServiceDep, SessionDep
from app.collectors.registry import available_collectors, get_collector
from app.models.event import CollectorRun

router = APIRouter(prefix="/collectors", tags=["collectors"])


class CollectorRunResult(BaseModel):
    collector: str
    source: str
    status: str
    fetched: int
    inserted: int
    duplicated: int
    rejected: int
    error: str | None = None


class CollectorRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    collector: str
    status: str
    started_at: object
    finished_at: object | None
    events_fetched: int
    events_inserted: int
    events_duplicate: int
    error: str | None


@router.get("", summary="Collectors disponibles")
async def list_collectors() -> dict[str, list[str]]:
    return {"collectors": available_collectors()}


@router.post(
    "/{name}/run",
    response_model=CollectorRunResult,
    summary="Ejecutar un collector ahora",
)
async def run_collector(name: str, service: IngestServiceDep) -> CollectorRunResult:
    try:
        collector = get_collector(name, service.session)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    result = await collector.run()
    return CollectorRunResult(
        collector=result.collector,
        source=result.source.value,
        status=result.status.value,
        fetched=result.fetched,
        inserted=result.inserted,
        duplicated=result.duplicated,
        rejected=result.rejected,
        error=result.error,
    )


@router.get(
    "/runs",
    response_model=list[CollectorRunRead],
    summary="Últimas ejecuciones",
    description=(
        "Permite distinguir 'no hubo eventos' de 'el collector estaba caído' "
        "al analizar la ventana de recolección."
    ),
)
async def list_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CollectorRunRead]:
    stmt = select(CollectorRun).order_by(desc(CollectorRun.started_at)).limit(limit)
    runs = (await session.execute(stmt)).scalars().all()
    return [CollectorRunRead.model_validate(run) for run in runs]
