#!/usr/bin/env python3
"""Instantánea de las zonas de pronóstico de la DMC para la V Región.

    python -m scripts.dmc_zonas
    python -m scripts.dmc_zonas --desde ruta/coordenadas_zonas_DMC_v3.geojson
    python -m scripts.dmc_zonas --out static/geo/dmc_zonas_valpo.json

Qué es
======
El mapa «Sistema de Alerta Meteorológica» de la DMC dibuja sus avisos sobre
87 zonas de pronóstico (`coordenadas_zonas_DMC_v3.geojson`, mismo directorio
que `datos_AAA.json`). Cada zona tiene `indice` = `<región>_<zona>`; los avisos
por zonas las nombran así en `dataZonaAfecta`.

Cambian cuando la DMC rehace su zonificación (el nombre del archivo lleva la
versión, `v3`), o sea casi nunca. Igual que los cuarteles y la amenaza sísmica,
se baja a mano, se recorta a la región y queda versionado. Si la DMC publica
una `v4`, se corre de nuevo con la URL nueva.

Se queda sólo con la región continental (`05_*`): Juan Fernández e Isla de
Pascua están fuera por decisión del proyecto. Las coordenadas se redondean a
5 decimales (~1 m), de sobra para un polígono que mide decenas de kilómetros.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

URL = "https://archivos.meteochile.gob.cl/portaldmc/AAA/coordenadas_zonas_DMC_v3.geojson"
REGION = "05"
DECIMALES = 5
SALIDA = Path(__file__).resolve().parents[1] / "static" / "geo" / "dmc_zonas_valpo.json"


def _redondear(coords: Any) -> Any:
    if isinstance(coords, list) and coords and isinstance(coords[0], int | float):
        return [round(float(c), DECIMALES) for c in coords[:2]]
    return [_redondear(c) for c in coords]


def recortar(coleccion: dict[str, Any], *, region: str = REGION) -> dict[str, Any]:
    """Las zonas de `region`, con `zona` y `indice` y la geometría redondeada."""
    features = []
    for feature in coleccion.get("features", []):
        props = feature.get("properties") or {}
        if props.get("region") != region or not props.get("zona"):
            continue
        geometria = feature.get("geometry") or {}
        if geometria.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"zona": props["zona"], "indice": props.get("indice")},
                "geometry": {
                    "type": geometria["type"],
                    "coordinates": _redondear(geometria["coordinates"]),
                },
            }
        )
    features.sort(key=lambda f: f["properties"]["zona"])
    return {
        "type": "FeatureCollection",
        "fuente": "Dirección Meteorológica de Chile, coordenadas_zonas_DMC_v3.geojson",
        "features": features,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desde", help="GeoJSON local en vez de bajarlo de la DMC.")
    parser.add_argument("--out", default=str(SALIDA))
    args = parser.parse_args(argv)

    if args.desde:
        texto = Path(args.desde).read_text(encoding="utf-8-sig")
    else:
        agente = {"User-Agent": "AlertaV (github.com/EmersonDEVRegion/AlertaV)"}
        pedido = urllib.request.Request(URL, headers=agente)
        with urllib.request.urlopen(pedido, timeout=60) as respuesta:
            texto = respuesta.read().decode("utf-8-sig")
    recorte = recortar(json.loads(texto))
    if not recorte["features"]:
        print("no hay zonas de la región en el archivo", file=sys.stderr)
        return 1
    Path(args.out).write_text(
        json.dumps(recorte, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    zonas = ", ".join(f["properties"]["zona"] for f in recorte["features"])
    print(f"{len(recorte['features'])} zonas ({zonas}) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
