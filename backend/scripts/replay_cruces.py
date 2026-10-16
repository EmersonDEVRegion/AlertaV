#!/usr/bin/env python3
"""Mide el índice de esquinas contra despachos reales, sin red y sin base.

    python -m scripts.replay_cruces --indice dist/osm_cruces.csv.gz --despachos despachos.json

`despachos.json` es una lista de objetos con `cuenta`, `street_1`, `street_2`,
`city` y, si los tiene, `lat`, `lon` y `precision` del punto actual. Se saca de
producción con una consulta de sólo lectura (ver `docs/operacion.md`).

Para cada despacho con dos calles busca la esquina en las comunas de la
jurisdicción, igual que `geocode_dispatches`, y cuenta cuántos quedan con una
esquina única, con varias o con ninguna. Si el despacho ya tenía punto, mide
cuánto se mueve. Con `--faltantes` lista los que no encontraron esquina.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.collectors import cruces_locales  # noqa: E402
from app.collectors.geoservices import normalise_text  # noqa: E402
from app.collectors.overpass import distancia_m  # noqa: E402
from app.collectors.traffic.bomberos_10_4_worker import (  # noqa: E402
    comuna_de_handle,
    comunas_alternativas,
)
from app.core.config import settings  # noqa: E402
from app.core.nombres_via import nombre_nucleo, sin_numeracion, ultima_palabra  # noqa: E402
from app.services.osm_cruces_service import iterar_filas  # noqa: E402


def cargar_indice(ruta: Path) -> dict[str, list[tuple]]:
    """Filas del índice agrupadas por última palabra, como lo filtra el SQL."""
    por_ultima: dict[str, list[tuple]] = defaultdict(list)
    for _nodo, lat, lon, a, b, ua, ub, comunas in iterar_filas(ruta.read_bytes()):
        fila = (a, b, lat, lon, tuple(comunas))
        por_ultima[ua].append(fila)
        if ub != ua:
            por_ultima[ub].append(fila)
    return por_ultima


def buscar(indice, calle_1, calle_2, comunas):
    calle_1, calle_2 = sin_numeracion(calle_1), sin_numeracion(calle_2)
    pedidas = {normalise_text(c) for c in comunas if c}
    if not calle_1 or not calle_2 or not pedidas or nombre_nucleo(calle_1) == nombre_nucleo(calle_2):
        return []
    filas = {}
    for ultima in {ultima_palabra(calle_1), ultima_palabra(calle_2)} - {None}:
        for fila in indice.get(ultima, ()):
            if pedidas & set(fila[4]):
                filas[fila] = None
    return cruces_locales.agrupar(
        cruces_locales._clasificar(list(filas), calle_1, calle_2), settings.CRUCES_AGRUPAR_M
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--indice", type=Path, required=True)
    parser.add_argument("--despachos", type=Path, required=True)
    parser.add_argument("--faltantes", action="store_true")
    parser.add_argument(
        "--lejanos", type=float, default=0, help="lista los que se mueven más de N metros"
    )
    args = parser.parse_args(argv)

    indice = cargar_indice(args.indice)
    despachos = json.loads(args.despachos.read_text(encoding="utf-8"))
    resumen: dict[str, Counter] = defaultdict(Counter)
    movidas: list[float] = []
    faltantes = []
    lejanos: list[tuple] = []
    for d in despachos:
        cuenta = d.get("cuenta") or "?"
        r = resumen[cuenta]
        r["despachos"] += 1
        if not d.get("street_1") or not d.get("street_2"):
            r["sin_dos_calles"] += 1
            continue
        principal = d.get("city") or comuna_de_handle(cuenta)
        comunas = [principal, *([] if d.get("city") else comunas_alternativas(cuenta))]
        esquinas = buscar(indice, d["street_1"], d["street_2"], comunas)
        if len(esquinas) == 1:
            r["esquina_unica"] += 1
            if esquinas[0].metodo == cruces_locales.METODO_PARECIDO:
                r["por_parecido"] += 1
            if d.get("lat") is not None:
                movida = distancia_m((d["lat"], d["lon"]), (esquinas[0].lat, esquinas[0].lon))
                movidas.append(movida)
                if args.lejanos and movida > args.lejanos:
                    lejanos.append((round(movida), cuenta, d["street_1"], d["street_2"], esquinas[0]))
        elif esquinas:
            r["varias_esquinas"] += 1
        else:
            r["sin_esquina"] += 1
            faltantes.append((cuenta, d["street_1"], d["street_2"], comunas))

    total = Counter()
    for cuenta, r in sorted(resumen.items()):
        total.update(r)
        print(cuenta, dict(r))
    print("TOTAL", dict(total))
    con_dos = total["despachos"] - total["sin_dos_calles"]
    if con_dos:
        print(f"esquina única: {total['esquina_unica']}/{con_dos} = {total['esquina_unica'] / con_dos:.0%}")
    if movidas:
        cuartiles = statistics.quantiles(movidas, n=4)
        print(
            f"el punto se mueve (m): mediana {statistics.median(movidas):.0f}, "
            f"p25 {cuartiles[0]:.0f}, p75 {cuartiles[2]:.0f}, máx {max(movidas):.0f}"
        )
    for lejano in sorted(lejanos, reverse=True):
        print("LEJOS", *lejano)
    if args.faltantes:
        for falta in faltantes:
            print("SIN ESQUINA", *falta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
