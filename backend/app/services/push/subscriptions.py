"""Alta, baja y prueba de suscripciones. Lo que usa la API; el notificador no.

La ubicación se redondea acá, en la puerta de entrada, y no en la base: así
ningún camino —la API, un script, una prueba— puede guardar la coordenada
exacta de la casa de alguien. Ver el docstring de `app/models/push.py`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.push import PushSubscription
from app.repositories.push_repository import PushRepository
from app.schemas.push import PushProbeResult, PushSubscribeRequest
from app.services.push.config import get_push_config
from app.services.push.messages import probe_message
from app.services.push.webpush import (
    PushSubscriptionKeys,
    SubscriptionKeyError,
    WebPushSender,
    validate_subscription_keys,
)

logger = logging.getLogger(__name__)

#: Decimales de la ubicación guardada. Tres son ~110 m en latitud (y ~95 m en
#: longitud a la altura de Valparaíso): de sobra para un radio de 5 km, y
#: demasiado gruesos para señalar una casa.
LOCATION_DECIMALS = 3


def round_location(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, LOCATION_DECIMALS), round(lon, LOCATION_DECIMALS)


def endpoint_host_allowed(endpoint: str, allowed: list[str]) -> bool:
    """¿El endpoint pertenece a un servicio de push conocido?

    Coincide el dominio exacto o un subdominio suyo, nunca un sufijo suelto:
    `evil-push.apple.com.example.org` no pasa por terminar en algo parecido.
    """
    host = (urlsplit(endpoint).hostname or "").lower().rstrip(".")
    if not host:
        return False
    for entry in allowed:
        domain = entry.lower().strip().lstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


class PushSubscriptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PushRepository(session)

    async def subscribe(self, request: PushSubscribeRequest) -> PushSubscription:
        endpoint = request.subscription.endpoint
        if not endpoint_host_allowed(endpoint, settings.PUSH_ALLOWED_ENDPOINT_HOSTS):
            raise ValidationError(
                "Este navegador usa un servicio de notificaciones que AlertaV no reconoce.",
                detail={"host": urlsplit(endpoint).hostname},
            )
        try:
            validate_subscription_keys(
                request.subscription.keys.p256dh, request.subscription.keys.auth
            )
        except SubscriptionKeyError as exc:
            raise ValidationError(f"Suscripción inválida: {exc}") from exc

        lat, lon = round_location(request.lat, request.lon)
        subscription = await self.repo.upsert_subscription(
            endpoint=endpoint,
            p256dh=request.subscription.keys.p256dh,
            auth=request.subscription.keys.auth,
            lat=lat,
            lon=lon,
            accuracy_m=(round(request.accuracy_m) if request.accuracy_m is not None else None),
            radius_m=request.radius_m or settings.PUSH_INCIDENT_RADIUS_M,
            notify_incidents=request.notify_incidents,
            notify_seismic=request.notify_seismic,
        )
        await self.session.commit()
        return subscription

    async def unsubscribe(self, endpoint: str) -> bool:
        removed = await self.repo.delete_by_endpoint(endpoint)
        await self.session.commit()
        return removed

    async def probe(self, endpoint: str, *, sender: WebPushSender | None) -> PushProbeResult:
        """Manda el aviso de prueba a una suscripción ya registrada.

        Sólo a una registrada: si no, este endpoint serviría para mandar
        notificaciones firmadas por AlertaV a cualquier navegador.
        """
        subscription = await self.repo.get_by_endpoint(endpoint)
        if subscription is None:
            return PushProbeResult(
                sent=False,
                detail="Este dispositivo no está suscrito. Activa los avisos primero.",
            )
        if sender is None:
            return PushProbeResult(
                sent=False,
                detail=get_push_config().reason or "Los avisos no están disponibles.",
            )

        message = probe_message(radius_m=subscription.radius_m)
        now = datetime.now(UTC)
        result = await sender.send(
            PushSubscriptionKeys(
                endpoint=subscription.endpoint,
                p256dh=subscription.p256dh,
                auth=subscription.auth,
            ),
            message.payload(now=now),
            ttl_seconds=message.ttl_seconds,
            topic=message.topic,
        )
        if result.ok:
            return PushProbeResult(sent=True, detail="Aviso de prueba enviado.")
        if result.gone:
            await self.repo.delete_by_endpoint(endpoint)
            await self.session.commit()
            return PushProbeResult(
                sent=False,
                detail=(
                    "El navegador ya no acepta avisos de esta suscripción. Vuelve a activarlos."
                ),
            )
        logger.warning(
            "aviso de prueba fallido",
            extra={"http_status": result.status_code, "error": result.error},
        )
        return PushProbeResult(
            sent=False,
            detail="El servicio de notificaciones rechazó el aviso. Intenta más tarde.",
        )


__all__ = [
    "LOCATION_DECIMALS",
    "PushSubscriptionService",
    "endpoint_host_allowed",
    "round_location",
]
