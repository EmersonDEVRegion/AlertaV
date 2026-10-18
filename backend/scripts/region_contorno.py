#!/usr/bin/env python3
"""Contorno de la Región de Valparaíso continental para la máscara del mapa.

    python -m scripts.region_contorno
    python -m scripts.region_contorno --out ../frontend/src/data/region_valpo.json

Qué es
======
La PWA apaga todo lo que queda fuera de la región con una máscara casi opaca y
dibuja su borde con una línea fina. Para eso necesita un solo polígono: la unión
de las 36 comunas continentales de `migrations/data/comunas_v_region.geojson`
(las mismas de la tabla `comunas_region`).

Las comunas se simplificaron una por una, así que sus bordes compartidos no
calzan exacto y la unión deja rendijas. Se cierran engordando cada comuna
~60 m, uniendo y adelgazando lo mismo. Después se descartan los islotes de
menos de 2 km² y los agujeros, y se simplifica a ~150 m: el borde se ve desde
zoom 7 a 17 y no necesita más. Queda en ~17 KB y va dentro del bundle (sin
pedido extra, funciona sin conexión).

El archivo trae dos rasgos:

- `region`: el contorno. Lo usan el borde dibujado y «¿está en la región?».
- `visible`: el contorno más el mar de enfrente, que es lo que la máscara deja
  sin tapar. Sin el mar, la máscara cortaba los rótulos de las ciudades de la
  costa (Valparaíso, San Antonio, Quintero), que se dibujan sobre el agua. El
  mar es una franja de ~40 km que sigue la costa, entre la desembocadura del
  Rapel y el límite con Coquimbo: no se destapa tierra de otra región.

Requiere `shapely` (`scripts/requirements-osm.txt`). Se corre a mano cuando
cambia el archivo de comunas, o sea casi nunca.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

RAIZ = Path(__file__).resolve().parents[1]
ENTRADA = RAIZ / "migrations" / "data" / "comunas_v_region.geojson"
SALIDA = RAIZ.parent / "frontend" / "src" / "data" / "region_valpo.json"

#: ~60 m en grados a la latitud de la región: cierra las rendijas entre comunas.
COSTURA = 0.0006
#: ~150 m: el detalle que se alcanza a ver del borde.
TOLERANCIA = 0.0015
#: Islotes menores que esto (en grados², ~2 km²) no aportan nada al borde.
AREA_MINIMA = 0.0002
DECIMALES = 4
#: Latitudes donde la costa de la región empieza y termina: el límite con
#: Coquimbo (al sur de Pichidangui) y la desembocadura del Rapel.
COSTA_NORTE = -32.177
COSTA_SUR = -33.918
#: Ancho del mar destapado, en grados (~40 km): lo que ocupa el rótulo de una
#: ciudad de la costa al zoom mínimo, que va a la izquierda del punto.
MAR_ANCHO = 0.4
#: Borde oeste del polígono con que se arma el mar, antes de recortarlo.
MAR_OESTE = -74.0
#: Paso con que se sigue la costa (~550 m).
PASO_COSTA = 0.005


def _redondear(coords: Any) -> Any:
    if isinstance(coords, tuple | list) and coords and isinstance(coords[0], int | float):
        return [round(float(c), DECIMALES) for c in coords[:2]]
    return [_redondear(c) for c in coords]


def contorno(coleccion: dict[str, Any]) -> Polygon | MultiPolygon:
    """La unión de las comunas, sin rendijas, islotes ni agujeros."""
    comunas = [shape(f["geometry"]) for f in coleccion["features"]]
    union = unary_union([c.buffer(COSTURA) for c in comunas]).buffer(-COSTURA)
    partes = list(union.geoms) if isinstance(union, MultiPolygon) else [union]
    limpias = [Polygon(p.exterior) for p in partes if p.area >= AREA_MINIMA]
    unida = unary_union(limpias).simplify(TOLERANCIA, preserve_topology=True)
    if isinstance(unida, MultiPolygon):
        return MultiPolygon([orient(p) for p in unida.geoms])
    return orient(unida)


def mar_de_enfrente(region: Polygon | MultiPolygon) -> Polygon:
    """La franja de mar de `MAR_ANCHO` frente a la costa de la región.

    La costa, latitud por latitud, es el punto más al oeste de la región en esa
    latitud. Todo lo que queda al oeste de ella, hasta `MAR_ANCHO` de la región,
    es mar: así la franja sigue la forma de la costa.
    """
    costa: list[tuple[float, float]] = []
    lat = COSTA_NORTE
    while lat >= COSTA_SUR:
        corte = LineString([(MAR_OESTE, lat), (-69.0, lat)]).intersection(region)
        if not corte.is_empty:
            costa.append((round(corte.bounds[0], 5), round(lat, 5)))
        lat -= PASO_COSTA
    anillo = [(MAR_OESTE, costa[0][1]), *costa, (MAR_OESTE, costa[-1][1])]
    # El borde del mar toca la costa: se engorda un poco para que la unión no
    # deje una rendija entre los dos.
    return Polygon(anillo).buffer(COSTURA).intersection(region.buffer(MAR_ANCHO))


def _feature(tipo: str, geometria: Polygon | MultiPolygon) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"tipo": tipo},
        "geometry": {
            "type": geometria.geom_type,
            "coordinates": _redondear(mapping(geometria)["coordinates"]),
        },
    }


def coleccion(region: Polygon | MultiPolygon) -> dict[str, Any]:
    visible = unary_union([region, mar_de_enfrente(region)]).simplify(
        TOLERANCIA, preserve_topology=True
    )
    if isinstance(visible, MultiPolygon):
        visible = max(visible.geoms, key=lambda p: p.area)
    visible = orient(Polygon(visible.exterior))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "nombre": "Región de Valparaíso (continental)",
            "fuente": "BCN, División Político-Administrativa (comunas_v_region.geojson)",
            "generado_por": "backend/scripts/region_contorno.py",
        },
        "features": [_feature("region", region), _feature("visible", visible)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--desde", type=Path, default=ENTRADA)
    parser.add_argument("--out", type=Path, default=SALIDA)
    args = parser.parse_args()

    geometria = contorno(json.loads(args.desde.read_text(encoding="utf-8")))
    salida = coleccion(geometria)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(salida, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    oeste, sur, este, norte = geometria.bounds
    partes = len(geometria.geoms) if isinstance(geometria, MultiPolygon) else 1
    print(
        f"{args.out}: {partes} polígono(s), {len(json.dumps(salida))} bytes, "
        f"caja [{oeste:.3f}, {sur:.3f}, {este:.3f}, {norte:.3f}]"
    )


if __name__ == "__main__":
    main()
