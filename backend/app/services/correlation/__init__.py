"""Motor de correlación de AlertaV.

    señales independientes  ──►  un incidente con confianza agregada

Tres piezas, deliberadamente separadas por su dependencia de la base de datos:

* `confidence` — Confidence Engine. Funciones puras: reciben una lista de
  señales y devuelven confianza, tipo, estado y la derivación auditable. Se
  testea sin PostGIS.
* `communes`  — extracción y coincidencia de comunas. También puro.
* `engine`    — `CorrelationEngine`. Lo único que toca la base: orquesta el
  Paso A (geometría) y el Paso B (texto) y persiste.

Esa frontera es intencional. La parte difícil de este sistema no es el SQL: es
decidir cuánto vale cada señal. Mantenerla pura permite recalibrarla con
fixtures reales, en segundos, sin levantar nada.
"""

from app.services.correlation.confidence import (
    ConfidenceResult,
    SignalView,
    build_title,
    resolve_status,
    resolve_type,
    score,
)
from app.services.correlation.engine import CorrelationEngine, CorrelationPass

__all__ = [
    "ConfidenceResult",
    "CorrelationEngine",
    "CorrelationPass",
    "SignalView",
    "build_title",
    "resolve_status",
    "resolve_type",
    "score",
]
