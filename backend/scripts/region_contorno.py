#!/usr/bin/env python3
"""Contorno de la Región de Valparaíso continental y la viñeta del mapa.

    python -m scripts.region_contorno
    python -m scripts.region_contorno --out ../frontend/src/data/region_valpo.json

Qué es
======
La PWA apaga lo que queda fuera de la región con una viñeta: transparente en
el borde y cada vez más oscura hasta `VINETA_KM`. Así se ve la región como
iluminada y lo vecino (Los Vilos, Tiltil, Colina) como contexto apagado, sin
el escalón de una máscara pareja, que se leía como un mapa de videojuego.

El archivo trae:

- `region`: la unión de las 36 comunas continentales de
  `migrations/data/comunas_v_region.geojson` (las mismas de la tabla
  `comunas_region`). Lo usan el borde dibujado y «¿está en la región?». Las
  comunas se simplificaron una por una, así que sus bordes compartidos no
  calzan exacto y la unión deja rendijas: se cierran engordando cada comuna
  ~60 m, uniendo y adelgazando lo mismo. Después se descartan los islotes de
  menos de 2 km² y los agujeros, y se simplifica a ~150 m.
- `vineta`: un rectángulo enorme con un agujero en la región engordada a cada
  distancia de `ANILLOS_KM`. Los rectángulos se apilan: un punto a 20 km del
  borde queda bajo todos los que tienen el agujero más chico que 20 km. Cada
  uno lleva `f` (la oscuridad buscada entre su distancia y la siguiente, de 0
  a 1) y `fp` (la del anterior); la PWA saca de ahí cuánto pinta cada capa para
  que el apilado dé `f` × el tope del tema. Apilar en vez de cortar anillos
  guarda cada borde una sola vez.

El mar recibe el mismo degradado que la tierra. Las distancias se miden en
kilómetros de verdad: se encoge la longitud por el coseno de la latitud antes
de engordar y se estira después.

Requiere `shapely` (`scripts/requirements-osm.txt`). Se corre a mano cuando
cambia el archivo de comunas o la forma de la viñeta.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
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

#: Hasta dónde llega el degradado.
VINETA_KM = 40.0
#: Distancias de cada escalón. Cada 3 km: con el tope de la PWA (~0,8) ningún
#: salto pasa de ~0,1 de opacidad y el ojo no los separa.
ANILLOS_KM = [3.0 * i for i in range(1, 14)] + [VINETA_KM]
#: Los bordes engordados son curvas suaves: ~300 m de detalle y 3 decimales
#: (~100 m) bastan, y el archivo queda en una fracción.
TOLERANCIA_VINETA = 0.003
DECIMALES_VINETA = 3
#: Lo que cubre el rectángulo exterior: mucho más que todo lo que el mapa deja
#: mirar, para que nunca se vea su borde.
AFUERA = box(-90.0, -60.0, -50.0, -10.0)
#: Latitud media de la región, para pasar kilómetros a grados.
LAT_MEDIA = -33.0
KM_POR_GRADO = 111.32


def _redondear(coords: Any, decimales: int) -> Any:
    if isinstance(coords, tuple | list) and coords and isinstance(coords[0], int | float):
        return [round(float(c), decimales) for c in coords[:2]]
    return [_redondear(c, decimales) for c in coords]


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


def engordar(region: Polygon | MultiPolygon, km: float) -> Polygon | MultiPolygon:
    """La región engordada `km` kilómetros de verdad en todas direcciones."""
    escala = math.cos(math.radians(LAT_MEDIA))
    plana = affinity.scale(region, xfact=escala, yfact=1.0, origin=(0, 0))
    gorda = plana.buffer(km / KM_POR_GRADO, quad_segs=8)
    vuelta = affinity.scale(gorda, xfact=1 / escala, yfact=1.0, origin=(0, 0))
    return vuelta.simplify(TOLERANCIA_VINETA, preserve_topology=True)


def oscuridad(km: float) -> float:
    """De 0 en el borde a 1 en `VINETA_KM`: suave al empezar y al terminar."""
    t = min(max(km / VINETA_KM, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def vineta(region: Polygon | MultiPolygon) -> list[dict[str, Any]]:
    """Un rectángulo con agujero por distancia, del agujero más chico al más grande."""
    capas: list[dict[str, Any]] = []
    anterior = 0.0
    for i, km in enumerate(ANILLOS_KM):
        siguiente = ANILLOS_KM[i + 1] if i + 1 < len(ANILLOS_KM) else km
        # La oscuridad del tramo [km, siguiente): la de su punto medio; la
        # última capa cubre todo lo de más allá, al tope.
        f = 1.0 if km >= VINETA_KM else round(oscuridad((km + siguiente) / 2), 4)
        hueco = engordar(region, km)
        huecos = list(hueco.geoms) if isinstance(hueco, MultiPolygon) else [hueco]
        geometria = orient(Polygon(AFUERA.exterior, [h.exterior for h in huecos]))
        capas.append(
            {
                "type": "Feature",
                "properties": {"tipo": "vineta", "km": km, "f": f, "fp": anterior},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": _redondear(
                        mapping(geometria)["coordinates"], DECIMALES_VINETA
                    ),
                },
            }
        )
        anterior = f
    return capas


def coleccion(region: Polygon | MultiPolygon) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "metadata": {
            "nombre": "Región de Valparaíso (continental)",
            "fuente": "BCN, División Político-Administrativa (comunas_v_region.geojson)",
            "generado_por": "backend/scripts/region_contorno.py",
            "vineta_km": VINETA_KM,
        },
        "features": [
            {
                "type": "Feature",
                "properties": {"tipo": "region"},
                "geometry": {
                    "type": region.geom_type,
                    "coordinates": _redondear(mapping(region)["coordinates"], DECIMALES),
                },
            },
            *vineta(region),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--desde", type=Path, default=ENTRADA)
    parser.add_argument("--out", type=Path, default=SALIDA)
    args = parser.parse_args()

    geometria = contorno(json.loads(args.desde.read_text(encoding="utf-8")))
    salida = coleccion(geometria)
    texto = json.dumps(salida, ensure_ascii=False, separators=(",", ":")) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(texto, encoding="utf-8")
    oeste, sur, este, norte = geometria.bounds
    print(
        f"{args.out}: {len(salida['features']) - 1} capas de viñeta, {len(texto)} bytes, "
        f"caja [{oeste:.3f}, {sur:.3f}, {este:.3f}, {norte:.3f}]"
    )


if __name__ == "__main__":
    main()
