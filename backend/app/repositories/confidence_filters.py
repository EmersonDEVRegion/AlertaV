"""Comparaciones de confianza que sobreviven a la precisión de la columna.

Toda comparación de confianza contra la base pasa por acá, y la razón es un bug
de producción concreto: los reportes ciudadanos sin corroborar se quedaban
clavados en el mapa con 40 % de confianza porque el `UPDATE` de la regla de
muerte súbita no encontraba ninguna fila que descartar.

El motivo era el tipo físico de la columna. `incidents.confidence` es `REAL`
(float4) y el motor calcula, escribe y compara en float8:

    Python escribe            0.40                    (float8, exacto para el motor)
    PostgreSQL guarda         0.4000000059604645      (float4, redondeo al alza)
    la comparación reconvierte a float8 y compara     0.4000000059604645 <= 0.40
                                                      → false

Ninguna excepción, ningún log, `rowcount = 0`. La guarda existía, estaba bien
escrita, y no matcheaba nunca.

Estas dos funciones aplican `CONFIDENCE_EPSILON` en el sentido correcto —hacia
afuera del conjunto que se quiere seleccionar— de modo que el umbral siempre
incluya el valor que el propio motor escribió. Son deliberadamente `sargable`:
no envuelven la columna en `ROUND()` ni la castean a `numeric`, así que un
índice sobre `confidence` sigue sirviendo.

La migración `0006` pasa `incidents.confidence` a `DOUBLE PRECISION` y elimina
la causa de raíz. Estas funciones se quedan igual: son la red que evita que el
problema vuelva si alguien redeclara la columna, y siguen cubriendo
`raw_events.confidence`, que sigue siendo `REAL` a propósito (ver la migración).
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

from app.models.enums import CONFIDENCE_EPSILON

__all__ = ["confidence_at_least", "confidence_at_most"]


def confidence_at_most(
    column: InstrumentedAttribute[float], value: float
) -> ColumnElement[bool]:
    """`column <= value`, incluyendo el `value` que float4 redondeó hacia arriba."""
    return column <= value + CONFIDENCE_EPSILON


def confidence_at_least(
    column: InstrumentedAttribute[float], value: float
) -> ColumnElement[bool]:
    """`column >= value`, incluyendo el `value` que float4 redondeó hacia abajo."""
    return column >= value - CONFIDENCE_EPSILON
