"""Geocodificación contra Nominatim (OpenStreetMap), con el rate limit respetado.

Por qué este módulo existe aparte
---------------------------------
Nominatim es un servicio donado. Su política de uso permite **1 petición por
segundo por IP**, y el incumplimiento no se castiga con un 429 que uno pueda
reintentar: se castiga bloqueando la IP, a veces de forma permanente y sin
aviso. Para este proyecto eso significaría perder la única vía de
georreferenciar los avisos del MTT — y perderla en silencio, porque el worker
seguiría corriendo y reportando cero geocodificaciones exitosas.

De ahí las dos decisiones que ordenan el archivo:

**El limitador es global al proceso, no por worker.** Nominatim cuenta por IP y
todo el backend sale por una sola. Un limitador por instancia de collector daría
1 req/s *cada uno*, que es exactamente la forma de superar el límite creyendo
que se lo respeta. `_LIMITER` es un singleton de módulo, y con collectors y
correlación compartiendo un intérprete (`app/workers.py`) eso cubre todo el
backend. Si algún día los workers vuelven a procesos separados, esta garantía se
rompe y habrá que mover el limitador a Redis o a la base.

**Serializa, no descarta.** El limitador hace esperar a quien llega temprano en
vez de rechazarlo. Un aviso de accidente sin geocodificar es un aviso perdido, y
el trabajo es de fondo: nadie está esperando la respuesta. Lo que sí se acota es
cuántas geocodificaciones intenta una corrida
(`TRANSPORTE_INFORMA_MAX_GEOCODES`), para que un día de temporal no deje al
worker cinco minutos colgado.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.collectors.geoservices import as_float, request_json
from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Espaciador de llamadas. Garantiza `min_interval` entre dos adquisiciones.

    El `asyncio.Lock` es lo que hace que la garantía se sostenga con varias
    corrutinas compitiendo: sin él, diez tareas leerían el mismo `_last_call`,
    todas concluirían que ya pueden salir y dispararían a la vez.

    Se mide con `monotonic()` y no con `time()` a propósito: un ajuste de reloj
    del sistema —NTP corrigiendo hacia atrás en pleno arranque del contenedor—
    haría que el reloj de pared retrocediera y el limitador dejara pasar una
    ráfaga justo cuando menos conviene.
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = asyncio.Lock()
        self._last_call: float | None = None

    async def acquire(self) -> float:
        """Espera lo necesario y reserva el turno. Devuelve los segundos dormidos."""
        async with self._lock:
            waited = 0.0
            now = time.monotonic()
            if self._last_call is not None:
                elapsed = now - self._last_call
                remaining = self.min_interval - elapsed
                if remaining > 0:
                    waited = remaining
                    await asyncio.sleep(remaining)
                    now = time.monotonic()
            self._last_call = now
            return waited


#: Singleton de proceso. Ver el docstring del módulo: Nominatim cuenta por IP.
_LIMITER = RateLimiter(settings.NOMINATIM_MIN_INTERVAL_SECONDS)


def get_limiter() -> RateLimiter:
    return _LIMITER


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """Un punto resuelto, con lo necesario para dudar de él.

    `display_name`, `osm_type` e `importance` no son decoración: son lo que
    permite a un operador mirar una señal del MTT y decidir si el punto
    corresponde a la intersección informada o si Nominatim resolvió a la comuna
    entera. Todo esto termina en `raw_data._geocoding`.
    """

    lat: float
    lon: float
    display_name: str | None = None
    osm_type: str | None = None
    #: Heurística de relevancia de Nominatim, en [0,1]. Un valor bajo suele
    #: indicar que resolvió algo más genérico que lo pedido.
    importance: float | None = None
    query: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "display_name": self.display_name,
            "osm_type": self.osm_type,
            "importance": self.importance,
            "query": self.query,
            "provider": "nominatim",
        }


def build_query(streets: dict[str, Any]) -> str | None:
    """Arma la cadena de búsqueda desde el diccionario del Paso A.

    Formato `calle y calle, ciudad, región`: es el que mejor resuelve Nominatim
    para intersecciones en Chile. Si no hay ninguna calle, devuelve None — buscar
    sólo por ciudad devolvería el centroide comunal, que como ubicación de un
    accidente es peor que no tener ubicación: parece un dato y no lo es.
    """
    primary = (streets.get("street") or "").strip()
    secondary = (streets.get("cross_street") or "").strip()
    city = (streets.get("city") or "").strip()
    region = (streets.get("region") or "").strip()

    if not primary:
        return None

    head = f"{primary} y {secondary}" if secondary else primary
    parts = [head, *[part for part in (city, region) if part]]
    return ", ".join(parts)


async def geocode(
    client: httpx.AsyncClient,
    streets: dict[str, Any],
    *,
    limiter: RateLimiter | None = None,
) -> GeocodeResult | None:
    """Resuelve una intersección a lat/lon. None si no hay match o falta calle.

    Devolver None es un resultado legítimo y frecuente: el MTT nombra tramos de
    ruta ("Ruta 68, km 42") que Nominatim no sabe resolver. El worker registra la
    señal igual, sin coordenadas — no entra al Paso A, pero queda consultable y
    es la métrica que dirá si conviene una capa de rutas propia.
    """
    query = build_query(streets)
    if not query:
        return None

    waited = await (limiter or _LIMITER).acquire()
    if waited > 0:
        logger.debug(
            "espera por el rate limit de Nominatim",
            extra={"waited_s": round(waited, 3), "query": query},
        )

    payload = await request_json(
        client,
        settings.NOMINATIM_URL,
        {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 0,
            "countrycodes": settings.NOMINATIM_COUNTRY_CODES,
        },
        origin="nominatim",
        # Un reintento y no dos: cada uno cuesta otro segundo de rate limit, y
        # una dirección que no resuelve hoy tampoco resolverá en 1,5 segundos.
        retries=1,
    )

    if not isinstance(payload, list) or not payload:
        return None

    first = payload[0]
    if not isinstance(first, dict):
        return None

    lat = as_float(first.get("lat"))
    lon = as_float(first.get("lon"))
    if lat is None or lon is None:
        return None

    return GeocodeResult(
        lat=lat,
        lon=lon,
        display_name=first.get("display_name"),
        osm_type=first.get("osm_type"),
        importance=as_float(first.get("importance")),
        query=query,
    )


def build_client(timeout: float | None = None) -> httpx.AsyncClient:
    """Cliente con el User-Agent que Nominatim exige.

    Las peticiones sin User-Agent identificable se rechazan: es parte del
    contrato de uso, no una recomendación.
    """
    return httpx.AsyncClient(
        timeout=timeout or settings.NOMINATIM_TIMEOUT_SECONDS,
        headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        follow_redirects=True,
    )


__all__ = [
    "GeocodeResult",
    "RateLimiter",
    "build_client",
    "build_query",
    "geocode",
    "get_limiter",
]
