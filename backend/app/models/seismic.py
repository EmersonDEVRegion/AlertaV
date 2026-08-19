"""Detalle sismológico de una señal del USGS.

Por qué una tabla aparte y no columnas en `raw_events`
------------------------------------------------------

La decisión se tomó descartando las otras dos opciones, no por gusto:

* **Columnas en `raw_events`.** `magnitude` y `depth_km` sólo tienen sentido para
  sismos. Añadirlas a la tabla principal dejaría dos columnas NULL en cada
  detección de FIRMS, cada incendio de CONAF y cada reporte ciudadano, para
  siempre. Y el siguiente collector con campos propios pediría lo mismo: es el
  camino directo a una tabla ancha y llena de huecos.

* **Sólo `raw_data`.** El payload íntegro ya se guarda ahí y siempre se seguirá
  guardando. Pero `raw_data` es memoria, no índice: "sismos de magnitud ≥ 4.5 en
  los últimos 7 días" sobre JSONB obliga a castear texto a número en cada fila, y
  el índice GIN que existe (`jsonb_path_ops`) no sirve para rangos numéricos.
  Magnitud y profundidad no son metadatos del origen: son las dos dimensiones por
  las que un operador va a filtrar.

* **Tabla satélite (esto).** Tipada, con CHECKs, indexable por magnitud, y sin
  tocar el esquema de los demás collectors. El costo es un JOIN cuando se quieren
  los detalles — y sólo entonces.

Lo que esta tabla NO hace es abrir una segunda puerta de entrada al sistema. El
sismo entra como `RawEvent` igual que todo lo demás y hereda la idempotencia por
`(source, external_id)`, la traza en `collector_runs` y los endpoints ya
existentes. Esto es un anexo de esa fila, no un pipeline paralelo.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base

_SCHEMA = settings.DB_SCHEMA


class SeismicDetail(Base):
    """Parámetros del sismo asociados 1:1 a una fila de `raw_events`."""

    __tablename__ = "seismic_details"

    raw_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{_SCHEMA}.raw_events.id", ondelete="CASCADE"),
        primary_key=True,
        doc="La señal de la que esto es el detalle. 1:1, con borrado en cascada.",
    )
    usgs_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc=(
            "Identificador del evento en el catálogo del USGS (p. ej. "
            "'us6000tlm3'). El mismo que sostiene `raw_events.external_id`."
        ),
    )

    # -- Parámetros del sismo ------------------------------------------------
    magnitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Nullable a propósito: una solución preliminar puede publicarse sin "
            "magnitud calculada. Inventar un 0.0 sería peor que no saber."
        ),
    )
    mag_type: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc=(
            "Escala usada: ml, mb, mww, md… Sin esto, comparar dos magnitudes es "
            "comparar dos cosas distintas."
        ),
    )
    depth_km: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Profundidad del hipocentro. Puede ser negativa: el USGS mide desde "
            "el nivel del mar y un evento cordillerano superficial queda por "
            "encima. Es la variable que separa un sismo intraplaca profundo, casi "
            "inofensivo, de uno superficial destructivo de la misma magnitud."
        ),
    )
    place: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Descripción de la ubicación según el USGS (en inglés)."
    )

    # -- Impacto reportado ---------------------------------------------------
    felt_reports: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="Reportes ciudadanos «Did You Feel It?»."
    )
    tsunami: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        doc=(
            "Bandera del feed. NO es una alerta de tsunami vigente: marca que el "
            "evento cae en una región con protocolo de tsunami. Quien declara la "
            "alerta en Chile es SHOA/SENAPRED, y eso entra por su propio collector."
        ),
    )
    pager_alert: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc="Nivel PAGER de impacto estimado: green | yellow | orange | red.",
    )
    significance: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="`sig` del USGS: 0–1000, combina magnitud e impacto."
    )
    review_status: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc=(
            "'automatic' o 'reviewed'. Un sismo entra automático y se corrige "
            "horas después; el upsert reescribe la fila y esto deja ver cuál es cuál."
        ),
    )
    usgs_url: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Ficha pública del evento en earthquake.usgs.gov."
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="`updated` del feed: cuándo el USGS tocó por última vez esta solución.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Bandas generosas: son cotas de disparate, no de dominio. La mayor
        # magnitud registrada es 9.5 (Valdivia, 1960) y el sismo más profundo
        # conocido ronda los 750 km.
        CheckConstraint(
            "magnitude IS NULL OR (magnitude >= -2.0 AND magnitude <= 10.5)",
            name="magnitude",
        ),
        CheckConstraint(
            "depth_km IS NULL OR (depth_km >= -15.0 AND depth_km <= 800.0)",
            name="depth_km",
        ),
        CheckConstraint(
            "felt_reports IS NULL OR felt_reports >= 0", name="felt_reports"
        ),
        CheckConstraint(
            "significance IS NULL OR (significance >= 0 AND significance <= 5000)",
            name="significance",
        ),
        CheckConstraint(
            "pager_alert IS NULL OR pager_alert IN ('green', 'yellow', 'orange', 'red')",
            name="pager_alert",
        ),
        CheckConstraint(
            "review_status IS NULL OR review_status IN ('automatic', 'reviewed')",
            name="review_status",
        ),
        Index("uq_seismic_details_usgs_id", "usgs_id", unique=True),
        # El filtro real del operador: "los sismos fuertes, primero los recientes".
        Index(
            "ix_seismic_details_magnitude",
            sa_text("magnitude DESC NULLS LAST"),
        ),
        Index("ix_seismic_details_depth_km", "depth_km"),
        {"schema": _SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        magnitude = "?" if self.magnitude is None else f"{self.magnitude:.1f}"
        depth = "?" if self.depth_km is None else f"{self.depth_km:.0f}"
        return (
            f"<SeismicDetail {self.usgs_id} M{magnitude} "
            f"prof={depth}km event={self.raw_event_id}>"
        )
