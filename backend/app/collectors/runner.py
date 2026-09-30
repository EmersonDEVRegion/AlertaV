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

En producción sobre la capa gratuita, este bucle y el de correlación comparten
un único proceso —ver `app/workers.py`— porque 512 MB no alcanzan para tres
intérpretes de Python. Este módulo sigue siendo ejecutable por su cuenta, que es
como se usa en desarrollo y como se depura un collector en particular.

En modo `--loop` cada collector corre en su propia tarea, con su propio
intervalo. Un incendio de CONAF cambia de estado en minutos; una pasada de
satélite de FIRMS ocurre unas pocas veces al día. Forzar una cadencia común
significaría o malgastar cuota o llegar tarde.

Una tarea por collector, no un cronjob por scraper
--------------------------------------------------
La pregunta reaparece cada vez que entra una fuente raspada —la última fue
Transporte Informa—: ¿se acopla al ciclo de otro scraper, o se aísla en un
proceso propio con su propia frecuencia? La respuesta de este módulo es una
tercera, y conviene dejarla escrita para no rediscutirla por fuente:

**Aislamiento lógico sin aislamiento de proceso.** Cada collector ya tiene su
tarea, su cadencia, su traza en `collector_runs` y su propio `try` — si el
portal del MTT devuelve HTML roto, el bucle de la prensa local no se entera.
Eso es todo lo que un cronjob separado ofrecería en materia de aislamiento.

Lo que un cronjob separado ofrecería *además* es un intérprete más, y ese es el
argumento que lo descarta: `app/workers.py` documenta la medición —un proceso
Python con httpx y SQLAlchemy cuesta ~53 MB de los 512 MB del plan gratuito,
más un segundo pool contra Supabase—. Gastar un tercio del margen de memoria
para separar UN GET cada diez minutos es un mal negocio, y encima el plan no
tiene primitiva de cron: habría que inventarla en `start.sh`.

Acoplar dos scrapers a un mismo ciclo es peor todavía y por el motivo opuesto:
los sincronizaría a propósito, que es exactamente lo que `_STARTUP_JITTER` y
`_next_delay` existen para deshacer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
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
from app.core.shutdown import (
    install_signal_handlers,
    is_shutting_down,
    sleep_unless_stopped,
)
from app.models.enums import CollectorStatus
from app.services.ingest_service import IngestService

logger = logging.getLogger("alertav.runner")

#: Dispersión aleatoria del primer disparo de cada collector, como fracción del
#: intervalo. Evita que todos golpeen los servicios institucionales en el mismo
#: segundo tras un reinicio.
_STARTUP_JITTER = 0.25


def _next_delay(interval: int) -> float:
    """Espera hasta el próximo ciclo, con dispersión.

    Existe porque el escalonado del arranque resolvía sólo la mitad del
    problema. `_STARTUP_JITTER` reparte el primer disparo y evita el pico del
    reinicio; a partir de ahí cada collector entraba en un ciclo exacto y se
    quedaba ahí durante semanas, con dos efectos que ninguna cantidad de
    escalonado inicial corrige:

    * **Dos cadencias con divisor común vuelven a juntarse.** 600 s y 900 s
      comparten período 1800, así que Transporte Informa y prensa local salían a
      la red en el mismo segundo cada media hora, pasara lo que pasara en el
      arranque. El desfase inicial sólo elige *cuál* segundo.
    * **Un intervalo exacto es una firma.** Ningún navegador pide una página
      cada 600 s con precisión de reloj. Es lo que un WAF reconoce como
      automatización antes de contar peticiones, y contra un portal
      institucional el bloqueo no llega como un 429 que se pueda reintentar:
      llega como una IP vetada que hay que ir a pedir que desbloqueen.

    La dispersión es simétrica —adelanta tanto como atrasa— para no arrastrar la
    cadencia efectiva hacia arriba con el paso de los ciclos. Y va acotada
    inferiormente a 5 s: con `COLLECTOR_JITTER_RATIO` alto y un intervalo corto,
    la resta podría producir esperas ridículas o negativas.
    """
    ratio = max(0.0, min(0.5, settings.COLLECTOR_JITTER_RATIO))
    if ratio == 0.0:
        return float(interval)
    spread = interval * ratio
    return max(5.0, interval + random.uniform(-spread, spread))


#: Semáforo de `COLLECTOR_MAX_CONCURRENCY`. Se crea perezosamente, en el loop
#: que lo usa por primera vez.
_CUPOS: asyncio.Semaphore | None = None


def _cupos() -> asyncio.Semaphore:
    global _CUPOS
    if _CUPOS is None:
        _CUPOS = asyncio.Semaphore(max(1, settings.COLLECTOR_MAX_CONCURRENCY))
    return _CUPOS


