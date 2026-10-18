"""Nombres de los sectores que toca un corte, según OpenStreetMap.

Chilquinta sabe qué sectores deja sin luz («Rodelillo», «Santos Ossa»), pero
sólo lo publica en una API con llave. Lo que sí está abierto es el área del
corte (`core/areas.py`), y OSM tiene los barrios y poblaciones de la
región con nombre (`static/geo/osm_sectores_valpo.json`, armado por
`scripts/osm_sectores.py`). Este módulo cruza las dos cosas.

Son nombres de OSM, no de la empresa: la ficha lo dice. Pueden faltar o no
coincidir con cómo la empresa llama al sector.

Cómo se eligen
--------------
* **Con los clientes del corte** (Chilquinta): un lugar cuenta si tiene al menos
  `MIN_CLIENTES` (o el `MIN_FRACCION` del corte, si es mayor) puntos a menos de
  `RADIO_M`. Así entra «Rodelillo» aunque su nodo de OSM caiga en una quebrada
  sin casas, y no entra el barrio vecino por un poste suelto.
* **Sólo con el polígono** (CGE): los lugares que caen dentro.

Primero los sectores grandes (`suburb`, `quarter`, `village`), después los
barrios, y dentro de cada grupo por cantidad de clientes (o cercanía al pin).
"""

from __future__ import annotations

import json
import logging
import math
import unicodedata
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.core.areas import caja, contiene

logger = logging.getLogger(__name__)

RUTA = Path(__file__).resolve().parents[2] / "static" / "geo" / "osm_sectores_valpo.json"

RADIO_M = 300.0
MIN_CLIENTES = 5
MIN_FRACCION = 0.03
MAXIMO = 5

#: Orden de presentación por tipo de lugar: lo grande y conocido primero.
_RANGO = {"suburb": 0, "quarter": 0, "village": 0, "neighbourhood": 1, "hamlet": 1, "locality": 2}

_M_POR_GRADO_LAT = 110_574.0


@lru_cache(maxsize=1)
def _lugares() -> tuple[np.ndarray, tuple[str, ...], tuple[str, ...]]:
    """`(coordenadas [lat, lon], nombres, tipos)`. Vacío si el archivo falta."""
    try:
        documento = json.loads(RUTA.read_text(encoding="utf-8"))
        filas = [f for f in documento.get("lugares", []) if isinstance(f, list) and len(f) >= 4]
    except (OSError, ValueError) as exc:
        logger.warning("sin lista de sectores de OSM", extra={"error": str(exc)})
        filas = []
    coordenadas = np.array([[float(f[1]), float(f[2])] for f in filas], dtype=np.float64)
    return (
        coordenadas.reshape(-1, 2),
        tuple(str(f[0]) for f in filas),
        tuple(str(f[3]) for f in filas),
    )


def _clave(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def sectores(
    *,
    area: Mapping[str, Any] | None = None,
    clientes: Sequence[tuple[float, float]] = (),
    pin: tuple[float, float] | None = None,
    maximo: int = MAXIMO,
) -> list[str]:
    """Nombres de OSM para el corte, del más relevante al menos. `[]` si nada calza."""
    coordenadas, nombres, tipos = _lugares()
    if not len(nombres):
        return []

    candidatos: list[tuple[int, float, str]] = []  # (rango, -peso o distancia, nombre)
    if clientes:
        puntos = np.asarray(clientes, dtype=np.float64)
        lat0 = float(puntos[:, 0].mean())
        kx = 111_320.0 * math.cos(math.radians(lat0))
        margen_lat = RADIO_M / _M_POR_GRADO_LAT
        margen_lon = RADIO_M / kx
        dentro = (
            (coordenadas[:, 0] >= puntos[:, 0].min() - margen_lat)
            & (coordenadas[:, 0] <= puntos[:, 0].max() + margen_lat)
            & (coordenadas[:, 1] >= puntos[:, 1].min() - margen_lon)
            & (coordenadas[:, 1] <= puntos[:, 1].max() + margen_lon)
        )
        minimo = max(MIN_CLIENTES, math.ceil(MIN_FRACCION * len(puntos)))
        for indice in np.flatnonzero(dentro).tolist():
            lat, lon = coordenadas[indice]
            distancias = np.hypot(
                (puntos[:, 0] - lat) * _M_POR_GRADO_LAT, (puntos[:, 1] - lon) * kx
            )
            peso = int((distancias <= RADIO_M).sum())
            if peso >= minimo:
                candidatos.append((_RANGO.get(tipos[indice], 2), -peso, nombres[indice]))
    elif area is not None:
        limites = caja(area)
        if limites is None:
            return []
        sur, oeste, norte, este = limites
        dentro = (
            (coordenadas[:, 0] >= sur)
            & (coordenadas[:, 0] <= norte)
            & (coordenadas[:, 1] >= oeste)
            & (coordenadas[:, 1] <= este)
        )
        for indice in np.flatnonzero(dentro).tolist():
            lat, lon = (float(v) for v in coordenadas[indice])
            if not contiene(area, lat, lon):
                continue
            distancia = math.hypot(lat - pin[0], lon - pin[1]) if pin else 0.0
            candidatos.append((_RANGO.get(tipos[indice], 2), distancia, nombres[indice]))
    else:
        return []

    salida: list[str] = []
    vistos: set[str] = set()
    for _, _, nombre in sorted(candidatos):
        clave = _clave(nombre)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(nombre)
        if len(salida) >= maximo:
            break
    return salida


__all__ = ["MAXIMO", "RADIO_M", "sectores"]
