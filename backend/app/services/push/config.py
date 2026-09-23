"""Estado del push en este proceso: si está activo, con qué claves y por qué no.

Se resuelve una sola vez por proceso. Una clave mal pegada en Render no debe
tumbar la API —que sigue sirviendo el mapa— ni el worker —que sigue recolectando
incendios—: el push se declara apagado, con el motivo escrito, y lo demás sigue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.services.push.webpush import VapidKeyError, VapidKeys, VapidSigner

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PushConfig:
    enabled: bool
    signer: VapidSigner | None = None
    reason: str | None = None

    @property
    def public_key(self) -> str | None:
        return self.signer.keys.public_b64url if self.signer else None


def load_push_config(*, private_key: str, subject: str, switch: bool) -> PushConfig:
    """Traduce la configuración a un estado. Nunca lanza."""
    if not private_key.strip():
        return PushConfig(
            enabled=False,
            reason="Falta VAPID_PRIVATE_KEY: el servidor no tiene claves para firmar avisos.",
        )
    try:
        keys = VapidKeys.from_private_b64url(private_key)
        signer = VapidSigner(keys, subject=subject.strip())
    except VapidKeyError as exc:
        return PushConfig(enabled=False, reason=f"Configuración VAPID inválida: {exc}")

    if not switch:
        # Las suscripciones se siguen aceptando: la clave pública es válida y
        # el día que se reactive el push no hay que pedirle a nadie que vuelva
        # a suscribirse.
        return PushConfig(
            enabled=False,
            signer=signer,
            reason="Los avisos están pausados (PUSH_ENABLED=false).",
        )
    return PushConfig(enabled=True, signer=signer)


@lru_cache(maxsize=1)
def get_push_config() -> PushConfig:
    config = load_push_config(
        private_key=settings.VAPID_PRIVATE_KEY,
        subject=settings.VAPID_SUBJECT,
        switch=settings.PUSH_ENABLED,
    )
    if not config.enabled:
        logger.warning("notificaciones push desactivadas", extra={"motivo": config.reason})
    return config


__all__ = ["PushConfig", "get_push_config", "load_push_config"]
