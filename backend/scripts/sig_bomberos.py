#!/usr/bin/env python3
"""Instantánea de los cuarteles de Bomberos de la V Región, desde el SIG oficial.

    python -m scripts.sig_bomberos
    python -m scripts.sig_bomberos --dry-run
    python -m scripts.sig_bomberos --out static/geo/cuarteles_valpo.json

Qué es, y por qué no va por el pipeline de cinco minutos
========================================================
`sig.bomberos.cl` (Bomberos de Chile) muestra en un mapa los Cuerpos, sus
compañías y los grifos. No tiene API: la página carga dos archivos JSON
estáticos, `/cuerpos.json` y `/compannias.json`, con nombre, dirección, comuna
y coordenadas. Los grifos no interesan acá.

Es un dato que cambia cuando se inaugura o se muda un cuartel, o sea casi
nunca: la build del SIG que se leyó el 2026-09-30 es del 29-05-2025. Igual que
la amenaza sísmica (`fetch_seismic_hazard.py`), se baja a mano, se recorta a la
región y queda versionado en el repositorio. Se vuelve a correr cuando haga
falta.

Para qué se usa
===============
* **La capa «Cuarteles de Bomberos»** del mapa y los «cuarteles cercanos» de
  cada lugar guardado (frontend, vía `GET /api/v1/events/cuarteles`).
* **Ubicar despachos cuya dirección es un cuartel** («Quinta Compañía de
  Bomberos Quilpue», «Comandancia Cuerpo de Bomberos Quilpué», «CUARTEL
  GENERAL»), que Nominatim no encuentra. Ver `collectors/cuarteles.py`.

Lo que se sabe que le falta
===========================
Está incompleto: Quillota trae 3 compañías y Wurtlitzer dice 5. Se sirve igual,
citando la fuente y la fecha, porque un cuartel que falta es un hueco visible y
uno inventado sería peor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE = "https://sig.bomberos.cl"
REGION = "Valparaíso"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "static" / "geo" / "cuarteles_valpo.json"

#: Caja de la V Región continental, con margen. Isla de Pascua y Juan Fernández
#: son de la región pero quedan fuera del mapa: su cuartel no sirve de nada a
#: quien mira Valparaíso, y estirar la capa hasta el Pacífico sí estorba.
_LAT = (-33.95, -32.0)
_LON = (-72.0, -69.9)

_ORDINALES = {
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5,
    "sexta": 6, "septima": 7, "setima": 7, "octava": 8, "novena": 9,
    "decima": 10, "undecima": 11, "decimoprimera": 11, "duodecima": 12,
    "decimosegunda": 12, "decimotercera": 13, "decimocuarta": 14,
    "decimoquinta": 15, "decimosexta": 16, "decimoseptima": 17,
    "decimoctava": 18, "decimonovena": 19, "vigesima": 20,
}


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def numero_de_compania(nombre: str) -> int | None:
    """«Quinta de Quilpué» → 5; «12a Compañía» → 12. None si no se deduce."""
    plano = _plano(nombre)
    primera = plano.split(" ", 1)[0].rstrip(".,")
    if primera in _ORDINALES:
        return _ORDINALES[primera]
    encontrado = re.match(r"^(\d{1,2})\s*(?:a|ra|da|ta|va|na|°|º)?\b", plano)
    if encontrado:
        return int(encontrado.group(1))
    return None


def _coordenadas(item: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(item.get("lat"))  # type: ignore[arg-type]
        lon = float(item.get("lng"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (_LAT[0] <= lat <= _LAT[1] and _LON[0] <= lon <= _LON[1]):
        return None
    return (round(lat, 6), round(lon, 6))


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    limpio = " ".join(str(valor).split())
    return limpio or None


def construir(cuerpos: list[dict[str, Any]], companias: list[dict[str, Any]]) -> dict[str, Any]:
    """Los dos JSON del SIG → un FeatureCollection de la V Región continental."""
    features: list[dict[str, Any]] = []
    descartados = 0

    for item in cuerpos:
        if item.get("region") != REGION:
            continue
        punto = _coordenadas(item)
        if punto is None:
            descartados += 1
            continue
        atendidas = [c.strip() for c in str(item.get("comunasAtendidas") or "").split(",") if c.strip()]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [punto[1], punto[0]]},
            "properties": {
                "tipo": "cuerpo",
                "nombre": f"Cuerpo de Bomberos de {_texto(item.get('nombreCuerpo')) or _texto(item.get('nombre'))}",
                "cuerpo": _texto(item.get("nombreCuerpo")) or _texto(item.get("nombre")),
                "numero": None,
                "direccion": _texto(item.get("direccion")),
                "comuna": _texto(item.get("comuna")),
                "comunas_atendidas": atendidas,
                "telefono": _texto(item.get("telefono")),
            },
        })

    for item in companias:
        if item.get("region") != REGION:
            continue
        punto = _coordenadas(item)
        if punto is None:
            descartados += 1
            continue
        nombre = _texto(item.get("nombre")) or "Compañía"
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [punto[1], punto[0]]},
            "properties": {
                "tipo": "compania",
                "nombre": nombre,
                "cuerpo": _texto(item.get("nombreCuerpo")),
                "numero": numero_de_compania(nombre),
                "direccion": _texto(item.get("direccion")),
                "comuna": _texto(item.get("comuna")),
                "comunas_atendidas": [],
                "telefono": _texto(item.get("telefono")),
            },
        })

    features.sort(key=lambda f: (
        f["properties"]["cuerpo"] or "",
        f["properties"]["tipo"] != "cuerpo",
        f["properties"]["numero"] or 99,
        f["properties"]["nombre"],
    ))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "fuente": "SIG Bomberos de Chile (sig.bomberos.cl)",
            "region": REGION,
            "generado": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "cuerpos": sum(1 for f in features if f["properties"]["tipo"] == "cuerpo"),
            "companias": sum(1 for f in features if f["properties"]["tipo"] == "compania"),
            "descartados_fuera_de_la_region_continental": descartados,
        },
        "features": features,
    }


def descargar() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": "AlertaV/1.0 (instantanea de cuarteles)"}) as client:
        cuerpos = client.get(f"{BASE}/cuerpos.json")
        cuerpos.raise_for_status()
        companias = client.get(f"{BASE}/compannias.json")
        companias.raise_for_status()
    return (cuerpos.json(), companias.json())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="no escribe el archivo")
    args = parser.parse_args(argv)

    cuerpos, companias = descargar()
    coleccion = construir(cuerpos, companias)
    meta = coleccion["metadata"]
    if meta["cuerpos"] == 0 or meta["companias"] == 0:
        print("El SIG no devolvió cuerpos o compañías de la región: no se escribe nada.", file=sys.stderr)
        return 1
    sin_numero = [f["properties"]["nombre"] for f in coleccion["features"]
                  if f["properties"]["tipo"] == "compania" and f["properties"]["numero"] is None]
    print(f"{meta['cuerpos']} cuerpos y {meta['companias']} compañías "
          f"({meta['descartados_fuera_de_la_region_continental']} fuera de la región continental).")
    if sin_numero:
        print(f"Compañías sin número deducible: {', '.join(sin_numero)}")
    if args.dry_run:
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(coleccion, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Escrito {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
