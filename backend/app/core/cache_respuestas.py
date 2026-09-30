"""Caché corta de las lecturas que la PWA sondea, y la cabecera `Server-Timing`.

Por qué
-------
La prueba de carga del 2026-09-30 (desde Chile, contra el plan Free de Render)
dio esto para `/incidents/active`:

    concurrencia   1 →  0,7 req/s, p50 1,5 s
    concurrencia  40 →  5,9 req/s, p50 8,8 s, p95 10,6 s

El techo de ~5 peticiones por segundo es el pool de la base (2 + 3 conexiones)
por el tiempo de cada consulta: cada petición toma una conexión casi un
segundo. Cada persona con el mapa abierto pide ~2,5 lecturas por minuto, así
que 100 personas ya rozan ese techo.

Los datos de esas rutas cambian, como mucho, cada 2 minutos (el motor) o cada 5
(los collectors). Reutilizar la misma respuesta 20 segundos no se nota en el
mapa y convierte N personas mirando en **una** consulta cada 20 s.

Cómo
----
Middleware ASGI puro, dentro de CORS (se registra antes que `CORSMiddleware`
para que cada respuesta reciba sus propias cabeceras de origen y no las del
primero que la pidió). Sólo `GET`, sólo las rutas de `RUTAS`, sólo respuestas
200 sin `ETag` ni `Set-Cookie`. La clave es la ruta más la query string.

Un solo vuelo por clave: si llegan diez peticiones iguales con la caché vacía,
la primera consulta y las otras nueve esperan su respuesta en vez de ir las
diez a la base. Es lo que evita la estampida justo cuando más gente mira.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

_P = settings.API_V1_PREFIX

#: Rutas exactas que se guardan. Todas son lecturas que la PWA sondea.
RUTAS: frozenset[str] = frozenset(
    {
        f"{_P}/incidents/active",
        f"{_P}/incidents/geojson",
        f"{_P}/collectors/health",
        f"{_P}/events/seismic",
        f"{_P}/events/weather/tactical",
        f"{_P}/events/weather/geojson",
        f"{_P}/events/road-closures/geojson",
        f"{_P}/events/water-cuts/geojson",
        f"{_P}/feed/vehiculos",
    }
)
#: Prefijos que también se guardan: la ficha de un incidente, que se sondea
#: mientras está abierta. `/incidents/stats` y `/incidents/correlate` quedan
#: fuera por nombre.
PREFIJOS: tuple[str, ...] = (f"{_P}/incidents/",)
EXCLUIDAS: frozenset[str] = frozenset({f"{_P}/incidents/stats", f"{_P}/incidents/correlate"})

#: Tope de entradas. Cada una es una respuesta JSON de pocos KB; 256 son de
#: sobra para las combinaciones de parámetros que usa la PWA.
MAX_ENTRADAS = 256

_CABECERAS_QUE_NO_SE_GUARDAN = {b"date", b"server", b"set-cookie", b"x-alertav-cache", b"age"}


@dataclass(slots=True)
class _Entrada:
    guardada_en: float
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes


def cacheable(path: str) -> bool:
    if path in RUTAS:
        return True
    if path in EXCLUIDAS:
        return False
    return any(path.startswith(prefijo) for prefijo in PREFIJOS)


class CacheDeLectura:
    """Middleware ASGI. Ver el docstring del módulo."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._entradas: OrderedDict[str, _Entrada] = OrderedDict()
        self._candados: dict[str, asyncio.Lock] = {}

    def limpiar(self) -> None:
        self._entradas.clear()
        self._candados.clear()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ttl = settings.API_RESPONSE_CACHE_SECONDS
        if (
            ttl <= 0
            or scope["type"] != "http"
            or scope.get("method") != "GET"
            or not cacheable(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        clave = scope["path"] + "?" + scope.get("query_string", b"").decode("latin-1")

        entrada = self._vigente(clave, ttl)
        if entrada is not None:
            await self._servir(entrada, send, acierto=True)
            return

        candado = self._candados.setdefault(clave, asyncio.Lock())
        async with candado:
            # Quien esperó el candado puede encontrar la respuesta ya lista.
            entrada = self._vigente(clave, ttl)
            if entrada is not None:
                await self._servir(entrada, send, acierto=True)
                return
            await self._pedir_y_guardar(clave, scope, receive, send)

    def _vigente(self, clave: str, ttl: int) -> _Entrada | None:
        entrada = self._entradas.get(clave)
        if entrada is None:
            return None
        if time.monotonic() - entrada.guardada_en > ttl:
            self._entradas.pop(clave, None)
            return None
        return entrada

    async def _pedir_y_guardar(
        self, clave: str, scope: Scope, receive: Receive, send: Send
    ) -> None:
        inicio: dict[str, Any] = {}
        partes: list[bytes] = []

        async def capturar(message: Message) -> None:
            if message["type"] == "http.response.start":
                inicio["status"] = message["status"]
                inicio["headers"] = list(message.get("headers", []))
                message = {
                    **message,
                    "headers": [*inicio["headers"], (b"x-alertav-cache", b"miss")],
                }
            elif message["type"] == "http.response.body":
                partes.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capturar)

        headers: list[tuple[bytes, bytes]] = inicio.get("headers", [])
        nombres = {nombre.lower() for nombre, _ in headers}
        if inicio.get("status") != 200 or b"etag" in nombres or b"set-cookie" in nombres:
            return
        self._entradas[clave] = _Entrada(
            guardada_en=time.monotonic(),
            status=200,
            headers=[(n, v) for n, v in headers if n.lower() not in _CABECERAS_QUE_NO_SE_GUARDAN],
            body=b"".join(partes),
        )
        self._entradas.move_to_end(clave)
        while len(self._entradas) > MAX_ENTRADAS:
            viejo, _ = self._entradas.popitem(last=False)
            self._candados.pop(viejo, None)

    @staticmethod
    async def _servir(entrada: _Entrada, send: Send, *, acierto: bool) -> None:
        edad = int(time.monotonic() - entrada.guardada_en)
        await send(
            {
                "type": "http.response.start",
                "status": entrada.status,
                "headers": [
                    *entrada.headers,
                    (b"x-alertav-cache", b"hit" if acierto else b"miss"),
                    (b"age", str(edad).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": entrada.body})


class ServerTiming:
    """`Server-Timing: app;dur=<ms>` en cada respuesta HTTP.

    Separa el tiempo del servidor del de la red en cualquier `curl -v` o en las
    herramientas del navegador. Sin esto, 1,5 s de respuesta desde Chile no
    dicen cuánto es Render y cuánto es el viaje.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()

        async def con_tiempo(message: Message) -> None:
            if message["type"] == "http.response.start":
                ms = (time.perf_counter() - inicio) * 1000
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"server-timing", f"app;dur={ms:.1f}".encode()),
                    ],
                }
            await send(message)

        await self.app(scope, receive, con_tiempo)


__all__ = ["RUTAS", "CacheDeLectura", "ServerTiming", "cacheable"]
