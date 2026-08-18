"""Señal de término compartida por los procesos de larga vida.

Hasta ahora cada runner —collectors y correlación— tenía su propio
`asyncio.Event` privado y su propia copia, idéntica, de
`_install_signal_handlers`. Mientras corrían en procesos separados la
duplicación era inofensiva. Al fusionarlos en un solo proceso
(`app/workers.py`) deja de serlo: un SIGTERM debe detener a los dos motores, y
dos eventos distintos significan dos apagados a medias —uno de los bucles
seguiría abriendo transacciones mientras el otro cierra el pool.

Por qué un apagado cooperativo y no dejar que la plataforma mate el proceso:
Koyeb envía SIGTERM y espera 30 segundos antes del SIGKILL. En esos 30 segundos
un runner puede terminar la pasada en curso y devolver sus conexiones al pooler.
Una conexión que muere por SIGKILL queda colgada del lado de Supabase hasta que
expira, y en la capa gratuita el presupuesto de conexiones es justo.
"""

from __future__ import annotations

import asyncio
import logging
import signal

logger = logging.getLogger("alertav.shutdown")

#: La verdad sobre el estado del proceso. Es un bool y no el Event a propósito:
#: `request_shutdown()` puede llegar desde un handler de señal en Windows, donde
#: no hay event loop corriendo, y consultarlo no debe exigir uno.
_stopped = False

#: El Event se crea perezosamente y atado al loop en curso. Un
#: `asyncio.Event()` construido al importar el módulo queda ligado al primer
#: loop que lo toque, y cualquier segundo `asyncio.run()` en el mismo proceso
#: —una batería de tests, típicamente— revienta con "bound to a different event
#: loop". El proceso real corre un solo loop, pero un módulo que sólo funciona
#: si nadie lo prueba dos veces es un módulo que no se prueba.
_event: asyncio.Event | None = None
_event_loop: asyncio.AbstractEventLoop | None = None


def _get_event() -> asyncio.Event:
    global _event, _event_loop

    loop = asyncio.get_running_loop()
    if _event is None or _event_loop is not loop:
        _event = asyncio.Event()
        _event_loop = loop
        if _stopped:
            _event.set()
    return _event


def request_shutdown() -> None:
    """Pide el término ordenado. Idempotente: llamarlo dos veces no hace daño."""
    global _stopped

    _stopped = True
    if _event is not None:
        _event.set()


def is_shutting_down() -> bool:
    return _stopped


async def wait_for_shutdown() -> None:
    await _get_event().wait()


async def sleep_unless_stopped(seconds: float) -> bool:
    """Duerme `seconds`, o corta antes si llega la señal de término.

    Devuelve True si transcurrió el tiempo completo —hay que seguir trabajando—
    y False si el apagado interrumpió la espera.

    Este es el motivo de que exista el módulo. Un `asyncio.sleep(seconds)` a
    secas obligaría a esperar el ciclo entero antes de reaccionar a un SIGTERM:
    hasta 30 minutos en el caso de FIRMS, contra los 30 segundos de gracia que
    da la plataforma. El proceso moriría siempre a la fuerza.
    """
    if _stopped:
        return False
    try:
        await asyncio.wait_for(_get_event().wait(), timeout=seconds)
    except TimeoutError:
        return True
    return False


def _on_signal(sig: signal.Signals) -> None:
    logger.info("señal recibida; iniciando apagado", extra={"signal": sig.name})
    request_shutdown()


def install_signal_handlers() -> None:
    """Conecta SIGINT y SIGTERM al apagado ordenado.

    Se llama una sola vez, desde el `main()` del proceso. Los handlers del event
    loop son la vía correcta en asyncio: `signal.signal` a secas ejecutaría el
    callback en medio de cualquier operación del loop.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, sig)
        except NotImplementedError:
            # Windows no implementa add_signal_handler. En desarrollo local
            # basta con el handler síncrono; en producción corre sobre Linux.
            signal.signal(sig, lambda *_: request_shutdown())


def reset() -> None:
    """Limpia el estado. Sólo para pruebas: un proceso real se apaga una vez."""
    global _stopped, _event, _event_loop

    _stopped = False
    _event = None
    _event_loop = None
