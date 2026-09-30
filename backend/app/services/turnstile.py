"""Verificación de Cloudflare Turnstile para el reporte ciudadano.

Turnstile es gratis y, en su modo gestionado, casi siempre invisible: el
navegador resuelve un desafío y entrega un token de un solo uso que el servidor
confirma contra Cloudflare. No frena a una persona decidida a mentir —para eso
está el quórum—, frena al script que manda cien reportes.

Política de fallos, y por qué es asimétrica:

* **Sin `TURNSTILE_SECRET_KEY`** → no se verifica nada. Es el estado local y el
  de producción hasta que se cree el sitio en Cloudflare.
* **Cloudflare dice que el token no vale** → se rechaza (403). Es la respuesta
  que Turnstile existe para dar.
* **Cloudflare no responde** → se deja pasar y se anota. Una caída de un
  tercero no puede impedir que alguien reporte un incendio; el reporte igual
  necesita quórum para verse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TurnstileResult:
    #: ¿Se deja pasar?
    permitido: bool
    #: ¿Cloudflare confirmó el token? False también cuando no se verificó.
    verificado: bool
    motivo: str | None = None


def activo() -> bool:
    return bool(settings.TURNSTILE_SECRET_KEY.strip())


async def verificar(token: str | None, *, ip: str | None = None) -> TurnstileResult:
    """Confirma el token con Cloudflare. Nunca lanza."""
    secreto = settings.TURNSTILE_SECRET_KEY.strip()
    if not secreto:
        return TurnstileResult(permitido=True, verificado=False, motivo="desactivado")
    if not token or not token.strip():
        return TurnstileResult(permitido=False, verificado=False, motivo="sin token")

    datos = {"secret": secreto, "response": token.strip()}
    if ip and ip != "desconocida":
        datos["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=settings.TURNSTILE_TIMEOUT_SECONDS) as cliente:
            respuesta = await cliente.post(settings.TURNSTILE_VERIFY_URL, data=datos)
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
    except Exception as exc:
        logger.warning(
            "Turnstile no respondió; el reporte pasa sin verificar",
            extra={"error": type(exc).__name__},
        )
        return TurnstileResult(permitido=True, verificado=False, motivo="sin respuesta")

    if isinstance(cuerpo, dict) and cuerpo.get("success") is True:
        return TurnstileResult(permitido=True, verificado=True)

    codigos = cuerpo.get("error-codes") if isinstance(cuerpo, dict) else None
    logger.info("token de Turnstile rechazado", extra={"codigos": codigos})
    return TurnstileResult(permitido=False, verificado=False, motivo="token inválido")


__all__ = ["TurnstileResult", "activo", "verificar"]
