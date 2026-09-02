"""Disparo manual y estado de los collectors.

En producción estos endpoints deben quedar detrás de autenticación de operador:
lanzan tráfico saliente hacia APIs de terceros con cuota.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.deps import IngestServiceDep, SessionDep
from app.collectors.registry import available_collectors, get_collector
from app.models.event import CollectorRun
from app.services.collector_health import build_health

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


class CollectorHealthRead(BaseModel):
    collector: str
    families: list[str]
    status: str
    last_run_at: datetime | None
    age_seconds: int | None
    expected_interval_seconds: int
    detail: str | None


class HealthRead(BaseModel):
    """Lo que el mapa necesita para no mentir con un cero."""

    generated_at: datetime
    by_family: dict[str, str]
    collectors: list[CollectorHealthRead]


@router.get("", summary="Collectors disponibles")
async def list_collectors() -> dict[str, list[str]]:
    return {"collectors": available_collectors()}


@router.get(
    "/health",
    response_model=HealthRead,
    summary="Frescura de la recolección, por familia",
    description=(
        "Responde la pregunta que un contador en cero no puede: ¿no pasó nada, "
        "o no está llegando nada? `by_family` da el estado de cada capa del "
        "mapa —`ok`, `stale`, `failing`, `degraded`, `never`— y un cero con la "
        "familia en cualquier estado que no sea `ok` NO significa calma."
    ),
)
async def collectors_health(session: SessionDep) -> HealthRead:
    # Una fila por collector: la más reciente. `DISTINCT ON` es de PostgreSQL y
    # este proyecto ya depende de PostGIS, así que la portabilidad no es un
    # argumento; la alternativa con `row_number()` sobre la tabla entera lee
    # mucho más para el mismo resultado.
    stmt = (
        select(CollectorRun)
        .distinct(CollectorRun.collector)
        .order_by(CollectorRun.collector, desc(CollectorRun.started_at))
    )
    runs = (await session.execute(stmt)).scalars().all()
    ultimas = {run.collector: run for run in runs}

    salud, por_familia = build_health(ultimas)
    return HealthRead(
        generated_at=datetime.now(UTC),
        by_family=por_familia,
        collectors=[
            CollectorHealthRead(
                collector=item.collector,
                families=list(item.families),
                status=item.status,
                last_run_at=item.last_run_at,
                age_seconds=item.age_seconds,
                expected_interval_seconds=item.expected_interval_seconds,
                # Recortado: `error` guarda hasta 4000 caracteres y esto lo
                # consume el mapa en cada refresco.
                detail=item.detail[:300] if item.detail else None,
            )
            for item in salud
        ],
    )


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
