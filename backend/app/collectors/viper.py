"""El punto exacto de un despacho, leído de la imagen que publica la central.

Qué es la imagen
----------------
Cuatro de las cinco centrales (@CGI_CBV, @CBVM132, @CBQuilpue, @cbquillota)
adjuntan a cada despacho una imagen de Viper, su sistema de despacho: clave,
hora, dirección, comuna, unidades y un mapa de OpenStreetMap en zoom 18 con el
marcador del siniestro **en el centro**. Ese marcador es el punto que usó la
central para despachar, más fino que cualquier geocodificación del texto.

Cómo se lee el punto
--------------------
La imagen no trae coordenadas, pero su mapa está hecho con los mismos mosaicos
estándar de OSM que cualquiera puede pedir (`osm_tiles`). Se busca dónde calza
la imagen sobre esos mosaicos alrededor de una semilla, y el centro de la
imagen calzada es el marcador:

1. **Grueso**, a media resolución y por FFT, sobre un radio de
   `VIPER_RADIO_M` (o `VIPER_RADIO_AMPLIO_M`) alrededor de la semilla. Se
   comparan bordes y no colores, ocultando el marcador y la atribución.
2. **Fino**, a resolución completa, ±8 px alrededor del grueso. Un píxel de
   zoom 18 son ~0,5 m en la región.

Con las cinco imágenes reales de la prueba del 16-10-2026, la correlación fue
≥ 0,99 en todas: la imagen calza casi píxel a píxel. Se acepta sólo si supera
`VIPER_NCC_MIN` y si el mejor calce le saca `VIPER_MARGEN_MIN` al segundo; si
no, el despacho se queda con el punto que ya tenía.

La semilla
----------
Es el punto del despacho (normalmente la esquina del índice local). Si la
imagen no calza ahí, puede que la comuna esté mal: la central de Quillota
despacha también en San Felipe, y el tuit no lo dice. Entonces Gemini lee la
comuna del encabezado de la imagen y, si es otra, el despacho se geocodifica de
nuevo en ella antes de volver a buscar.

Cuando la imagen y el texto no coinciden, **manda la imagen**: es el punto con
que la central despachó.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from app.collectors import osm_tiles
from app.collectors.geoservices import normalise_text
from app.collectors.lugares import comuna_por_nombre
from app.collectors.nominatim import (
    PRECISION_ADDRESS,
    PRECISION_DISPATCH,
    PRECISION_INTERSECTION,
    GeocodeResult,
    build_client,
    geocode,
)
from app.collectors.overpass import distancia_m
from app.collectors.traffic import gemini
from app.core.config import settings

if TYPE_CHECKING:
    from app.collectors.traffic.bomberos_10_4_worker import Dispatch

logger = logging.getLogger(__name__)

ZOOM = 18
#: Ancho de la imagen original de Viper. El mapa de otro tamaño (las versiones
#: chicas de X) está a otra escala y no calzaría con zoom 18.
ANCHO_VIPER = 1080
REDUCCION = 2
#: Radio del marcador y alto de la atribución, en píxeles de la imagen original.
RADIO_MARCADOR = 75
ALTO_ATRIBUCION = 45
#: Holgura del ajuste fino, en píxeles de resolución completa.
HOLGURA_FINA = 8
#: Alrededor del mejor calce grueso, lo que no cuenta como «segundo mejor».
VECINDAD_PICO = 12

_HOSTS_IMAGEN = frozenset({"pbs.twimg.com"})


@dataclass(frozen=True, slots=True)
class Calce:
    """Dónde calzó la imagen: el punto del marcador y qué tan seguro es."""

    lat: float
    lon: float
    ncc: float
    margen: float
    radio_m: float
    distancia_semilla_m: float


# --- Geometría de Web Mercator ---------------------------------------------------


def a_pixeles(lat: float, lon: float, z: int = ZOOM) -> tuple[float, float]:
    n = osm_tiles.TAMANO * 2**z
    seno = math.sin(math.radians(lat))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + seno) / (1 - seno)) / (4 * math.pi)) * n


def a_coordenadas(x: float, y: float, z: int = ZOOM) -> tuple[float, float]:
    n = osm_tiles.TAMANO * 2**z
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, x / n * 360 - 180


def metros_por_pixel(lat: float, z: int = ZOOM) -> float:
    return 156_543.033_92 * math.cos(math.radians(lat)) / 2**z


# --- La imagen -------------------------------------------------------------------


def recortar_mapa(gris: Any) -> tuple[Any, int] | None:
    """El mapa de la imagen (entre el encabezado y el pie) y la fila donde empieza.

    El encabezado y el pie son blancos con texto; el mapa no tiene filas
    blancas. Se toma el tramo de filas con más de 60 % de píxeles no blancos.
    """
    import numpy as np

    filas = np.flatnonzero((gris < 250).mean(axis=1) > 0.6)
    if filas.size < 400:
        return None
    return gris[filas[0] : filas[-1] + 1], int(filas[0])


def mascara(alto: int, ancho: int) -> Any:
    """Lo que se compara: todo menos el marcador del centro y la atribución."""
    import numpy as np

    yy, xx = np.mgrid[0:alto, 0:ancho]
    fuera_marcador = (yy - alto / 2) ** 2 + (xx - ancho / 2) ** 2 > RADIO_MARCADOR**2
    fuera_atribucion = ~((yy > alto - ALTO_ATRIBUCION) & (xx > ancho * 0.58))
    return fuera_marcador & fuera_atribucion


def bordes(gris: Any) -> Any:
    import numpy as np

    gy, gx = np.gradient(gris)
    return np.hypot(gx, gy).astype(np.float32)


def reducir(arreglo: Any, factor: int) -> Any:
    alto, ancho = (arreglo.shape[0] // factor) * factor, (arreglo.shape[1] // factor) * factor
    recorte = arreglo[:alto, :ancho].astype("float32")
    return recorte.reshape(alto // factor, factor, ancho // factor, factor).mean(axis=(1, 3))


def ncc_fft(grande: Any, plantilla: Any, mascara_: Any) -> tuple[tuple[int, int], float, float]:
    """Correlación cruzada normalizada con máscara, por FFT en `float32`.

    Devuelve `((fila, columna), pico, segundo)`: la esquina superior izquierda
    del mejor calce, su correlación y la del mejor calce fuera de su vecindad.
    """
    import numpy as np

    alto_g, ancho_g = grande.shape
    alto, ancho = plantilla.shape
    m = mascara_.astype(np.float32)
    t = np.where(mascara_, plantilla - plantilla[mascara_].mean(), 0).astype(np.float32)
    t /= np.sqrt((t**2).sum()) + 1e-9
    forma = (alto_g, ancho_g)

    def correlacion(a: Any, b: Any) -> Any:
        espectro = np.fft.rfft2(a, s=forma) * np.conj(np.fft.rfft2(b, s=forma))
        return np.fft.irfft2(espectro, s=forma).astype(np.float32)

    numerador = correlacion(grande, t)
    suma = correlacion(grande, m)
    suma_cuadrados = correlacion(grande * grande, m)
    del grande
    varianza = np.maximum(suma_cuadrados - suma**2 / m.sum(), 1e-3)
    r = (numerador / np.sqrt(varianza))[: alto_g - alto + 1, : ancho_g - ancho + 1]
    fila, columna = np.unravel_index(int(np.argmax(r)), r.shape)
    pico = float(r[fila, columna])
    r[
        max(0, fila - VECINDAD_PICO) : fila + VECINDAD_PICO + 1,
        max(0, columna - VECINDAD_PICO) : columna + VECINDAD_PICO + 1,
    ] = -1
    return (int(fila), int(columna)), pico, float(r.max())


def ncc_directa(
    grande: Any, plantilla: Any, mascara_: Any, fila: int, columna: int, holgura: int
) -> tuple[tuple[int, int], float]:
    """El mejor calce a ±`holgura` píxeles de `(fila, columna)`, sin FFT."""
    import numpy as np

    alto, ancho = plantilla.shape
    t = np.where(mascara_, plantilla - plantilla[mascara_].mean(), 0).astype(np.float32)
    t /= np.sqrt((t**2).sum()) + 1e-9
    mejor = ((fila, columna), -1.0)
    for df in range(-holgura, holgura + 1):
        for dc in range(-holgura, holgura + 1):
            f, c = fila + df, columna + dc
            if f < 0 or c < 0 or f + alto > grande.shape[0] or c + ancho > grande.shape[1]:
                continue
            ventana = grande[f : f + alto, c : c + ancho]
            centrada = np.where(mascara_, ventana - ventana[mascara_].mean(), 0)
            norma = float(np.sqrt((centrada**2).sum()))
            if norma <= 0:
                continue
            valor = float((centrada * t).sum() / norma)
            if valor > mejor[1]:
                mejor = ((f, c), valor)
    return mejor


def leer(imagen: bytes) -> tuple[Any, Any, int] | None:
    """`(gris, mapa, fila_del_mapa)` de una imagen de Viper, o None si no lo es."""
    import numpy as np
    from PIL import Image

    try:
        gris = np.asarray(Image.open(io.BytesIO(imagen)).convert("L"), np.float32)
    except Exception:
        return None
    if gris.shape[1] != ANCHO_VIPER:
        return None
    recorte = recortar_mapa(gris)
    if recorte is None:
        return None
    return gris, recorte[0], recorte[1]


async def calzar(
    client: httpx.AsyncClient, mapa: Any, semilla: tuple[float, float], radio_m: float
) -> Calce | None:
    """Dónde está el marcador, buscando a `radio_m` de la semilla. None si no calza."""
    alto, ancho = mapa.shape
    centro_f, centro_c = alto / 2, ancho / 2
    mascara_ = mascara(alto, ancho)

    # 1) Grueso, a media resolución.
    gx, gy = a_pixeles(*semilla)
    radio_px = radio_m / metros_por_pixel(semilla[0])
    grande, ox, oy = await osm_tiles.mosaico(
        client,
        ZOOM,
        gx - radio_px - centro_c,
        gy - radio_px - centro_f,
        gx + radio_px + centro_c,
        gy + radio_px + centro_f,
        reduccion=REDUCCION,
    )
    mapa_reducido = reducir(mapa, REDUCCION)
    mascara_reducida = reducir(mascara_.astype("float32"), REDUCCION) > 0.99
    (fila, columna), pico, segundo = ncc_fft(
        bordes(grande), bordes(mapa_reducido), mascara_reducida
    )
    del grande
    margen = pico - segundo
    if margen < settings.VIPER_MARGEN_MIN:
        logger.info(
            "la imagen de Viper no calza sin ambigüedad",
            extra={"pico": round(pico, 3), "segundo": round(segundo, 3), "radio_m": radio_m},
        )
        return None
    x_sup, y_sup = ox + columna * REDUCCION, oy + fila * REDUCCION

    # 2) Fino, a resolución completa.
    fino, fx, fy = await osm_tiles.mosaico(
        client,
        ZOOM,
        x_sup - HOLGURA_FINA,
        y_sup - HOLGURA_FINA,
        x_sup + ancho + HOLGURA_FINA,
        y_sup + alto + HOLGURA_FINA,
    )
    (f_fino, c_fino), ncc = ncc_directa(
        bordes(fino), bordes(mapa), mascara_, y_sup - fy, x_sup - fx, HOLGURA_FINA
    )
    if ncc < settings.VIPER_NCC_MIN:
        logger.info(
            "la imagen de Viper calza mal",
            extra={"ncc": round(ncc, 3), "margen": round(margen, 3), "radio_m": radio_m},
        )
        return None
    lat, lon = a_coordenadas(fx + c_fino + centro_c, fy + f_fino + centro_f)
    return Calce(
        lat=round(lat, 7),
        lon=round(lon, 7),
        ncc=round(ncc, 3),
        margen=round(margen, 3),
        radio_m=radio_m,
        distancia_semilla_m=round(distancia_m(semilla, (lat, lon)), 1),
    )


def encabezado_png(gris: Any, fila_del_mapa: int) -> bytes:
    """La franja de texto sobre el mapa, en PNG, para que Gemini la lea."""
    from PIL import Image

    salida = io.BytesIO()
    Image.fromarray(gris[:fila_del_mapa].astype("uint8")).save(salida, format="PNG")
    return salida.getvalue()


def url_de_imagen(url: str | None) -> str | None:
    """La URL de la imagen original, sólo si es de X (`pbs.twimg.com`)."""
    if not url:
        return None
    partes = urlsplit(url.strip())
    if partes.scheme != "https" or partes.hostname not in _HOSTS_IMAGEN:
        return None
    return f"https://{partes.hostname}{partes.path}?name=orig"


# --- Los despachos ---------------------------------------------------------------


def resultado(
    calce: Calce, previo: GeocodeResult | None, url: str, comuna: str | None
) -> GeocodeResult:
    return GeocodeResult(
        lat=calce.lat,
        lon=calce.lon,
        query=url,
        precision=PRECISION_DISPATCH,
        comuna=comuna or (previo.comuna if previo else None),
        provider="viper",
        viper={
            "ncc": calce.ncc,
            "margen": calce.margen,
            "radio_m": calce.radio_m,
            "distancia_semilla_m": calce.distancia_semilla_m,
            "semilla_precision": previo.precision if previo else None,
            "semilla_provider": previo.provider if previo else None,
        },
    )


async def _ubicar_uno(
    client: httpx.AsyncClient, dispatch: Dispatch, url: str, *, leer_encabezado: bool
) -> tuple[Dispatch, bool]:
    """El despacho con el punto de su imagen, si calza. `(despacho, usó_gemini)`."""
    respuesta = await client.get(url)
    respuesta.raise_for_status()
    leida = leer(respuesta.content)
    if leida is None:
        logger.info("la imagen del despacho no es de Viper", extra={"url": url})
        return dispatch, False
    gris, mapa, fila_del_mapa = leida

    previo = dispatch.point
    if previo is not None:
        fino = previo.precision in (PRECISION_INTERSECTION, PRECISION_ADDRESS)
        radios = (
            (settings.VIPER_RADIO_M, settings.VIPER_RADIO_AMPLIO_M)
            if fino
            else (settings.VIPER_RADIO_AMPLIO_M,)
        )
        for radio in radios:
            calce = await calzar(client, mapa, (previo.lat, previo.lon), radio)
            if calce is not None:
                return replace(dispatch, point=resultado(calce, previo, url, None)), False

    if not leer_encabezado:
        return dispatch, False

    # No calzó (o no había punto): ¿es otra comuna? La imagen lo dice.
    encabezado = await gemini.leer_encabezado_viper(encabezado_png(gris, fila_del_mapa))
    comuna = comuna_por_nombre((encabezado or {}).get("comuna"))
    if comuna is None or (
        previo is not None and normalise_text(previo.comuna or "") == normalise_text(comuna)
    ):
        return dispatch, True
    calles = dict(dispatch.decoded or {})
    if not calles.get("street_1"):
        return dispatch, True
    nuevo = await geocode(client, {**calles, "city": comuna}, comuna=comuna)
    if nuevo is None:
        return dispatch, True
    calce = await calzar(client, mapa, (nuevo.lat, nuevo.lon), settings.VIPER_RADIO_AMPLIO_M)
    if calce is not None:
        return replace(dispatch, point=resultado(calce, nuevo, url, comuna)), True
    # Sin calce, al menos la comuna correcta: el punto nuevo reemplaza al de la
    # comuna equivocada.
    return replace(dispatch, point=nuevo), True


async def ubicar_por_imagen(
    dispatches: Sequence[Dispatch], *, max_imagenes: int
) -> tuple[list[Dispatch], int]:
    """Cambia el punto de cada despacho por el de su imagen de Viper, si calza.

    Devuelve `(despachos, ubicados_por_imagen)`. **Nunca lanza**: una imagen que
    no baja, un tope de mosaicos o un error dejan el despacho como estaba.
    """
    if not settings.VIPER_ENABLED or max_imagenes <= 0:
        return list(dispatches), 0
    salida: list[Dispatch] = []
    ubicados = 0
    usadas = 0
    lecturas = 0
    antes = osm_tiles.uso_de_hoy()
    async with build_client() as client:
        for dispatch in dispatches:
            url = url_de_imagen(dispatch.imagen_url)
            if url is None or usadas >= max_imagenes:
                salida.append(dispatch)
                continue
            usadas += 1
            try:
                nuevo, leyo = await _ubicar_uno(
                    client,
                    dispatch,
                    url,
                    leer_encabezado=lecturas < settings.VIPER_MAX_LECTURAS_POR_ENTREGA,
                )
            except osm_tiles.TopeDeMosaicosError as exc:
                logger.warning("tope diario de mosaicos de OSM", extra={"motivo": str(exc)})
                salida.append(dispatch)
                continue
            except Exception as exc:
                logger.warning(
                    "no se pudo ubicar el despacho por su imagen",
                    extra={"error": f"{type(exc).__name__}: {exc}", "url": url},
                )
                salida.append(dispatch)
                continue
            lecturas += int(leyo)
            if nuevo.point is not None and nuevo.point.provider == "viper":
                ubicados += 1
            salida.append(nuevo)
    despues = osm_tiles.uso_de_hoy()
    logger.info(
        "despachos ubicados por imagen",
        extra={
            "imagenes": usadas,
            "ubicados": ubicados,
            "lecturas_gemini": lecturas,
            "mosaicos_pedidos": despues["pedidos"] - antes["pedidos"],
            "mosaicos_cache": despues["cache"] - antes["cache"],
            "mosaicos_hoy": despues["pedidos"],
        },
    )
    return salida, ubicados


__all__ = [
    "ANCHO_VIPER",
    "ZOOM",
    "Calce",
    "a_coordenadas",
    "a_pixeles",
    "calzar",
    "leer",
    "recortar_mapa",
    "ubicar_por_imagen",
    "url_de_imagen",
]
