"""Runner CLI de los collectors.

    # una pasada de todos los collectors
    python -m app.collectors.runner

    # sólo algunos
    python -m app.collectors.runner --collector conaf_incendios --collector senapred_alertas

    # bucle continuo, cada collector con su propia cadencia
    python -m app.collectors.runner --loop

    # bucle continuo forzando un intervalo común
    python -m app.collectors.runner --loop --interval 900

    # ver la cadencia configurada sin ejecutar nada
    python -m app.collectors.runner --show-schedule

Se ejecuta como proceso aparte de la API a propósito: la recolección no debe
competir por los workers que atienden a los ciudadanos, ni caerse con un
redeploy del backend.

En modo `--loop` cada collector corre en su propia tarea, con su propio
intervalo. Un incendio de CONAF cambia de estado en minutos; una pasada de
satélite de FIRMS ocurre unas pocas veces al día. Forzar una cadencia común
significaría o malgastar cuota o llegar tarde.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import sys
from collections.abc import Sequence

from app.collectors.base import CollectorResult
from app.collectors.registry import (
    available_collectors,
    collector_class,
    get_collector,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.logging import configure_logging
from app.models.enums import CollectorStatus
from app.services.ingest_service import IngestService

logger = logging.getLogger("alertav.runner")

_shutdown = asyncio.Event()

#: Dispersión aleatoria del primer disparo de cada collector, como fracción del
#: intervalo. Evita que todos golpeen los servicios institucionales en el mismo
#: segundo tras un reinicio.
_STARTUP_JITTER = 0.25


async def run_collector(name: str) -> CollectorResult:
    """Ejecuta un collector en su propia sesión y garantiza que quede trazado.

    Si la construcción del collector falla —falta una MAP_KEY, una URL mal
    declarada— igual se escribe una fila en `collector_runs`. Un collector que
    nunca llega a arrancar es indistinguible, desde los datos, de uno que corrió
    y no encontró nada: esa ambigüedad es justo lo que la tabla existe para
    eliminar.
    """
    async with AsyncSessionLocal() as session:
        try:
            collector = get_collector(name, session)
        except Exception as exc:
            return await _record_bootstrap_failure(session, name, exc)
        return await collector.run()


async def _record_bootstrap_failure(session, name: str, exc: Exception) -> CollectorResult:
    message = f"{type(exc).__name__}: {exc}"
    logger.exception("no se pudo construir el collector", extra={"collector": name})

    try:
        klass = collector_class(name)
    except KeyError:
        return CollectorResult(
            collector=name,
            source=None,  # type: ignore[arg-type]
            status=CollectorStatus.FAILED,
            error=message,
        )

    service = IngestService(session)
    run = await service.start_run(
        source=klass.source, collector=name, params={"bootstrap": "failed"}
    )
    await service.finish_run(run, status=CollectorStatus.FAILED, error=message)
    return CollectorResult(
        collector=name,
        source=klass.source,
        status=CollectorStatus.FAILED,
        error=message,
    )


async def run_once(names: Sequence[str] | None = None) -> list[CollectorResult]:
    """Ejecuta los collectors indicados (todos si `names` es None)."""
    selected = list(names) if names else available_collectors()
    return [await run_collector(name) for name in selected]


def interval_for(name: str, override: int | None = None) -> int:
    """Cadencia de un collector: la suya, salvo que se fuerce una global."""
    if override:
        return override
    try:
        return max(30, collector_class(name).poll_interval_seconds())
    except KeyError:
        return override or settings.FIRMS_POLL_INTERVAL_SECONDS


def schedule(names: Sequence[str] | None = None, override: int | None = None) -> dict[str, int]:
    selected = list(names) if names else available_collectors()
    return {name: interval_for(name, override) for name in selected}


async def _collector_loop(name: str, interval: int) -> None:
    """Bucle de un único collector. Aislado: si revienta, no arrastra a los demás."""
    delay = random.uniform(0, interval * _STARTUP_JITTER)
    logger.info(
        "collector programado",
        extra={"collector": name, "interval_s": interval, "first_run_in_s": round(delay)},
    )
    try:
        await asyncio.wait_for(_shutdown.wait(), timeout=delay)
        return  # apagado durante el arranque escalonado
    except TimeoutError:
        pass

    while not _shutdown.is_set():
        try:
            await run_collector(name)
        except Exception:
            # `run_collector` ya traza los fallos de la fuente; llegar acá
            # significa un fallo de infraestructura (base de datos caída, por
            # ejemplo). Se registra y se reintenta en el siguiente ciclo.
            logger.exception("ciclo del collector falló", extra={"collector": name})

        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("collector detenido", extra={"collector": name})


async def run_loop(names: Sequence[str] | None, interval: int | None = None) -> None:
    plan = schedule(names, interval)
    logger.info("runner iniciado", extra={"schedule": plan})
    await asyncio.gather(
        *(_collector_loop(name, seconds) for name, seconds in plan.items())
    )
    logger.info("runner detenido")


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: _shutdown.set())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlertaV — runner de collectors")
    parser.add_argument(
        "--collector",
        action="append",
        dest="collectors",
        choices=available_collectors(),
        help="Collector a ejecutar. Repetible. Por defecto: todos.",
    )
    parser.add_argument("--loop", action="store_true", help="Ejecución continua.")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=(
            "Fuerza un intervalo común en segundos. Por defecto cada collector "
            "usa su propia cadencia (*_POLL_INTERVAL_SECONDS)."
        ),
    )
    parser.add_argument(
        "--show-schedule",
        action="store_true",
        help="Imprime la cadencia configurada y termina.",
    )
    return parser.parse_args(argv)


async def _main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    _install_signal_handlers(asyncio.get_running_loop())

    if args.show_schedule:
        for name, seconds in schedule(args.collectors, args.interval).items():
            print(f"{name:<24} cada {seconds}s")
        return 0

    try:
        if args.loop:
            await run_loop(args.collectors, args.interval)
            return 0

        results = await run_once(args.collectors)
        for result in results:
            print(
                f"{result.collector:<24} {result.status.value:<8} "
                f"fetched={result.fetched:<5} inserted={result.inserted:<5} "
                f"dup={result.duplicated:<5} rejected={result.rejected}"
                + (f"  error={result.error}" if result.error else "")
            )
        return 0 if all(r.status.value != "failed" for r in results) else 1
    finally:
        await dispose_engine()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
