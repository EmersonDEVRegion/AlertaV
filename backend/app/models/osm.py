"""Índice de cruces de calles de OpenStreetMap (migración 0021).

Cada fila es un nodo de OSM donde se tocan dos vías de nombre distinto: la
esquina que una central escribe como «ARLEGUI / TRASLAVIÑA». La arma cada mes
`scripts/osm_cruces.py` en GitHub Actions y la carga
`services/osm_cruces_service.py`. Ver `collectors/cruces_locales.py`.

Datos © OpenStreetMap contributors, bajo licencia ODbL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import ARRAY, BigInteger, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base

_SCHEMA = settings.DB_SCHEMA


class OsmCruce(Base):
    __tablename__ = "osm_cruces"
    __table_args__ = (
        Index("ix_osm_cruces_ultima_a", "ultima_a"),
        Index("ix_osm_cruces_ultima_b", "ultima_b"),
        {"schema": _SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nodo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nombre_a: Mapped[str] = mapped_column(String(200), nullable=False)
    nombre_b: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Última palabra significativa de cada nombre (`nombres_via.ultima_palabra`).
    ultima_a: Mapped[str] = mapped_column(String(80), nullable=False)
    ultima_b: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Comunas a menos de ~100 m del nodo, normalizadas («vina del mar»). Más de
    #: una cerca de un límite: los polígonos están simplificados a ~40 m.
    comunas: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )


class OsmIndice(Base):
    """La versión del índice cargada. Una sola fila."""

    __tablename__ = "osm_indice"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_osm_indice_una_fila"),
        {"schema": _SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    #: Etiqueta del release de GitHub («osm-cruces-2026-10-16»).
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Fecha de los datos de OSM (la del extracto de Geofabrik).
    datos_de: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cargado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filas: Mapped[int] = mapped_column(Integer, nullable=False)


__all__ = ["OsmCruce", "OsmIndice"]
