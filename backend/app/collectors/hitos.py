"""Nomenclátor de lugares viales: los extremos de un aviso que no son una vía.

«Congestión desde Rotonda San Martín hasta Escuela de Operaciones», «Bus en
panne… altura Caleta Abarca». Una rotonda, un enlace o un puente con nombre en
OSM se resuelve con `osm_vias` (ver `tramos.py`); acá van sólo los lugares que
OSM no tiene como vía o que Nominatim confunde («Rotonda San Martín» le daba un
edificio). El archivo (`static/geo/hitos_viales.json`) se completa a medida que
el MTT nombra lugares que no se ubican: los anota `raw_data._mtt.sin_ubicar`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.collectors.geoservices import normalise_text

ARCHIVO = Path(__file__).resolve().parents[2] / "static" / "geo" / "hitos_viales.json"

#: Palabras que el aviso antepone y el nomenclátor no repite.
_PREFIJOS = ("sector ", "altura ", "plaza de ")


@dataclass(frozen=True, slots=True)
class Hito:
    nombre: str
    comuna: str
    lat: float
    lon: float


def _clave(texto: str | None) -> str:
    clave = " ".join(normalise_text(texto or "").split())
    for prefijo in _PREFIJOS:
        if clave.startswith(prefijo):
            clave = clave[len(prefijo) :]
    return clave


@lru_cache(maxsize=1)
def _indice() -> dict[str, tuple[Hito, ...]]:
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    indice: dict[str, list[Hito]] = {}
    for fila in datos["hitos"]:
        hito = Hito(fila["nombre"], fila["comuna"], float(fila["lat"]), float(fila["lon"]))
        for nombre in (fila["nombre"], *fila.get("alias", ())):
            indice.setdefault(_clave(nombre), []).append(hito)
    return {clave: tuple(hitos) for clave, hitos in indice.items()}


def buscar(nombre: str | None, comunas: Sequence[str]) -> Hito | None:
    """El lugar con ese nombre en alguna de esas comunas (normalizadas). None si no."""
    candidatos = _indice().get(_clave(nombre), ())
    for hito in candidatos:
        if not comunas or hito.comuna in comunas:
            return hito
    return None


__all__ = ["ARCHIVO", "Hito", "buscar"]
