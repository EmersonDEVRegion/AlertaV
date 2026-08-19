"""Contrato de salida de los sismos.

Un sismo **no es un incidente**. No pasa por el motor de correlación, no tiene
`confidence` y no se agrupa con nada: es un hecho medido por una red
sismológica, y por eso su schema no comparte nada con `IncidentRead`.

Lo que sí necesita decir con claridad es lo contrario de lo que la gente asume:
que haya temblado no significa que haya un siniestro. El sismo es **contexto** —
causa posible de incendios, derrumbes o tsunami—, y el mapa tiene que poder
mostrarlo sin que se confunda con una emergencia declarada.

Los nombres de las propiedades son los de las columnas de `seismic_details`
(`magnitude`, `depth_km`) y no una traducción: el resto de la API va en inglés y
un objeto con `lat`, `lon` y `magnitud` mezclados obligaría a mantener un mapeo
que no aporta nada.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeismicEventRead(BaseModel):
    """Un sismo del USGS con su detalle sismológico."""

    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    usgs_id: str = Field(..., description="Identificador del catálogo del USGS.")
    timestamp: datetime = Field(..., description="Hora de origen del sismo, en UTC.")

    lat: float
    lon: float

    magnitude: float | None = Field(
        default=None,
        description=(
            "Magnitud. Puede venir vacía en una solución preliminar: el USGS "
            "publica la detección antes de terminar de calcularla."
        ),
    )
    mag_type: str | None = Field(
        default=None, description="Escala usada: mww, mb, ml, md…"
    )
    depth_km: float | None = Field(
        default=None,
        description=(
            "Profundidad del hipocentro en kilómetros. Puede ser negativa: se "
            "mide desde el nivel del mar."
        ),
    )

    place: str | None = Field(
        default=None, description="Descripción del USGS, en inglés y relativa a una localidad."
    )
    commune: str | None = None
    province: str | None = None

    felt_reports: int | None = Field(
        default=None, description="Reportes ciudadanos «Did You Feel It?» del USGS."
    )
    tsunami: bool = Field(
        default=False,
        description=(
            "Bandera del USGS. Indica que el evento cumple criterios para "
            "evaluación de tsunami, NO que haya alerta vigente en Chile: eso lo "
            "declara SENAPRED y viaja por `alert_level` en los incidentes."
        ),
    )
    pager_alert: str | None = Field(
        default=None, description="Nivel PAGER de impacto estimado: green | yellow | orange | red."
    )
    significance: int | None = None
    review_status: str | None = Field(
        default=None,
        description=(
            "`automatic` = solución de máquina, sin revisar. `reviewed` = revisada "
            "por un sismólogo. La magnitud de una automática puede corregirse."
        ),
    )
    usgs_url: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> SeismicEventRead:
        return cls.model_validate(row, from_attributes=True)


class SeismicStats(BaseModel):
    """Resumen de la ventana consultada."""

    total: int
    max_magnitude: float | None
    felt_count: int = Field(
        ..., description="Sismos con al menos un reporte ciudadano al USGS."
    )
    tsunami_flagged: int
