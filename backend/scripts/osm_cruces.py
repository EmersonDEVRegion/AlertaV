#!/usr/bin/env python3
"""Índice de cruces de calles de la Región de Valparaíso, desde OpenStreetMap.

    python -m scripts.osm_cruces --pbf chile-latest.osm.pbf --salida dist/

Qué produce
===========
`osm_cruces.csv.gz`: un nodo de OSM por fila donde se tocan dos vías con
nombre distinto, con los dos nombres, la última palabra significativa de cada
uno (la clave de búsqueda) y las comunas a menos de ~100 m. `osm_cruces.json`:
fecha de los datos y cantidad de filas.

Lo corre cada mes `.github/workflows/osm-cruces.yml`, que publica los dos
archivos en el release fijo `osm-cruces`. Los workers de Render lo bajan y
lo cargan a `alertav.osm_cruces` (`app/services/osm_cruces_service.py`).

Por qué así
===========
* **Sin Overpass.** Sus servidores públicos fallan casi siempre desde Render.
  Con el extracto de Geofabrik el cruce se calcula una vez al mes, acá.
* **La normalización es la misma que la de la búsqueda**
  (`app/core/nombres_via.py`): el índice guarda exactamente las claves que la
  búsqueda va a pedir. Por eso este script no importa nada más de `app`.
* **Comunas con holgura.** Los polígonos (`migrations/data/`) están
  simplificados a ~40 m; una esquina en el límite Viña–Valparaíso queda con
  las dos comunas para que la encuentre la central de cualquiera de ellas.

Requiere `osmium` (pyosmium ≥ 4) y `shapely` (`scripts/requirements-osm.txt`).
Datos © OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.core.nombres_via import nombre_nucleo, ultima_palabra  # noqa: E402
from app.core.texto import sin_tildes  # noqa: E402

#: Región continental, con margen: (oeste, sur, este, norte). Lo que cae fuera
#: de toda comuna se descarta después, así que el margen no agrega filas.
CAJA = (-72.0, -34.0, -69.9, -32.0)

#: `highway` que no son vías donde despache una central.
EXCLUIDAS = frozenset(
    {
        "proposed", "construction", "abandoned", "razed", "disused", "platform",
        "bus_stop", "elevator", "raceway", "corridor", "emergency_bay", "rest_area",
        "services", "escape",
    }
)

#: Etiquetas con nombres alternativos. `ref` es la que lleva «F-30-E» o «68».
ETIQUETAS_DE_NOMBRE = (
    "name", "alt_name", "official_name", "short_name", "old_name", "loc_name", "name:es", "ref",
)

#: Holgura de los polígonos comunales, en grados (~100 m).
HOLGURA_COMUNA = 0.001

COLUMNAS = ("nodo", "lat", "lon", "nombre_a", "nombre_b", "ultima_a", "ultima_b", "comunas")


def nombres_de(tags: dict[str, str]) -> tuple[str, ...]:
    """Los nombres de una vía, sin repetir y sin los que normalizan igual."""
    vistos: dict[tuple[str, ...], str] = {}
    for etiqueta in ETIQUETAS_DE_NOMBRE:
        for valor in str(tags.get(etiqueta) or "").split(";"):
            valor = " ".join(valor.split())[:200]
            nucleo = nombre_nucleo(valor)
            if valor and nucleo and ultima_palabra(valor) and nucleo not in vistos:
                vistos[nucleo] = valor
    return tuple(vistos.values())


def dentro(lon: float, lat: float) -> bool:
    oeste, sur, este, norte = CAJA
    return oeste <= lon <= este and sur <= lat <= norte


def leer_vias(pbf: Path) -> tuple[list[tuple[str, ...]], dict[int, list[int]], dict[int, tuple[float, float]], str | None]:
    """Vías con nombre de la caja: sus nombres, qué vías toca cada nodo y dónde está."""
    import osmium

    fecha = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header().get(
        "osmosis_replication_timestamp"
    )
    nombres: list[tuple[str, ...]] = []
    vias_de_nodo: dict[int, list[int]] = defaultdict(list)
    posicion: dict[int, tuple[float, float]] = {}

    # El caché de posiciones ve todos los nodos antes que los filtros: a Python
    # sólo llegan las vías con `highway`, ya con la posición de cada nodo.
    procesador = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for objeto in procesador:
        if not objeto.is_way():
            continue
        tipo = objeto.tags.get("highway")
        if not tipo or tipo in EXCLUIDAS:
            continue
        tags = {t.k: t.v for t in objeto.tags}
        propios = nombres_de(tags)
        if not propios:
            continue
        puntos = [
            (n.ref, n.lon, n.lat)
            for n in objeto.nodes
            if n.location.valid() and dentro(n.lon, n.lat)
        ]
        if not puntos:
            continue
        indice = len(nombres)
        nombres.append(propios)
        for ref, lon, lat in puntos:
            lista = vias_de_nodo[ref]
            if indice not in lista:
                lista.append(indice)
            posicion[ref] = (lat, lon)
    return nombres, vias_de_nodo, posicion, fecha


def cargar_comunas(geojson: Path):
    """STRtree de polígonos comunales con holgura, y el nombre normalizado de cada uno."""
    from shapely import STRtree
    from shapely.geometry import shape

    datos = json.loads(geojson.read_text(encoding="utf-8"))
    poligonos, nombres = [], []
    for feature in datos["features"]:
        poligonos.append(shape(feature["geometry"]).buffer(HOLGURA_COMUNA))
        nombres.append(" ".join(sin_tildes(feature["properties"]["nombre"]).split()))
    return STRtree(poligonos), poligonos, nombres


def cruces(
    nombres: list[tuple[str, ...]],
    vias_de_nodo: dict[int, list[int]],
    posicion: dict[int, tuple[float, float]],
    comunas_de,
):
    """Filas del índice: cada par de nombres distintos que se tocan en un nodo."""
    for nodo, vias in vias_de_nodo.items():
        if len(vias) < 2:
            continue
        lat, lon = posicion[nodo]
        comunas = comunas_de(lon, lat)
        if not comunas:
            continue
        vistos: set[frozenset[tuple[str, ...]]] = set()
        for via_a, via_b in combinations(vias, 2):
            for nombre_a in nombres[via_a]:
                for nombre_b in nombres[via_b]:
                    clave_a, clave_b = nombre_nucleo(nombre_a), nombre_nucleo(nombre_b)
                    par = frozenset((clave_a, clave_b))
                    if clave_a == clave_b or par in vistos:
                        continue
                    vistos.add(par)
                    yield (
                        nodo,
                        round(lat, 7),
                        round(lon, 7),
                        nombre_a,
                        nombre_b,
                        ultima_palabra(nombre_a),
                        ultima_palabra(nombre_b),
                        "|".join(comunas),
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument(
        "--comunas", type=Path, default=RAIZ / "migrations" / "data" / "comunas_v_region.geojson"
    )
    parser.add_argument("--salida", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)

    from shapely.geometry import Point

    arbol, poligonos, nombres_comuna = cargar_comunas(args.comunas)

    def comunas_de(lon: float, lat: float) -> list[str]:
        punto = Point(lon, lat)
        return sorted({nombres_comuna[i] for i in arbol.query(punto) if poligonos[i].contains(punto)})

    nombres, vias_de_nodo, posicion, fecha = leer_vias(args.pbf)
    args.salida.mkdir(parents=True, exist_ok=True)
    destino = args.salida / "osm_cruces.csv.gz"
    filas = 0
    por_comuna: dict[str, int] = defaultdict(int)
    with gzip.open(destino, "wt", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(COLUMNAS)
        for fila in cruces(nombres, vias_de_nodo, posicion, comunas_de):
            escritor.writerow(fila)
            filas += 1
            for comuna in fila[-1].split("|"):
                por_comuna[comuna] += 1

    meta = {
        "filas": filas,
        "vias": len(nombres),
        "datos_de": fecha or None,
        "generado_en": datetime.now(UTC).isoformat(timespec="seconds"),
        "por_comuna": dict(sorted(por_comuna.items())),
        "licencia": "Datos © OpenStreetMap contributors, ODbL 1.0",
    }
    (args.salida / "osm_cruces.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in meta.items() if k != "por_comuna"}, ensure_ascii=False))
    return 0 if filas else 1


if __name__ == "__main__":
    raise SystemExit(main())
