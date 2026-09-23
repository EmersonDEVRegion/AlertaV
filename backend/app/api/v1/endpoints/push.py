"""Suscripción a notificaciones push desde la PWA.

Cuatro operaciones y ninguna pide credenciales: una suscripción se identifica
por su `endpoint`, una URL impredecible que sólo conoce el navegador que la
creó. Poseerla es la prueba de que la suscripción es tuya.

Todas son POST salvo el estado —incluida la baja—, porque el CORS del backend
sólo permite GET y POST y no hacía falta abrir DELETE para esto.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import PushServiceDep
from app.core.config import settings
from app.core.ratelimit import RateLimiter, client_ip
from app.schemas.push import (
    PushEndpointRequest,
    PushProbeResult,
    PushStatus,
    PushSubscribeRequest,
    PushSubscriptionRead,
)
from app.services.push.config import get_push_config
from app.services.push.webpush import WebPushSender

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])

#: El aviso de prueba es el único que alguien puede disparar a voluntad. Cada
#: uno es un POST firmado a Google o Apple, y un emisor que manda de más termina
#: en su lista de abusadores: se limita por IP como los reportes ciudadanos.
probe_limiter = RateLimiter(interval_seconds=settings.PUSH_TEST_MIN_INTERVAL_SECONDS)

_probe_sender: WebPushSender | None = None


def _get_probe_sender() -> WebPushSender | None:
    """Cliente de envío del proceso de la API, creado al primer uso."""
    global _probe_sender
    config = get_push_config()
    if config.signer is None or not config.enabled:
        return None
    if _probe_sender is None:
        _probe_sender = WebPushSender(config.signer)
    return _probe_sender


async def close_probe_sender() -> None:
    """Lo llama el `lifespan` de la aplicación al apagarse."""
    global _probe_sender
    if _probe_sender is not None:
        await _probe_sender.aclose()
        _probe_sender = None


@router.get(
    "/status",
    response_model=PushStatus,
    summary="¿Hay avisos push y con qué clave suscribirse?",
)
async def push_status() -> PushStatus:
    config = get_push_config()
    return PushStatus(
        enabled=config.enabled,
        public_key=config.public_key,
        reason=config.reason,
        incident_radius_m=settings.PUSH_INCIDENT_RADIUS_M,
        incident_min_confidence=settings.PUSH_INCIDENT_MIN_CONFIDENCE,
        incident_min_sources=settings.PUSH_INCIDENT_MIN_SOURCES,
        seismic_min_magnitude=settings.PUSH_SEISMIC_MIN_MAGNITUDE,
    )


@router.post(
    "/subscriptions",
    response_model=PushSubscriptionRead,
    summary="Registrar o actualizar una suscripción",
    description=(
        "Idempotente por `subscription.endpoint`. La PWA lo llama al activar los "
        "avisos y de nuevo cada vez que se abre, con la ubicación actual: es la "
        "«última ubicación conocida» con la que el servidor calcula distancias. "
        "La ubicación se guarda redondeada a ~110 m."
    ),
)
async def subscribe(payload: PushSubscribeRequest, service: PushServiceDep) -> PushSubscriptionRead:
    if get_push_config().public_key is None:
        # Sin clave pública el navegador no pudo haber creado una suscripción
        # válida para este servidor; guardarla sería guardar basura.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Los avisos no están configurados en el servidor.",
        )
    entity = await service.subscribe(payload)
    return PushSubscriptionRead.model_validate(entity)


@router.post(
    "/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Dar de baja una suscripción",
    description="Borra la suscripción, su ubicación y su registro de envíos.",
)
async def unsubscribe(payload: PushEndpointRequest, service: PushServiceDep) -> Response:
    await service.unsubscribe(payload.endpoint)
    # 204 aunque no existiera: el resultado que pidió el cliente —que ese
    # navegador no reciba avisos— se cumple igual.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/test",
    response_model=PushProbeResult,
    summary="Enviar un aviso de prueba a una suscripción registrada",
    responses={429: {"description": "Demasiadas pruebas desde la misma IP."}},
)
async def send_probe(
    payload: PushEndpointRequest, request: Request, service: PushServiceDep
) -> PushProbeResult:
    ip = client_ip(
        forwarded_for=request.headers.get("x-forwarded-for"),
        real_ip=request.headers.get("x-real-ip"),
        peer=request.client.host if request.client else None,
    )
    decision = probe_limiter.check(ip)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ya enviamos una prueba hace poco. Espera un minuto.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    return await service.probe(payload.endpoint, sender=_get_probe_sender())
