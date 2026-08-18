"""Modelos ORM del motor de correlación.

Un `Incident` es la fusión de varias `RawEvent` en un único hecho del mundo.
La distinción que ordena todo este módulo es la misma que ordena la Fase 2:

    fenómeno  ≠  respuesta administrativa

`Incident` describe el fenómeno. Una alerta de SENAPRED no lo tipifica ni lo
crea: se le adjunta y fija su `alert_level`. De ahí que `lat`/`lon` sean NOT
NULL —un incidente nace siempre de señales georreferenciadas (Paso A)— y que una
alerta suelta, sin ningún incidente espacial que la reciba, simplemente quede sin
vincular. Esa alerta no se pierde: sigue siendo una `RawEvent` consultable. Lo
que no hace es inventar un punto en el mapa.
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
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base
from app.models.enums import IncidentStatus, IncidentType, LinkMethod

_SCHEMA = settings.DB_SCHEMA


def _pg_enum(python_enum: type, name: str) -> ENUM:
    """ENUM nativo cuyos valores son los `.value` del Enum de Python.

    `create_type=False`: el tipo lo crea la migración, no el ORM.
    """
    return ENUM(
        python_enum,
        name=name,
        schema=_SCHEMA,
        create_type=False,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        validate_strings=True,
    )


#: Igual que en `raw_events`: la geometría se deriva en la base, nunca a mano.
#: Aquí lat/lon son NOT NULL, así que no hace falta el CASE.
_GEOM_EXPR = "ST_SetSRID(ST_MakePoint(lon, lat), 4326)"


class Incident(Base):
    """Un hecho consolidado a partir de varias señales independientes."""

    __tablename__ = "incidents"

    # -- Identidad -----------------------------------------------------------
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=sa_text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=sa_text(f"{_SCHEMA}.next_incident_code()"),
        doc=(
            "Folio legible tipo INC-2026-00142. Lo genera la base para que "
            "cualquier inserción —ORM, script o psql— reciba uno consistente."
        ),
    )

    # -- Clasificación -------------------------------------------------------
    type: Mapped[IncidentType] = mapped_column(
        _pg_enum(IncidentType, "incident_type"),
        nullable=False,
        server_default=sa_text("'possible_fire'"),
    )
    status: Mapped[IncidentStatus] = mapped_column(
        _pg_enum(IncidentStatus, "incident_status"),
        nullable=False,
        server_default=sa_text("'active'"),
    )

    # -- Geometría -----------------------------------------------------------
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        Computed(_GEOM_EXPR, persisted=True),
        nullable=False,
    )

    # -- Confianza -----------------------------------------------------------
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=sa_text("0.0"),
        doc="Confianza en que el FENÓMENO es real. [0,1].",
    )
    alert_confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=sa_text("0.0"),
        doc=(
            "Confianza en el ESTADO DE ALERTA. 1.0 cuando hay una alerta "
            "vigente de SENAPRED adosada: el acto administrativo es cierto por "
            "definición. Es un eje distinto del anterior y por eso no se "
            "promedian: SENAPRED confirma que el Estado respondió, no que este "
            "punto del mapa esté ardiendo."
        ),
    )
    alert_level: Mapped[str | None] = mapped_column(
        String(32), nullable=True, doc="roja | amarilla | temprana_preventiva | verde"
    )
    is_official_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        doc="¿Hay una fuente que confirma el fenómeno (CONAF, Bomberos)?",
    )
    confidence_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
        doc=(
            "Aporte de cada fuente y techos aplicados. Un número de confianza "
            "sin su derivación no es auditable, y este número puede terminar "
            "moviendo camiones."
        ),
    )

    # -- Agregados de las señales -------------------------------------------
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("0")
    )
    sources: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sa_text("'{}'::text[]"),
        doc="Fuentes distintas que sostienen el incidente. Desnormalizado a propósito.",
    )

    # -- Descripción ---------------------------------------------------------
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    commune: Mapped[str | None] = mapped_column(String(120), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # -- Ventana temporal ----------------------------------------------------
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    correlated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    merged_into_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.incidents.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # `noload` a propósito: el endpoint del mapa devuelve decenas de incidentes y
    # no necesita sus enlaces. Quien los quiera los pide explícitamente por el
    # repositorio, y así una carga perezosa no se convierte en un N+1 silencioso
    # dentro de un handler async.
    links: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence"),
        CheckConstraint(
            "alert_confidence >= 0.0 AND alert_confidence <= 1.0",
            name="alert_confidence",
        ),
        CheckConstraint("lat >= -90.0 AND lat <= 90.0", name="lat"),
        CheckConstraint("lon >= -180.0 AND lon <= 180.0", name="lon"),
        CheckConstraint("last_seen_at >= first_seen_at", name="window"),
        CheckConstraint("event_count >= 0 AND source_count >= 0", name="counts"),
        # Invariante del ciclo de vida: `merged` y `merged_into_id` van juntos.
        CheckConstraint(
            "(status = 'merged') = (merged_into_id IS NOT NULL)", name="merged_pair"
        ),
        CheckConstraint(
            "merged_into_id IS NULL OR merged_into_id <> id", name="no_self_merge"
        ),
        CheckConstraint(
            "jsonb_typeof(confidence_breakdown) = 'object'", name="breakdown_is_object"
        ),
        Index("uq_incidents_code", "code", unique=True),
        Index("uq_incidents_public_id", "public_id", unique=True),
        Index("ix_incidents_geom", "geom", postgresql_using="gist"),
        Index(
            "ix_incidents_geom_last_seen",
            "geom",
            "last_seen_at",
            postgresql_using="gist",
        ),
        # El índice que atiende al motor y al mapa: sólo incidentes abiertos.
        Index(
            "ix_incidents_open_geom",
            "geom",
            postgresql_using="gist",
            postgresql_where=sa_text("status IN ('active', 'controlled')"),
        ),
        Index("ix_incidents_status_last_seen", "status", sa_text("last_seen_at DESC")),
        Index("ix_incidents_commune", "commune"),
        Index("ix_incidents_merged_into_id", "merged_into_id"),
        {"schema": _SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Incident {self.code} type={self.type} status={self.status} "
            f"conf={self.confidence:.2f} events={self.event_count}>"
        )


class IncidentEvent(Base):
    """Vínculo señal ↔ incidente, con el motivo del vínculo.

    Guardar `link_method` no es contabilidad: es lo que permite desarmar el
    trabajo del motor. Una vinculación `spatial` es una coincidencia geométrica
    medible (`distance_m`); una `commune_text` es una heurística sobre el nombre
    de una comuna. Si mañana el Paso B resulta ruidoso, se borran sólo sus
    enlaces y se recalcula la confianza sin tocar el Paso A.

    Asimetría deliberada de cardinalidad:

    * Una señal georreferenciada pertenece **a lo sumo a un** incidente. Lo
      impone el índice único parcial sobre `link_method = 'spatial'`.
    * Una alerta comunal puede pertenecer **a varios**. Una alerta roja para
      Viña del Mar cubre de verdad todos los incendios activos de Viña del Mar;
      forzarla a elegir uno sería inventar una precisión que el acto
      administrativo no tiene.
    """

    __tablename__ = "incident_events"

    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.incidents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    raw_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.raw_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_method: Mapped[LinkMethod] = mapped_column(
        _pg_enum(LinkMethod, "link_method"), nullable=False
    )
    link_confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=sa_text("1.0"),
        doc=(
            "Qué tan seguro es EL VÍNCULO, no la señal. Una coincidencia de "
            "comuna vale menos que una de 200 metros."
        ),
    )
    distance_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, doc="Sólo para vínculos espaciales."
    )
    matched_commune: Mapped[str | None] = mapped_column(
        String(120), nullable=True, doc="Comuna que produjo la coincidencia (Paso B)."
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped[Incident] = relationship(back_populates="links")

    __table_args__ = (
        CheckConstraint(
            "link_confidence >= 0.0 AND link_confidence <= 1.0", name="link_confidence"
        ),
        CheckConstraint("distance_m IS NULL OR distance_m >= 0", name="distance"),
        CheckConstraint(
            "link_method <> 'spatial' OR distance_m IS NOT NULL",
            name="spatial_needs_distance",
        ),
        Index(
            "uq_incident_events_spatial",
            "raw_event_id",
            unique=True,
            postgresql_where=sa_text("link_method = 'spatial'"),
        ),
        Index("ix_incident_events_raw_event_id", "raw_event_id"),
        Index("ix_incident_events_incident_method", "incident_id", "link_method"),
        {"schema": _SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<IncidentEvent incident={self.incident_id} "
            f"event={self.raw_event_id} via={self.link_method}>"
        )


class IncidentCounter(Base):
    """Contador por año que respalda `next_incident_code()`.

    Una secuencia normal de Postgres no se reinicia por año. Esta tabla sí, y el
    incremento es atómico porque va en un `INSERT ... ON CONFLICT DO UPDATE`
    que serializa por bloqueo de fila.

    Puede tener huecos si una transacción hace rollback. Es aceptable: el código
    es una etiqueta para que un operador diga "el INC-2026-00142" por radio, no
    un folio contable.
    """

    __tablename__ = "incident_counters"
    __table_args__ = ({"schema": _SCHEMA},)

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sa_text("0")
    )