async def run_collector(name: str) -> CollectorResult:
    """Ejecuta un collector en su propia sesión y garantiza que quede trazado.

    Si la construcción del collector falla —falta una MAP_KEY, una URL mal
    declarada— igual se escribe una fila en `collector_runs`. Un collector que
    nunca llega a arrancar es indistinguible, desde los datos, de uno que corrió
    y no encontró nada: esa ambigüedad es justo lo que la tabla existe para
    eliminar.

    Corre dentro de un cupo (`COLLECTOR_MAX_CONCURRENCY`). Sin él, un reinicio
    lanzaba los trece collectors casi a la vez contra un pool de cinco
    conexiones, y el sexto reventaba por `pool_timeout` antes de poder escribir
    su propia fila: el hueco sólo se veía en el log.
    """
    async with _cupos(), AsyncSessionLocal() as session:
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
    if not await sleep_unless_stopped(delay):
        return  # apagado durante el arranque escalonado

    while not is_shutting_down():
        try:
            await run_collector(name)
        except Exception:
            # `run_collector` ya traza los fallos de la fuente; llegar acá
            # significa un fallo de infraestructura (base de datos caída, por
            # ejemplo). Se registra y se reintenta en el siguiente ciclo.
            logger.exception("ciclo del collector falló", extra={"collector": name})

        if not await sleep_unless_stopped(_next_delay(interval)):
            break
    logger.info("collector detenido", extra={"collector": name})


async def _inbox_loop(intervalo: float) -> None:
    """Vacía el inbox del webhook de Apify. Ver «El inbox» en `apify_webhook_service`.

    Procesa de a una entrega y, mientras haya, sin esperar entre ellas; cuando
    no queda ninguna, duerme `intervalo`. Cada entrega ocupa un cupo de
    `COLLECTOR_MAX_CONCURRENCY` como cualquier collector: también abre
    conexiones y llama al modelo.
    """
    # Import local: el servicio importa medio árbol de collectors y este módulo
    # se importa desde la CLI sólo para `--show-schedule`.
    from app.services.apify_webhook_service import procesar_siguiente

    logger.info("inbox del webhook programado", extra={"interval_s": intervalo})
    while not is_shutting_down():
        try:
            while not is_shutting_down():
                async with _cupos():
                    hubo = await procesar_siguiente()
                if not hubo:
                    break
        except Exception:
            # Base caída al reclamar. Se reintenta en el siguiente ciclo; la
            # entrega sigue en la tabla, que es justamente el punto del inbox.
            logger.exception("ciclo del inbox del webhook falló")
        if not await sleep_unless_stopped(intervalo):
            break
    logger.info("inbox del webhook detenido")


async def _rain_grid_loop(intervalo: int) -> None:
    """Refresca la grilla de lluvia del mapa de calor. Ver `rain_grid_service`.

    No es un collector (no escribe señales), pero sale a la red como uno, así
    que ocupa un cupo de `COLLECTOR_MAX_CONCURRENCY` mientras corre. Un fallo de
    Open-Meteo queda anotado en la fila y no sube: la foto anterior sigue sirviendo.
    """
    from app.services.rain_grid_service import refresh_rain_grid

    delay = random.uniform(5, 60)
    logger.info(
        "grilla de lluvia programada",
        extra={"interval_s": intervalo, "first_run_in_s": round(delay)},
    )
    if not await sleep_unless_stopped(delay):
        return
    while not is_shutting_down():
        ok = False
        try:
            async with _cupos(), AsyncSessionLocal() as session:
                ok = await refresh_rain_grid(session)
        except Exception:
            # Base caída, por ejemplo.
            logger.exception("ciclo de la grilla de lluvia falló")
        # Una corrida fallida (un 429 de Open-Meteo, la base caída) se reintenta
        # pronto: con la cadencia de 3 h, esperar la próxima dejaba la foto
        # vieja en el mapa media tarde.
        espera = intervalo if ok else min(intervalo, settings.RAIN_GRID_RETRY_SECONDS)
        if not await sleep_unless_stopped(_next_delay(espera)):
            break
    logger.info("grilla de lluvia detenida")


async def run_loop(
    names: Sequence[str] | None,
    interval: int | None = None,
    *,
    inbox: bool | None = None,
) -> None:
    """Bucle de todos los collectors pedidos, más el inbox del webhook.

    El inbox corre por defecto sólo cuando se piden todos los collectors
    (`names` vacío): quien depura uno en particular con `--collector` no quiere
    que además se procesen entregas de Apify.
    """
    plan = schedule(names, interval)
    con_inbox = (not names) if inbox is None else inbox
    logger.info("runner iniciado", extra={"schedule": plan, "inbox": con_inbox})
    tareas = [_collector_loop(name, seconds) for name, seconds in plan.items()]
    if con_inbox:
        tareas.append(_inbox_loop(settings.APIFY_INBOX_POLL_SECONDS))
    # Con la misma regla que el inbox: sólo al correr todos los collectors.
    if con_inbox and settings.RAIN_GRID_ENABLED:
        tareas.append(_rain_grid_loop(settings.RAIN_GRID_POLL_INTERVAL_SECONDS))
    await asyncio.gather(*tareas)
    logger.info("runner detenido")


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
    install_signal_handlers()

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
