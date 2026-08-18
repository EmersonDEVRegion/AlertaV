#!/usr/bin/env python3
"""Verificación en vivo de las fuentes institucionales. NO toca la base de datos.

    python scripts/check_sources.py
    python scripts/check_sources.py --collector conaf
    python scripts/check_sources.py --sample 5

Sirve para dos cosas distintas:

  1. Antes de levantar el runner, confirmar que las URLs declaradas en el `.env`
     responden y que el esquema de campos sigue siendo el esperado.
  2. Cuando `collector_runs` empiece a mostrar corridas `partial` o `failed`,
     reproducir el problema en segundos y con el mensaje de error completo, sin
     depender de los logs del proceso.

Ejecuta `fetch()` y `normalize()` reales; lo único que no hace es escribir.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.collectors.conaf.client import ConafClient
from app.collectors.conaf.collector import ConafCollector, ConafMapping
from app.collectors.geoservices import GeoFeature
from app.collectors.senapred.client import SenapredClient
from app.collectors.senapred.collector import SenapredCollector, SenapredMapping
from app.core.config import settings
from app.core.exceptions import AlertaVError
from app.core.logging import configure_logging
from app.schemas.event import EventCreate

CAMPOS_ESPERADOS = {
    "conaf": ("id", "nombre", "estado", "f_inicio", "region", "comuna"),
    "senapred": ("Region", "Alerta", "Evento", "Comunas", "Ambito", "Fecha"),
}


def _titulo(texto: str) -> None:
    print("\n" + "═" * 72)
    print(texto)
    print("═" * 72)


def _revisar_esquema(features: list[GeoFeature], clave: str) -> list[str]:
    """Avisa si desaparecieron campos que el mapeo da por sentados."""
    if not features:
        return []
    presentes = {str(key).lower() for key in features[0].properties}
    faltantes = [
        campo for campo in CAMPOS_ESPERADOS[clave] if campo.lower() not in presentes
    ]
    return faltantes


def _mostrar(eventos: list[EventCreate], limite: int) -> None:
    for evento in eventos[:limite]:
        coordenadas = (
            f"({evento.lat:.4f}, {evento.lon:.4f})" if evento.has_location else "sin coordenadas"
        )
        print(f"  · {evento.external_id}")
        print(
            f"    tipo={evento.type.value}  confianza={evento.confidence}  "
            f"{evento.timestamp.isoformat()}  {coordenadas}  in_region={evento.in_region}"
        )
        print(f"    {(evento.text or '')[:120]}")


async def revisar_conaf(limite: int) -> bool:
    _titulo("CONAF — incendios forestales")
    collector = ConafCollector.__new__(ConafCollector)
    collector._mapping = ConafMapping.from_settings()
    collector.lookback_days = settings.CONAF_LOOKBACK_DAYS

    try:
        collector.client = ConafClient()
        features = await collector.fetch()
    except AlertaVError as exc:
        print(f"  FALLO: {exc.message}")
        if exc.detail:
            print(f"  detalle: {exc.detail}")
        return False

    eventos = collector.normalize(features)
    faltantes = _revisar_esquema(features, "conaf")

    print(f"  fuentes        : {[spec.label for spec in collector.client.sources]}")
    print(f"  ventana        : últimos {collector.lookback_days} días")
    print(f"  features país  : {len(features)}")
    print(f"  eventos {'/'.join(settings.CONAF_REGIONS) or 'todas'}: {len(eventos)}")
    if faltantes:
        print(f"  ATENCIÓN campos ausentes en la capa: {faltantes}")
    for advertencia in collector.warnings:
        print(f"  advertencia    : {advertencia}")
    _mostrar(eventos, limite)
    return not faltantes


async def revisar_senapred(limite: int) -> bool:
    _titulo("SENAPRED — alertas vigentes")
    collector = SenapredCollector.__new__(SenapredCollector)
    collector._mapping = SenapredMapping.from_settings()

    try:
        collector.client = SenapredClient()
        features = await collector.fetch()
    except AlertaVError as exc:
        print(f"  FALLO: {exc.message}")
        if exc.detail:
            print(f"  detalle: {exc.detail}")
        return False

    eventos = collector.normalize(features)
    faltantes = _revisar_esquema(features, "senapred")

    print(f"  fuentes        : {[spec.label for spec in collector.client.sources]}")
    print(f"  alertas país   : {len(features)}")
    print(f"  eventos {'/'.join(settings.SENAPRED_REGIONS) or 'todas'}: {len(eventos)}")
    if faltantes:
        print(f"  ATENCIÓN campos ausentes en la capa: {faltantes}")
    for advertencia in collector.warnings:
        print(f"  advertencia    : {advertencia}")

    print("  panorama nacional:")
    for feature in features[:15]:
        print(
            f"    {feature.get('Region', 'region'):<16} "
            f"{feature.get('Alerta', 'alerta'):<28} "
            f"{feature.get('Evento', 'evento')}"
        )
    _mostrar(eventos, limite)
    return not faltantes


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Chequeo en vivo de las fuentes")
    parser.add_argument("--collector", choices=("conaf", "senapred"), action="append")
    parser.add_argument("--sample", type=int, default=3, help="Eventos a mostrar.")
    args = parser.parse_args()

    configure_logging()
    seleccion = args.collector or ["conaf", "senapred"]
    resultados = []

    if "conaf" in seleccion:
        resultados.append(await revisar_conaf(args.sample))
    if "senapred" in seleccion:
        resultados.append(await revisar_senapred(args.sample))

    print()
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
