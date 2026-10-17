"""Fuentes de calor fijas: lo que FIRMS ve todas las noches y no es un incendio.

Una fundición o una refinería emiten calor de sobra para que VIIRS y MODIS las
marquen como anomalía térmica. Sin esto, la Fundición Chagres abría un «posible
incendio» público en Catemu casi cada noche (10 en 14 días, hasta el 16-10-2026).

Dos reglas, porque cada una tapa lo que la otra no ve:

* **Lista** (`static/geo/fuentes_termicas.json`): los focos conocidos, con su
  radio y de dónde salió el punto. Funciona desde la primera noche.
* **Recurrencia**: un punto que tuvo anomalías `FIRMS_FIJA_DIAS` días distintos
  dentro de los últimos `FIRMS_FIJA_VENTANA_DIAS` es una fuente fija aunque no
  esté en la lista. Tarda esos días en reconocer una nueva.

Lo que cualquiera de las dos marca **no se borra ni se ignora**: la anomalía
queda como señal y, si Bomberos, CONAF o un vecino abren un incidente ahí, se le
suma como corroboración. Lo único que no hace es abrir un incidente sola. Un
incendio de verdad que se extiende más allá del foco produce píxeles fuera del
radio, y esos abren incidente como siempre.

El costo de la recurrencia: un incendio que arde varios días en el mismo píxel y
se queda sin señales más de la brecha de su perfil no se reabre sólo por FIRMS.
Para eso está CONAF, que es fuente confirmatoria.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.enums import EventSource

FUENTES_PATH = Path(__file__).resolve().parents[3] / "static" / "geo" / "fuentes_termicas.json"

_RADIO_TIERRA_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class FuenteTermica:
    nombre: str
    comuna: str
    lat: float
    lon: float
    radio_m: float


def _distancia_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _RADIO_TIERRA_M * math.asin(min(1.0, math.sqrt(h)))


@lru_cache(maxsize=1)
def cargar(path: Path = FUENTES_PATH) -> tuple[FuenteTermica, ...]:
    """La lista de focos. Un archivo que falta o no se puede leer es una lista vacía:
    la recurrencia sigue funcionando sin él."""
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(
        FuenteTermica(
            nombre=str(f["nombre"]),
            comuna=str(f.get("comuna") or ""),
            lat=float(f["lat"]),
            lon=float(f["lon"]),
            radio_m=float(f["radio_m"]),
        )
        for f in datos.get("fuentes", [])
    )


def fuente_conocida(
    lat: float, lon: float, fuentes: Sequence[FuenteTermica] | None = None
) -> FuenteTermica | None:
    """El foco de la lista que contiene el punto, o None."""
    for fuente in cargar() if fuentes is None else fuentes:
        if _distancia_m(lat, lon, fuente.lat, fuente.lon) <= fuente.radio_m:
            return fuente
    return None


def solo_firms(fuentes: Sequence[EventSource]) -> bool:
    """¿El racimo es sólo satélite? Lo único a lo que se aplica la regla."""
    return bool(fuentes) and all(f is EventSource.NASA_FIRMS for f in fuentes)


__all__ = ["FUENTES_PATH", "FuenteTermica", "cargar", "fuente_conocida", "solo_firms"]
