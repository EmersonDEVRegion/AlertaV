"""Mosaicos del mapa estándar de OpenStreetMap, para comparar con las imágenes de Viper.

Viper, el sistema de despacho de las centrales, dibuja cada despacho sobre los
mosaicos estándar de OSM en zoom 18. Para saber dónde está su marcador hay que
tener esos mismos mosaicos y buscar dónde calza la imagen (`collectors/viper.py`).

La política de uso de `tile.openstreetmap.org` permite un uso liviano con un
User-Agent que identifique la aplicación y prohíbe las descargas masivas. Por
eso:

* **Caché en memoria** de los PNG ya bajados (`VIPER_CACHE_MOSAICOS`), de modo
  que dos despachos en el mismo barrio no vuelven a pedir lo mismo.
* **Dos conexiones a la vez** como máximo, por imagen.
* **Tope diario** (`VIPER_MAX_MOSAICOS_DIA`): pasado el tope, la imagen no se
  usa y el despacho queda con el punto del índice de esquinas.
* **Contador** de pedidos por día, que va a los logs: es la medida de volumen
  que se acordó revisar.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TAMANO = 256

_CACHE: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()
_USO: dict[str, int] = {"dia": 0, "pedidos": 0, "cache": 0}


class TopeDeMosaicosError(RuntimeError):
    """Se llegó a `VIPER_MAX_MOSAICOS_DIA`."""


def _dia_de_hoy() -> int:
    return datetime.now(UTC).toordinal()


def uso_de_hoy() -> dict[str, int]:
    """Mosaicos pedidos al servidor y servidos desde la caché, hoy (UTC)."""
    if _USO["dia"] != _dia_de_hoy():
        _USO.update(dia=_dia_de_hoy(), pedidos=0, cache=0)
    return {"pedidos": _USO["pedidos"], "cache": _USO["cache"]}


def reiniciar() -> None:
    """Vacía la caché y el contador. Para los tests."""
    _CACHE.clear()
    _USO.update(dia=0, pedidos=0, cache=0)


async def png(
    client: httpx.AsyncClient,
    z: int,
    x: int,
    y: int,
    *,
    conexiones: asyncio.Semaphore | None = None,
) -> bytes:
    """El PNG de un mosaico, de la caché o del servidor."""
    clave = (z, x, y)
    uso_de_hoy()
    if clave in _CACHE:
        _CACHE.move_to_end(clave)
        _USO["cache"] += 1
        return _CACHE[clave]
    if _USO["pedidos"] >= settings.VIPER_MAX_MOSAICOS_DIA:
        raise TopeDeMosaicosError(f"{_USO['pedidos']} mosaicos hoy")
    async with conexiones or asyncio.Semaphore(1):
        respuesta = await client.get(settings.OSM_TILES_URL.format(z=z, x=x, y=y))
    _USO["pedidos"] += 1
    respuesta.raise_for_status()
    _CACHE[clave] = respuesta.content
    while len(_CACHE) > settings.VIPER_CACHE_MOSAICOS:
        _CACHE.popitem(last=False)
    return respuesta.content


async def mosaico(
    client: httpx.AsyncClient,
    z: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    reduccion: int = 1,
) -> tuple[Any, int, int]:
    """Gris en `float32` que cubre los píxeles globales `[x0, x1) × [y0, y1)` del zoom `z`.

    Devuelve `(imagen, x_origen, y_origen)` en píxeles globales de resolución
    completa. Con `reduccion=2` cada mosaico se promedia en bloques de 2×2 al
    pegarlo: la búsqueda gruesa trabaja a media resolución y nunca arma el
    mosaico completo en memoria.
    """
    import numpy as np
    from PIL import Image

    tx0, ty0 = int(x0 // TAMANO), int(y0 // TAMANO)
    tx1, ty1 = int((x1 - 1) // TAMANO), int((y1 - 1) // TAMANO)
    lado = TAMANO // reduccion
    salida = np.zeros(((ty1 - ty0 + 1) * lado, (tx1 - tx0 + 1) * lado), np.float32)
    claves = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    # Dos conexiones a la vez, como pide la política de uso de los mosaicos.
    conexiones = asyncio.Semaphore(2)
    datos = await asyncio.gather(
        *(png(client, z, tx, ty, conexiones=conexiones) for tx, ty in claves)
    )
    for (tx, ty), contenido in zip(claves, datos, strict=True):
        gris = np.asarray(Image.open(io.BytesIO(contenido)).convert("L"), np.float32)
        if reduccion > 1:
            gris = gris.reshape(lado, reduccion, lado, reduccion).mean(axis=(1, 3))
        fila, columna = (ty - ty0) * lado, (tx - tx0) * lado
        salida[fila : fila + lado, columna : columna + lado] = gris
    return salida, tx0 * TAMANO, ty0 * TAMANO


__all__ = [
    "TAMANO",
    "TopeDeMosaicosError",
    "mosaico",
    "png",
    "reiniciar",
    "uso_de_hoy",
]
