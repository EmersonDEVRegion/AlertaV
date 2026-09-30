"""Moderación de los comentarios ciudadanos, para el operador.

Todas las rutas piden `Authorization: Bearer <OPERATOR_TOKEN>`. Ver
`app.services.moderacion` para quién revisa qué y por qué.

    GET  /api/v1/moderacion?estado=pendiente
    POST /api/v1/moderacion/{public_id}   {"decision": "aprobar" | "rechazar"}
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import OperatorDep, SessionDep
from app.models.enums import EventSource
from app.models.event import RawEvent
from app.repositories.event_repository import EventRepository
from app.services.ciudadanos import (
    APROBADO,
    CLAVE_MODERACION,
    PENDIENTE,
    RECHAZADO,
)
from app.services.moderacion import aplicar, decision

router = APIRouter(prefix="/moderacion", tags=["operación"], dependencies=[OperatorDep])


class ComentarioCiudadano(BaseModel):
    public_id: UUID
    timestamp: datetime
    categoria: str | None
    texto: str | None
    estado: str | None
    por: str | None = None
    motivo: str | None = None
    decidido_en: str | None = None
    incident_id: int | None


class Decision(BaseModel):
    decision: Literal["aprobar", "rechazar"]
    motivo: str | None = Field(default=None, max_length=200)


def _leer(evento: RawEvent) -> ComentarioCiudadano:
    datos: dict[str, Any] = evento.raw_data or {}
    moderacion = datos.get(CLAVE_MODERACION) or {}
    return ComentarioCiudadano(
        public_id=evento.public_id,
        timestamp=evento.timestamp,
        categoria=datos.get("category"),
        texto=evento.text,
        estado=moderacion.get("estado"),
        por=moderacion.get("por"),
        motivo=moderacion.get("motivo"),
        decidido_en=moderacion.get("en"),
        incident_id=evento.incident_id,
    )


@router.get(
    "",
    response_model=list[ComentarioCiudadano],
    summary="Comentarios ciudadanos por estado de moderación",
)
async def listar(
    session: SessionDep,
    estado: Annotated[
        list[Literal["pendiente", "aprobado", "rechazado"]] | None,
        Query(description="Uno o más estados. Por defecto, sólo `pendiente`."),
    ] = None,
    horas: Annotated[int, Query(ge=1, le=720)] = 72,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ComentarioCiudadano]:
    estados: tuple[str, ...] = tuple(estado) if estado else (PENDIENTE,)
    eventos = await EventRepository(session).pending_moderation(
        since=datetime.now(UTC) - timedelta(hours=horas), limit=limit, estados=estados
    )
    return [_leer(evento) for evento in eventos]


@router.post(
    "/{public_id}",
    response_model=ComentarioCiudadano,
    summary="Aprobar o rechazar un comentario ciudadano",
)
async def decidir(public_id: UUID, cuerpo: Decision, session: SessionDep) -> ComentarioCiudadano:
    evento = await EventRepository(session).get_by_public_id(public_id)
    if evento is None or evento.source is not EventSource.CITIZEN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reporte ciudadano no encontrado")
    estado = APROBADO if cuerpo.decision == "aprobar" else RECHAZADO
    aplicar(evento, decision(estado, por="operador", motivo=cuerpo.motivo))
    await session.commit()
    return _leer(evento)
