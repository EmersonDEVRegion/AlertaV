"""Modelos ORM del Fire Data Collector.

Nota: `text` y `timestamp` son nombres de columna pedidos por la especificación
y además chocan con `sqlalchemy.text` y con nombres de tipo. Por eso el helper
de SQLAlchemy se importa como `sa_text`: dentro del cuerpo de una clase, el
atributo `text` sombrearía a la función y rompería los `server_default`.
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
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base
from app.models.enums import EventSource, EventType

_SCHEMA = settings.DB_SCHEMA


def _pg_enum(python_enum: type, name: str) -> ENUM:
    """ENUM nativo de Postgres cuyos valores son los `.value` del Enum de Python.

    `create_type=False`: el tipo lo crea la migración, no el ORM. Así
    `Base.metadata.create_all()` en tests no compite con Alembic.
    """
    return ENUM(
        python_enum,
        name=name,
        schema=_SCHEMA,
        create_type=False,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        validate_strings=True,
    )


# Expresión de la columna generada. Idéntica a la de sql/001_schema.sql para que
# Alembic no detecte diferencias espurias.
_GEOM_EXPR = (
    "CASE WHEN lat IS NOT NULL AND lon IS NOT NULL "
    "THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326) END"
)


class RawEvent(Base):
    """Una señal cruda normalizada. NO es un incidente.

    El motor de correlación agrupa varias `RawEvent` cercanas en espacio y
    tiempo para producir un `Incident` con confianza agregada.
    """

    __tablename__ = "raw_events"

    # -- Identidad -----------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=sa_text("gen_random_uuid()"),
        doc="Identificador expuesto por la API; no filtra volumen ni orden de ingesta.",
    )

    # -- Campos del hito -----------------------------------------------------
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Momento del evento según la fuente, en UTC.",
    )
    source: Mapped[EventSource] = mapped_column(
        _pg_enum(EventSource, "event_source"), nullable=False
    )
    type: Mapped[EventType] = mapped_column(
        _pg_enum(EventType, "event_type"),
        nullable=False,
        server_default=sa_text("'unknown'"),
    )
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="ID estable en el sistema de origen. Base de la idempotencia.",
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=sa_text("0.5"),
        doc=(
            "Confianza de la señal en [0,1]. OJO: la columna física es REAL "
            "(float4), no double precision como sugiere este `Float` — ver la "
            "migración 0006. Comparar siempre con `confidence_at_least` / "
            "`confidence_at_most`: un `>= 0.35` literal no matchea con el 0.35 "
            "que la propia ingesta escribió."
        ),
    )
    raw_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )

    # -- Geometría derivada (GENERATED ... STORED, nunca se escribe a mano) ---
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        Computed(_GEOM_EXPR, persisted=True),
        nullable=True,
    )

    # -- Enriquecimiento territorial -----------------------------------------
    commune: Mapped[str | None] = mapped_column(String(120), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # -- Ciclo de vida en el pipeline ----------------------------------------
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    incident_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence"),
        CheckConstraint("lat IS NULL OR (lat >= -90.0 AND lat <= 90.0)", name="lat"),
        CheckConstraint("lon IS NULL OR (lon >= -180.0 AND lon <= 180.0)", name="lon"),
        CheckConstraint("(lat IS NULL) = (lon IS NULL)", name="latlon_pair"),
        CheckConstraint('lat IS NOT NULL OR "text" IS NOT NULL', name="has_signal"),
        CheckConstraint("jsonb_typeof(raw_data) = 'object'", name="raw_data_is_object"),
        Index(
            "uq_raw_events_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=sa_text("external_id IS NOT NULL"),
        ),
        Index("ix_raw_events_geom", "geom", postgresql_using="gist"),
        Index("ix_raw_events_geom_timestamp", "geom", "timestamp", postgresql_using="gist"),
        Index("ix_raw_events_timestamp", sa_text('"timestamp" DESC')),
        Index("ix_raw_events_source_timestamp", "source", sa_text('"timestamp" DESC')),
        Index("ix_raw_events_type_timestamp", "type", sa_text('"timestamp" DESC')),
        Index(
            "ix_raw_events_unprocessed",
            "timestamp",
            postgresql_where=sa_text("processed_at IS NULL"),
        ),
        Index(
            "ix_raw_events_incident_id",
            "incident_id",
            postgresql_where=sa_text("incident_id IS NOT NULL"),
        ),
        Index(
            "ix_raw_events_raw_data",
            "raw_data",
            postgresql_using="gin",
            postgresql_ops={"raw_data": "jsonb_path_ops"},
        ),
        Index(
            "ix_raw_events_text_fts",
            sa_text("to_tsvector('spanish', COALESCE(\"text\", ''))"),
            postgresql_using="gin",
        ),
        {"schema": _SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RawEvent id={self.id} source={self.source} "
            f"type={self.type} ts={self.timestamp}>"
        )


class SourceConfidence(Base):
    """Confianza base por fuente. Calibrable sin desplegar código."""

    __tablename__ = "source_confidence"
    __table_args__ = ({"schema": _SCHEMA},)

    source: Mapped[EventSource] = mapped_column(
        _pg_enum(EventSource, "event_source"), primary_key=True
    )
    base_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_official: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CollectorRun(Base):
    """Traza de cada ejecución de un collector.

    Sin esto, un hueco en los datos es ambiguo: ¿no hubo eventos, o el collector
    estaba caído? Durante la ventana de 7–14 días esa distinción es exactamente
    lo que se necesita para calibrar el correlacionador.
    """

    __tablename__ = "collector_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'partial', 'degraded', 'failed')",
            name="status",
        ),
        Index("ix_collector_runs_source_started", "source", sa_text("started_at DESC")),
        {"schema": _SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[EventSource] = mapped_column(
        _pg_enum(EventSource, "event_source"), nullable=False
    )
    collector: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sa_text("'running'")
    )
    events_fetched: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    events_inserted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    events_duplicate: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
