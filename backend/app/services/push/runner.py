"""Worker del notificador push.

    # una pasada, con la traza impresa
    python -m app.services.push.runner

    # bucle continuo (en producción lo lanza `app.workers`)
    python -m app.services.push.runner --loop

Si falta la configuración VAPID, el bucle no falla: registra el motivo una vez y
se queda esperando el apagado. Tumbar el proceso por eso arrastraría a la
recolección y la correlación, que comparten intérprete en `app/workers.py`.
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
    wait_for_shutdown,
)
from app.services.push.config import get_push_config
from app.services.push.notifier import NotifierPass, PushNotifier
from app.services.push.webpush import WebPushSender

logger = logging.getLogger("alertav.push")


async def run_once(sender: WebPushSender) -> NotifierPass:
    async with AsyncSessionLocal() as session:
        return await PushNotifier(session, sender).run()


async def run_loop(interval: int) -> None:
    config = get_push_config()
    if not config.enabled or config.signer is None:
        logger.warning(
            "notificador push inactivo; se espera el apagado",
            extra={"motivo": config.reason},
        )
        await wait_for_shutdown()
        return

    sender = WebPushSender(config.signer)
    logger.info("notificador push iniciado", extra={"interval_s": interval})
    try:
        while not is_shutting_down():
            try:
                await run_once(sender)
            except Exception:
                # Igual que la correlación: una pasada fallida se registra y se
                # reintenta en el ciclo siguiente. Un notificador caído es un
                # silencio que nadie nota hasta el incendio en que hacía falta.
                logger.exception("pasada del notificador fallida; se reintenta")
            if not await sleep_unless_stopped(interval):
                break
    finally:
        await sender.aclose()
    logger.info("notificador push detenido")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlertaV — notificador push")
    parser.add_argument("--loop", action="store_true", help="Ejecución continua.")
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.PUSH_POLL_INTERVAL_SECONDS,
        help="Segundos entre pasadas en modo --loop.",
    )
    return parser.parse_args(argv)


async def _main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    install_signal_handlers()
    try:
        if args.loop:
            await run_loop(max(15, args.interval))
            return 0

        config = get_push_config()
        if not config.enabled or config.signer is None:
            print(json.dumps({"enabled": False, "reason": config.reason}, ensure_ascii=False))
            return 1
        sender = WebPushSender(config.signer)
        try:
            result = await run_once(sender)
        finally:
            await sender.aclose()
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return 0
    finally:
        await dispose_engine()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
