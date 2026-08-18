"""Worker del motor de correlación.

    # una pasada
    python -m app.services.correlation.runner

    # bucle continuo
    python -m app.services.correlation.runner --loop

    # calibrar sin escribir el .env
    python -m app.services.correlation.runner --radius-m 2000 --window-hours 3

Proceso aparte de la API, por la misma razón que los collectors: una pasada de
correlación abre transacciones largas sobre la tabla de señales y no debe
competir por los workers que atienden a los ciudadanos, ni caerse con un
redeploy del backend.

En producción sobre la capa gratuita comparte proceso con los collectors —ver
`app/workers.py`—, no con la API. Este módulo sigue siendo ejecutable por su
cuenta, que es como se calibran el radio y la ventana contra datos reales.

Cadencia recomendada: **entre 60 y 300 segundos**. El motor es idempotente —una
pasada sobre una ventana ya correlacionada no duplica nada— así que el costo de
correr de más es sólo CPU. El costo de correr de menos es que un incendio real
tarde minutos en aparecer en el mapa, que es exactamente lo que este sistema
existe para evitar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence

from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.logging import configure_logging
from app.core.shutdown import (
    install_signal_handlers,
    is_shutting_down,
    sleep_unless_stopped,
)
from app.services.correlation.engine import CorrelationEngine, CorrelationPass

logger = logging.getLogger("alertav.correlation")


async def run_once(**overrides: object) -> CorrelationPass:
    """Una pasada en su propia sesión."""
    async with AsyncSessionLocal() as session:
        engine = CorrelationEngine(session, **overrides)  # type: ignore[arg-type]
        return await engine.run()


async def run_loop(interval: int, **overrides: object) -> None:
    logger.info("motor de correlación iniciado", extra={"interval_s": interval})
    while not is_shutting_down():
        try:
            await run_once(**overrides)
        except Exception:
            # Una pasada revienta entera y se revierte entera (ver
            # `CorrelationEngine.run`). Llegar acá significa que la base o la red
            # fallaron; se registra y se reintenta en el ciclo siguiente en vez
            # de tumbar el worker y dejar el mapa congelado.
            logger.exception("pasada de correlación fallida; se reintenta")

        if not await sleep_unless_stopped(interval):
            break
    logger.info("motor de correlación detenido")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlertaV — motor de correlación")
    parser.add_argument("--loop", action="store_true", help="Ejecución continua.")
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.CORRELATION_POLL_INTERVAL_SECONDS,
        help="Segundos entre pasadas en modo --loop.",
    )
    parser.add_argument(
        "--radius-m",
        type=float,
        default=None,
        help="Radio de agrupación en metros. Por defecto, CORRELATION_RADIUS_M.",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=None,
        help="Ventana de señales a considerar en cada pasada.",
    )
    parser.add_argument(
        "--min-signals",
        type=int,
        default=None,
        help=(
            "Señales mínimas para abrir un incidente desde fuentes no "
            "confirmatorias. Una fuente confirmatoria abre incidente siempre."
        ),
    )
    parser.add_argument(
        "--attach-regional-alerts",
        action="store_true",
        default=None,
        help=(
            "Adosar también las alertas de ámbito regional o nacional. "
            "Desactivado por defecto: una alerta preventiva nacional está "
            "vigente toda la temporada y teñiría el mapa entero."
        ),
    )
    return parser.parse_args(argv)


def _overrides(args: argparse.Namespace) -> dict[str, object]:
    candidates = {
        "radius_m": args.radius_m,
        "window_hours": args.window_hours,
        "min_signals": args.min_signals,
        "attach_regional_alerts": args.attach_regional_alerts,
    }
    return {key: value for key, value in candidates.items() if value is not None}


async def _main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    install_signal_handlers()
    overrides = _overrides(args)

    try:
        if args.loop:
            await run_loop(max(15, args.interval), **overrides)
            return 0

        result = await run_once(**overrides)
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return 0
    finally:
        await dispose_engine()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
