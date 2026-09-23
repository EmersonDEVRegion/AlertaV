"""Suscripciones a notificaciones push y registro de lo que se envió.

Dos tablas, y ninguna toca el modelo de señales ni el de incidentes: el
notificador *lee* incidentes y sismos, y sólo escribe acá. Si mañana se apaga el
push, el resto del sistema no se entera.

Qué se guarda de una persona, y qué no
--------------------------------------
Una suscripción es un teléfono, no una persona: no hay cuenta, correo ni
nombre. Lo que sí hay es una **ubicación**, y es un dato sensible —con la app
cerrada, la última ubicación conocida suele ser la casa—. Por eso:

* Se guarda redondeada a tres decimales (unos 110 m). Para decidir si alguien
  está a menos de 5 km de un incendio sobra, y deja de señalar una puerta.
* No hay historial: cada actualización pisa la anterior.
* Desuscribirse borra la fila y, en cascada, su registro de envíos.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base

_SCHEMA = settings.DB_SCHEMA

_GEOM_EXPR = "ST_SetSRID(ST_MakePoint(lon, lat), 4326)"

#: Tipos de envío. `incident` y `seismic` los decide el notificador; la prueba
#: que pide el usuario desde la PWA no se registra (ver `PushDelivery`).
DELIVERY_KINDS = ("incident", "seismic")

#: Estados de un envío. `pending` existe entre que se reserva el envío y que el
#: servicio de push responde: si el proceso muere en ese hueco, el aviso queda
#: sin mandar en vez de mandarse dos veces.
DELIVERY_STATUSES = ("pending", "sent", "failed", "gone")


class PushSubscription(Base):
    """Un navegador que aceptó recibir avisos, con su última ubicación."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=sa_text("gen_random_uuid()")
    )

    # -- Destino (lo entrega el navegador) -----------------------------------
    endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc=(
            "URL del servicio de push del navegador (Google, Mozilla, Apple). Es "
            "la identidad de la suscripción: impredecible y única por teléfono y "
            "sitio, así que conocerla equivale a poseerla."
        ),
    )
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    # -- Ubicación -----------------------------------------------------------
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        Computed(_GEOM_EXPR, persisted=True),
        nullable=False,
    )
    accuracy_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="Incertidumbre que informó el GPS, en metros."
    )
    location_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # -- Preferencias --------------------------------------------------------
    radius_m: Mapped[float] = mapped_column(Float, nullable=False, server_default=sa_text("5000"))
    notify_incidents: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("true")
    )
    notify_seismic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("true")
    )

    # -- Salud del canal -----------------------------------------------------
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("lat >= -90.0 AND lat <= 90.0", name="lat"),
        CheckConstraint("lon >= -180.0 AND lon <= 180.0", name="lon"),
        CheckConstraint("radius_m >= 500 AND radius_m <= 20000", name="radius_m"),
        CheckConstraint("consecutive_failures >= 0", name="failures"),
        Index("uq_push_subscriptions_endpoint", "endpoint", unique=True),
        Index("uq_push_subscriptions_public_id", "public_id", unique=True),
        # El filtro del notificador es `ST_DWithin` sobre `geography` —metros
        # reales, no grados—, así que el índice tiene que ser sobre la misma
        # expresión o el planificador no lo puede usar.
        Index(
            "ix_push_subscriptions_geog",
            sa_text("(geom::geography)"),
            postgresql_using="gist",
        ),
        {"schema": _SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PushSubscription {self.id} ({self.lat:.3f},{self.lon:.3f})>"


class PushDelivery(Base):
    """Un aviso enviado (o intentado) a una suscripción.

    La unicidad por `(subscription_id, kind, subject_key)` es lo que impide
    avisar dos veces del mismo incendio: el notificador reserva la fila con
    `INSERT ... ON CONFLICT DO NOTHING` y sólo envía si la inserción ocurrió.
    Dos notificadores concurrentes —un deploy que se solapa— no pueden mandar el
    mismo aviso.
    """

    __tablename__ = "push_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.push_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        doc="Folio del incidente (INC-2026-00142) o `external_id` del sismo (csn:379889).",
    )
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in DELIVERY_KINDS) + ")", name="kind"
        ),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in DELIVERY_STATUSES) + ")",
            name="status",
        ),
        Index(
            "uq_push_deliveries_subject",
            "subscription_id",
            "kind",
            "subject_key",
            unique=True,
        ),
        Index("ix_push_deliveries_kind_subject", "kind", "subject_key"),
        Index("ix_push_deliveries_created_at", "created_at"),
        {"schema": _SCHEMA},
    )
