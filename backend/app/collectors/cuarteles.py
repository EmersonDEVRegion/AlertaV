"""Cuarteles de Bomberos de la V Región: la instantánea del SIG, para servir y para ubicar.

Dos usos, un archivo
--------------------
`scripts/sig_bomberos.py` baja a mano los Cuerpos y las compañías del SIG de
Bomberos de Chile y deja `static/geo/cuarteles_valpo.json`. Este módulo:

* lo **sirve** a la capa «Cuarteles de Bomberos» del mapa
  (`GET /api/v1/events/cuarteles`), con `ETag`, igual que la amenaza sísmica;
* lo usa para **ubicar despachos cuya dirección es un cuartel**. Las centrales
  automatizadas por Viper escriben a veces el destino y no la calle: «Clave 1-1
  Dirección General Cuerpo de Bomberos Quilpué», «Quinta Compañía de Bomberos
  Quilpue», «PRIMERA COMPAÑIA», «CUARTEL GENERAL». Nominatim no conoce esos
  nombres y el despacho quedaba sin punto, o sea fuera del mapa.

La búsqueda se limita al Cuerpo que despachó (su primera comuna): la «Quinta
Compañía» de Quilpué y la de Viña están a 10 km una de otra.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.collectors.geoservices import normalise_text
from app.collectors.nominatim import PRECISION_STREET, GeocodeResult
from app.collectors.vocabulary import SistemaClaves
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: Coincide con `DEFAULT_OUTPUT` de `scripts/sig_bomberos.py`.
CUARTELES_PATH = Path(__file__).resolve().parents[2] / "static" / "geo" / "cuarteles_valpo.json"

_ORDINALES = {
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "sexta": 6, "septima": 7, "octava": 8, "novena": 9, "decima": 10,
    "undecima": 11, "duodecima": 12, "decimotercera": 13, "decimocuarta": 14,
    "decimoquinta": 15,
}

#: «Quinta Compañía», «5a Compañía», «5ta Cía», «Compañía N° 5», «Cia 5».
_COMPANIA = re.compile(
    r"\b(?:(?P<ord>" + "|".join(_ORDINALES) + r")|(?P<num>\d{1,2})\s*(?:a|ra|da|ta|va|na|°|º)?)"
    r"\s+(?:compania|cia)\b"
    r"|\b(?:compania|cia)\.?\s*(?:n\s*[°º.]?\s*)?(?P<num2>\d{1,2})\b"
)
#: El cuartel del Cuerpo, no de una compañía.
_CUARTEL_DEL_CUERPO = re.compile(r"\b(?:comandancia|cuartel general|direccion general)\b")


@dataclass(frozen=True, slots=True)
class Cuartel:
    tipo: str
    nombre: str
    cuerpo: str
    numero: int | None
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class CuartelesArtifact:
    payload: dict[str, Any]
    etag: str


@lru_cache(maxsize=1)
def _cargar(path: str) -> CuartelesArtifact:
    ruta = Path(path)
    try:
        crudo = ruta.read_bytes()
        payload = json.loads(crudo)
    except (OSError, ValueError) as exc:
        raise CollectorError(
            f"no se pudo leer {ruta.name} ({type(exc).__name__}); "
            "regenerarlo con: python -m scripts.sig_bomberos"
        ) from None
    if payload.get("type") != "FeatureCollection" or not payload.get("features"):
        raise CollectorError(
            f"{ruta.name} no es un FeatureCollection con cuarteles; "
            "regenerarlo con: python -m scripts.sig_bomberos"
        )
    return CuartelesArtifact(payload=payload, etag=f'"{sha256(crudo).hexdigest()[:32]}"')


def cargar_artefacto(path: Path | None = None) -> CuartelesArtifact:
    """El GeoJSON listo para servir. `CollectorError` (→ 502) si falta."""
    return _cargar(str(path or CUARTELES_PATH))


@lru_cache(maxsize=1)
def _cuarteles(path: str) -> tuple[Cuartel, ...]:
    try:
        artefacto = _cargar(path)
    except CollectorError:
        logger.warning("sin instantánea de cuarteles: los despachos a un cuartel no se ubican")
        return ()
    salida: list[Cuartel] = []
    for feature in artefacto.payload["features"]:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) != 2 or not props.get("cuerpo"):
            continue
        salida.append(
            Cuartel(
                tipo=str(props.get("tipo")),
                nombre=str(props.get("nombre")),
                cuerpo=normalise_text(props["cuerpo"]),
                numero=props.get("numero"),
                lat=float(coords[1]),
                lon=float(coords[0]),
            )
        )
    return tuple(salida)


def cuartel_nombrado(
    texto: str | None, sistema: SistemaClaves, *, path: Path | None = None
) -> Cuartel | None:
    """El cuartel que el aviso nombra como destino, dentro del Cuerpo que despachó."""
    limpio = normalise_text(texto or "")
    if not limpio or not sistema.comunas:
        return None
    cuerpo = normalise_text(sistema.comunas[0])
    del_cuerpo = [c for c in _cuarteles(str(path or CUARTELES_PATH)) if c.cuerpo == cuerpo]
    if not del_cuerpo:
        return None

    encontrado = _COMPANIA.search(limpio)
    if encontrado:
        if encontrado.group("ord"):
            numero = _ORDINALES[encontrado.group("ord")]
        else:
            numero = int(encontrado.group("num") or encontrado.group("num2"))
        for cuartel in del_cuerpo:
            if cuartel.tipo == "compania" and cuartel.numero == numero:
                return cuartel
        return None

    if _CUARTEL_DEL_CUERPO.search(limpio):
        for cuartel in del_cuerpo:
            if cuartel.tipo == "cuerpo":
                return cuartel
    return None


def punto_de_cuartel(cuartel: Cuartel, texto: str) -> GeocodeResult:
    """El cuartel como resultado de geocodificación, marcado como tal."""
    return GeocodeResult(
        lat=cuartel.lat,
        lon=cuartel.lon,
        display_name=f"{cuartel.nombre} (SIG Bomberos)",
        osm_type="sig_bomberos",
        query=texto[:200],
        precision=PRECISION_STREET,
        matched="street_1",
    )


__all__ = [
    "CUARTELES_PATH",
    "Cuartel",
    "CuartelesArtifact",
    "cargar_artefacto",
    "cuartel_nombrado",
    "punto_de_cuartel",
]
