"""Contrato de la API de notificaciones push."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Largo máximo de un endpoint. Los reales rondan los 200-500 caracteres; 2048
#: deja margen sin aceptar un cuerpo arbitrario.
_MAX_ENDPOINT = 2048


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=80, max_length=120)
    auth: str = Field(..., min_length=16, max_length=40)


class BrowserSubscription(BaseModel):
    """Lo que devuelve `PushSubscription.toJSON()` en el navegador, tal cual."""

    endpoint: str = Field(..., min_length=10, max_length=_MAX_ENDPOINT)
    keys: PushKeys
    #: El navegador lo manda y casi siempre en `null`. Se acepta y se ignora: la
    #: suscripción muere cuando el servicio de push responde 404/410.
    expirationTime: float | None = None  # noqa: N815 — nombre del estándar W3C

    @field_validator("endpoint")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("el endpoint de push debe ser https")
        return value


class PushSubscribeRequest(BaseModel):
    subscription: BrowserSubscription
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    accuracy_m: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    notify_incidents: bool = True
    notify_seismic: bool = True
    radius_m: float | None = Field(
        default=None,
        ge=500.0,
        le=20_000.0,
        description="Radio de aviso de emergencias. Por defecto, PUSH_INCIDENT_RADIUS_M.",
    )


class PushSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., validation_alias="public_id")
    lat: float
    lon: float
    radius_m: float
    notify_incidents: bool
    notify_seismic: bool
    location_updated_at: datetime


class PushEndpointRequest(BaseModel):
    """Cuerpo de las operaciones que sólo identifican la suscripción."""

    endpoint: str = Field(..., min_length=10, max_length=_MAX_ENDPOINT)


class PushStatus(BaseModel):
    """Lo que la PWA necesita saber antes de ofrecer el botón de avisos."""

    enabled: bool = Field(..., description="¿El servidor está enviando avisos?")
    public_key: str | None = Field(
        default=None,
        description=(
            "`applicationServerKey` para `pushManager.subscribe`. Puede venir "
            "aunque `enabled` sea false: con los avisos pausados la suscripción "
            "se sigue aceptando."
        ),
    )
    reason: str | None = None
    incident_radius_m: float
    incident_min_confidence: float
    incident_min_sources: int
    seismic_min_magnitude: float


class PushProbeResult(BaseModel):
    sent: bool
    detail: str
