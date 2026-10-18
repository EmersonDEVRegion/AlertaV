#!/usr/bin/env python3
"""Instantánea de los nombres de barrios y sectores de la V Región en OSM.

    python -m scripts.osm_sectores
    python -m scripts.osm_sectores --desde respuesta_overpass.json
    python -m scripts.osm_sectores --out static/geo/osm_sectores_valpo.json

Qué es
======
Las distribuidoras no publican en su archivo abierto qué sectores deja sin luz
un corte (Chilquinta los tiene sólo en una API con llave). Lo que sí publican es
dónde: el área del corte. Con esta lista, `collectors/sectores_osm.py` nombra
los barrios de OpenStreetMap que caen dentro del área, y la ficha los muestra
como lo que son: nombres de OSM, no de la empresa.

Se guardan los lugares con `place` = suburb, quarter, neighbourhood, hamlet,
village o locality y nombre. Ciudades y pueblos (`city`, `town`) quedan fuera:
son la comuna, que la ficha ya dice. Los barrios cambian poco; se corre a mano
cuando haga falta, como las zonas de la DMC y los cuarteles.

Datos © colaboradores de OpenStreetMap, ODbL.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OVERPASS = "https://overpass-api.de/api/interpreter"
#: La misma caja que `REGION_*` en `config.py`.
CAJA = (-33.8, -72.0, -32.0, -69.8)
TIPOS = ("suburb", "quarter", "neighbourhood", "hamlet", "village", "locality")
DECIMALES = 5
SALIDA = Path(__file__).resolve().parents[1] / "static" / "geo" / "osm_sectores_valpo.json"
USER_AGENT = "AlertaV/1.0 (+https://github.com/EmersonDEVRegion/AlertaV)"


def consulta() -> str:
    sur, oeste, norte, este = CAJA
    caja = f"({sur},{oeste},{norte},{este})"
    filtro = f'["place"~"^({"|".join(TIPOS)})$"]["name"]'
    return (
        "[out:json][timeout:120];"
        f"(node{filtro}{caja};way{filtro}{caja};relation{filtro}{caja};);"
        "out center tags;"
    )


def lugares(respuesta: dict[str, Any]) -> list[list[Any]]:
    """Elementos de Overpass → `[nombre, lat, lon, place]`, sin repetidos."""
    vistos: set[tuple[str, float, float]] = set()
    salida: list[list[Any]] = []
    for elemento in respuesta.get("elements", []):
        tags = elemento.get("tags") or {}
        nombre = " ".join(str(tags.get("name") or "").split())
        tipo = tags.get("place")
        centro = elemento.get("center") or elemento
        lat, lon = centro.get("lat"), centro.get("lon")
        if not nombre or tipo not in TIPOS or lat is None or lon is None:
            continue
        clave = (nombre, round(float(lat), 4), round(float(lon), 4))
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append([nombre, round(float(lat), DECIMALES), round(float(lon), DECIMALES), tipo])
    salida.sort(key=lambda fila: (fila[1], fila[2]))
    return salida


def descargar() -> dict[str, Any]:
    cuerpo = urllib.parse.urlencode({"data": consulta()}).encode()
    pedido = urllib.request.Request(OVERPASS, data=cuerpo, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(pedido, timeout=180) as respuesta:
        return json.load(respuesta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desde", type=Path, help="respuesta de Overpass ya descargada")
    parser.add_argument("--out", type=Path, default=SALIDA)
    args = parser.parse_args(argv)

    respuesta = json.loads(args.desde.read_text(encoding="utf-8")) if args.desde else descargar()
    filas = lugares(respuesta)
    if not filas:
        print("Overpass no devolvió lugares; no se escribe nada.", file=sys.stderr)
        return 1
    documento = {
        "fuente": "© colaboradores de OpenStreetMap, ODbL",
        "generado": datetime.now(UTC).date().isoformat(),
        "campos": ["nombre", "lat", "lon", "place"],
        "lugares": filas,
    }
    args.out.write_text(
        json.dumps(documento, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"{len(filas)} lugares en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
