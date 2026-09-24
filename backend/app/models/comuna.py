"""Límites comunales de la Región de Valparaíso (migración 0015).

El último recurso para darle comuna a un incidente. Hasta el 2026-09-23 la
comuna salía sólo de los atributos de la fuente o del texto, y la consulta del
operador de ese día contó 211 incidentes de CGE y 26 de FIRMS sin comuna en 30
días: el Paso B no les podía adosar ninguna alerta de SENAPRED, y el mapa los
listaba como «sin comuna».

Los polígonos vienen de la capa de División Político-Administrativa de la
Biblioteca del Congreso Nacional (vía `github.com/caracena/chile-geojson`),
simplificados a ~40 m. No son cartografía oficial de precisión: sirven para
decir en qué comuna cae un punto, y en el borde pueden equivocarse por decenas
de metros. Los nombres son los de `COMUNAS_V_REGION`, los mismos que compara el
Paso B («La Calera», no «Calera»).
"""

from __future__ import annotations

from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base

_SCHEMA = settings.DB_SCHEMA


class ComunaRegion(Base):
    __tablename__ = "comunas_region"
    __table_args__ = (
        Index("ix_comunas_region_geom", "geom", postgresql_using="gist"),
        {"schema": _SCHEMA},
    )

    #: Código Único Territorial (5101 = Valparaíso).
    cut: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    provincia: Mapped[str | None] = mapped_column(String(80), nullable=True)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )


__all__ = ["ComunaRegion"]
