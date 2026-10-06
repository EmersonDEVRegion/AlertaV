"""Prueba un cruce contra Nominatim y Overpass reales, sin tocar la base.

    python -m scripts.probar_cruce "LAS MONJAS" "ANDRES BELLO" --comuna Valparaíso
    python -m scripts.probar_cruce --casos

`--casos` corre los dos despachos del 05-10-2026 que motivaron §K. Imprime el
punto que daba Nominatim, el del cruce y cuánto se movió. Respeta el límite de
1 petición por segundo de los dos servicios.
"""

from __future__ import annotations

import argparse
import asyncio

from app.collectors.nominatim import build_client, geocode
from app.collectors.overpass import distancia_m
from app.core.config import settings

CASOS = [
    ("LAS MONJAS", "ANDRES BELLO", "Valparaíso"),
    ("AVENIDA ALEMANIA", "GUILLERMO RIVERA", "Valparaíso"),
]


async def probar(calle_1: str, calle_2: str, comuna: str) -> None:
    calles = {"street_1": calle_1, "street_2": calle_2, "city": comuna}
    async with build_client() as client:
        punto = await geocode(client, calles, comuna=comuna)
    print(f"\n{calle_1} / {calle_2}, {comuna}")
    if punto is None:
        print("  sin punto")
        return
    print(f"  precisión: {punto.precision} ({punto.provider})")
    print(f"  punto:     {punto.lat:.6f}, {punto.lon:.6f}")
    cruce = punto.cruce or {}
    if cruce.get("nominatim_lat") is not None:
        antes = (cruce["nominatim_lat"], cruce["nominatim_lon"])
        movido = distancia_m(antes, (punto.lat, punto.lon))
        print(f"  Nominatim: {antes[0]:.6f}, {antes[1]:.6f}  → se movió {movido:.0f} m")
    if cruce:
        print(
            f"  cruce:     {cruce.get('via_1')} × {cruce.get('via_2')} "
            f"({cruce.get('metodo')}, {cruce.get('candidatos')} candidato/s)"
        )
    print(
        f"  mapa:      https://www.openstreetmap.org/?mlat={punto.lat}&mlon={punto.lon}#map=18/{punto.lat}/{punto.lon}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calle_1", nargs="?")
    parser.add_argument("calle_2", nargs="?")
    parser.add_argument("--comuna", default="Valparaíso")
    parser.add_argument("--casos", action="store_true")
    args = parser.parse_args()
    settings.OVERPASS_ENABLED = True
    casos = CASOS if args.casos or not args.calle_1 else [(args.calle_1, args.calle_2, args.comuna)]
    for calle_1, calle_2, comuna in casos:
        await probar(calle_1, calle_2 or "", comuna)


if __name__ == "__main__":
    asyncio.run(main())
