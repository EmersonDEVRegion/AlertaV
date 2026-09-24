"""Logging estructurado en JSON — pensado para agregación posterior."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

from app.core.config import settings

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


#: Largo mínimo de un secreto para tacharlo. Por debajo, un valor corto (el
#: `POSTGRES_PASSWORD` local de desarrollo es "alertav") taparía media línea de
#: log sin proteger nada.
_MIN_SECRET_LEN = 8


def secretos_configurados() -> tuple[str, ...]:
    """Los secretos que nunca pueden aparecer en un log, del más largo al más corto.

    Del más largo primero para que un secreto que contiene a otro se tache
    entero. La contraseña de la base se toma de `DATABASE_URL` en sus dos formas
    (codificada y no), porque es lo que se pega desde el panel de Supabase.
    """
    dsn_password = urlsplit(settings.DATABASE_URL.strip()).password or ""
    candidatos = (
        settings.FIRMS_MAP_KEY,
        settings.APIFY_TOKEN,
        settings.GEMINI_API_KEY,
        settings.APIFY_WEBHOOK_SECRET,
        settings.VAPID_PRIVATE_KEY,
        settings.OPERATOR_TOKEN,
        settings.POSTGRES_PASSWORD,
        dsn_password,
        unquote(dsn_password),
    )
    limpios = {c.strip() for c in candidatos if c and len(c.strip()) >= _MIN_SECRET_LEN}
    return tuple(sorted(limpios, key=len, reverse=True))


class JsonFormatter(logging.Formatter):
    """Una línea JSON por registro, con los secretos tachados.

    El tachado va sobre la línea FINAL y no sobre el mensaje: así cubre también
    los `extra` y, sobre todo, el traceback. Ese fue el camino por el que la
    `FIRMS_MAP_KEY` llegaba a los logs de Render: la clave va en la ruta de la
    URL, y el mensaje de `httpx.HTTPStatusError` —impreso por
    `logger.exception` en el traceback encadenado— trae la URL completa.
    """

    def __init__(self, secretos: tuple[str, ...] | None = None) -> None:
        super().__init__()
        self._secretos = secretos_configurados() if secretos is None else secretos

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        linea = json.dumps(payload, ensure_ascii=False, default=str)
        for secreto in self._secretos:
            linea = linea.replace(secreto, "***")
        return linea


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
