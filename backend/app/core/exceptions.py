"""Excepciones de dominio y su traducción a respuestas HTTP."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AlertaVError(Exception):
    """Raíz de todos los errores de dominio."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class CollectorError(AlertaVError):
    """Fallo al obtener datos de una fuente externa."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "collector_error"


class ConfigurationError(AlertaVError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "configuration_error"


class NotFoundError(AlertaVError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AlertaVError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AlertaVError)
    async def _handle(_: Request, exc: AlertaVError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )
