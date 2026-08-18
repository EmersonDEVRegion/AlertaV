"""Los dos motores de fondo dentro de un mismo proceso.

    # ambos motores, cadencias del .env
    python -m app.workers

    # sólo recolección, o sólo correlación
    python -m app.workers --no-correlation
    python -m app.workers --no-collectors

    # forzar cadencias sin tocar el .env
    python -m app.workers --interval 900 --correlation-interval 300

Por qué existe
--------------
La recolección y la correlación son trabajos distintos y en una infraestructura
normal irían en contenedores distintos. Acá comparten proceso por una razón
puramente económica: el Free Instance de Koyeb da 512 MB.

El costo medido de importar SQLAlchemy + GeoAlchemy2 + asyncpg + httpx, con el
proceso ocioso y antes de procesar un solo evento:

    intérprete pelado                    ~9 MB
    worker de correlación               ~46 MB
    worker de collectors (+httpx)       ~53 MB
    proceso de la API (+fastapi)        ~69 MB

Tres procesos parten de ~168 MB; dos, de ~122 MB. El ahorro es de unos **46 MB**
—cerca del 30% del piso—, y crece con el uso: lo que se elimina no es sólo la
copia de los módulos, sino el segundo pool de conexiones y el segundo conjunto
de buffers.

Fusionarlos es barato porque ambos ya eran asíncronos: `run_loop` de cada módulo
es una corrutina que pasa la mayor parte del tiempo esperando —a la red, a la
base, al próximo ciclo—. Dos corrutinas esperando en el mismo event loop no
compiten por nada; es exactamente el caso que asyncio resuelve bien.

El otro beneficio, menos visible pero probablemente más importante en la capa
gratuita: ahora hay **un solo pool de conexiones** en vez de dos. El techo
contra Supabase baja de `(POOL_SIZE + MAX_OVERFLOW) × 3` a `× 2`.

Lo que este módulo NO hace
--------------------------
No toca la API. Uvicorn sigue en su propio proceso, y eso no es negociable: una
pasada de correlación que se cuelgue no puede llevarse por delante el endpoint
que responde a los ciudadanos. La frontera entre "atender consultas" y "trabajar
en segundo plano" es la única que vale la pena pagar con memoria.

Si un motor muere
-----------------
Los dos `run_loop` ya atrapan las excepciones de cada ciclo. Que una llegue
hasta acá significa un fallo estructural —el event loop, el pool, un bug—, y
ante eso este proceso no intenta ser heroico: registra, pide el término del otro
motor y sale con código 1. `start.sh` lo reinicia con backoff. Reiniciar un
proceso limpio es más confiable que reparar uno en estado desconocido.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from app.collectors import runner as collectors_runner
from app.collectors.registry import available_collectors
from app.core.config import settings
from app.core.database import dispose_engine
from app.core.logging import configure_logging
from app.core.shutdown import install_signal_handlers, request_shutdown
from app.services.correlation import runner as correlation_runner

logger = logging.getLogger("alertav.workers")

#: Margen para que los motores terminen su ciclo tras el SIGTERM. La plataforma
#: concede 30 segundos antes del SIGKILL; se dejan 5 de holgura para alcanzar a
#: cerrar el pool de conexiones después de que ambos paren.
_GRACE_SECONDS = 25.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlertaV — collectors y correlación en un solo proceso"
    )
    parser.add_argument(
        "--collector",
        action="append",
        dest="collectors",
        choices=available_collectors(),
        help="Collector a ejecutar. Repetible. Por defecto: todos.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=(
            "Fuerza un intervalo común para los collectors, en segundos. Por "
            "defecto cada uno usa su propia cadencia (*_POLL_INTERVAL_SECONDS)."
        ),
    )
    parser.add_argument(
        "--correlation-interval",
        type=int,
        default=settings.CORRELATION_POLL_INTERVAL_SECONDS,
        help="Segundos entre pasadas del motor de correlación.",
    )
    parser.add_argument(
        "--no-collectors",
        action="store_true",
        help="No levantar la recolección.",
    )
    parser.add_argument(
        "--no-correlation",
        action="store_true",
        help="No levantar el motor de correlación.",
    )
    return parser.parse_args(argv)


def _build_tasks(args: argparse.Namespace) -> dict[str, asyncio.Task[None]]:
    tasks: dict[str, asyncio.Task[None]] = {}

    if not args.no_collectors:
        tasks["collectors"] = asyncio.create_task(
            collectors_runner.run_loop(args.collectors, args.interval),
            name="collectors",
        )
    if not args.no_correlation:
        tasks["correlation"] = asyncio.create_task(
            correlation_runner.run_loop(max(15, args.correlation_interval)),
            name="correlation",
        )
    return tasks


async def _drain(tasks: dict[str, asyncio.Task[None]]) -> None:
    """Espera a que los motores restantes terminen; los cancela si se pasan.

    La cancelación es el último recurso y no es gratis: interrumpe la corrutina
    donde esté, que puede ser en medio de una transacción. Se prefiere siempre
    que el motor note la señal y cierre por su cuenta, y por eso `_GRACE_SECONDS`
    está calibrado contra la ventana real de la plataforma.
    """
    pending = [task for task in tasks.values() if not task.done()]
    if not pending:
        return

    _, still_running = await asyncio.wait(pending, timeout=_GRACE_SECONDS)
    for task in still_running:
        logger.warning(
            "el motor no terminó dentro del margen; se cancela",
            extra={"worker": task.get_name(), "grace_s": _GRACE_SECONDS},
        )
        task.cancel()
    if still_running:
        await asyncio.gather(*still_running, return_exceptions=True)


async def _main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    install_signal_handlers()

    tasks = _build_tasks(args)
    if not tasks:
        logger.error("no hay nada que ejecutar: se desactivaron ambos motores")
        return 2

    logger.info("workers iniciados", extra={"motores": sorted(tasks)})

    failed = False
    try:
        # FIRST_EXCEPTION devuelve apenas un motor revienta; si ninguno lo hace,
        # equivale a esperar a que ambos terminen —el caso del SIGTERM.
        done, _ = await asyncio.wait(
            tasks.values(), return_when=asyncio.FIRST_EXCEPTION
        )

        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                failed = True
                logger.error(
                    "motor caído; se detiene el proceso completo",
                    extra={"worker": task.get_name(), "error": repr(error)},
                    exc_info=error,
                )

        if failed:
            request_shutdown()
            await _drain(tasks)
        return 1 if failed else 0
    finally:
        # Un solo `dispose_engine` porque hay un solo engine: los dos motores
        # comparten `AsyncSessionLocal`, que es justamente el ahorro que
        # justifica este módulo.
        await dispose_engine()
        logger.info("workers detenidos")


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
