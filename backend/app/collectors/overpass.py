"""Cruces de calles con Overpass: el punto que Nominatim no sabe dar.

El problema, con dos casos reales
---------------------------------
Nominatim busca nombres, no cruces (ver `nominatim.build_queries`). Ante un
despacho «LAS MONJAS / ANDRES BELLO» busca «Las Monjas» sola y devuelve el punto
donde OSM ancla esa calle: su comienzo en Av. Colón, a unos 200 m del incendio
que la central marcó en la esquina con Andrés Bello. Con «AVENIDA ALEMANIA /
GUILLERMO RIVERA» es peor: la avenida recorre los cerros de Valparaíso por
kilómetros y el pin cayó en otro cerro (05-10-2026, INC-2026-00872 y 00887).

Lo que hace este módulo
-----------------------
Overpass consulta los mismos datos de OpenStreetMap, pero por geometría. Se le
piden las vías cuyo nombre se parece a cualquiera de las dos calles, dentro de
la zona donde Nominatim ya ubicó una de ellas, y el cruce se calcula acá:

1. **Nodo compartido.** Dos vías de OSM que se cruzan comparten un nodo. Es el
   caso normal y el punto es exacto.
2. **Proximidad.** Si no comparten nodo (una calle mal dibujada, un paso bajo
   nivel, una calle que muere a metros de la otra), el par de puntos más
   cercano entre ambas, si están a menos de `OVERPASS_MAX_GAP_M`.

Si hay varios cruces con esos nombres (una avenida que corta dos veces la misma
calle), gana el más cercano al punto de Nominatim, que ya pasó las guardas de
comuna y sector.

Falla hacia lo de antes: sin cruce, sin red o con Overpass caído, `cruce`
devuelve None y queda el punto sobre la calle, rotulado `street`.

El servicio
-----------
Overpass es gratis y de uso justo: unas 10 000 consultas al día por IP y pocas
a la vez. AlertaV hace del orden de 50 al día (logs del 01 al 05-10). Hay dos
servidores públicos en `OVERPASS_URLS`; si el primero responde 429 o 5xx se
prueba el segundo.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.collectors.geoservices import normalise_text
from app.collectors.nominatim import RateLimiter
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Una consulta por segundo como máximo, compartida por todo el proceso.
_LIMITER = RateLimiter(1.0)

#: Palabras que indican el tipo de vía y no su nombre. La central escribe
#: «AVENIDA ALEMANIA» y OSM «Avenida Alemania»; otra fuente escribe «Av.
#: Alemania» o sólo «Alemania». Se sacan de los dos lados antes de comparar.
_TIPOS_DE_VIA = frozenset(
    {"avenida", "av", "avda", "calle", "pasaje", "pje", "psje", "camino", "callejon"}
)

#: Abreviaturas frecuentes en despachos y prensa, a su forma de OSM.
_ABREVIATURAS = {
    "pdte": "presidente",
    "pte": "presidente",
    "gral": "general",
    "dr": "doctor",
    "tte": "teniente",
    "cap": "capitan",
    "cptn": "capitan",
    "sta": "santa",
    "sto": "santo",
    "sn": "san",
    "ing": "ingeniero",
    "prof": "profesor",
    "almte": "almirante",
    "cmdte": "comandante",
}

#: Palabras que no distinguen una calle de otra.
_VACIAS = frozenset({"de", "del", "la", "las", "los", "el", "y"})

_NO_ALFANUM = re.compile(r"[^a-z0-9 ]+")

#: Letra → su variante con tilde, para el filtro del servidor (que no sabe
#: comparar sin tildes). No se usan clases como `[eé]`: si Overpass compila la
#: expresión byte a byte, la «é» (dos bytes en UTF-8) no cabe en una clase y
#: «Andrés» dejaría de calzar. Se arma una alternancia de palabras enteras.
_TILDES = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "n": "ñ"}

PRECISION_INTERSECTION = "intersection"


def nombre_nucleo(nombre: str | None) -> tuple[str, ...]:
    """Las palabras que nombran la calle, sin tildes, tipo de vía ni abreviaturas.

    «AVENIDA ALEMANIA» → («alemania»,); «Av. Pdte. Errázuriz» → («presidente»,
    «errazuriz»). Las palabras vacías se conservan salvo al comienzo («Las
    Monjas» → («las», «monjas»)) porque a veces son parte del nombre.
    """
    texto = _NO_ALFANUM.sub(" ", normalise_text(nombre).replace(".", " "))
    palabras = [_ABREVIATURAS.get(p, p) for p in texto.split()]
    while palabras and palabras[0] in _TIPOS_DE_VIA:
        palabras.pop(0)
    return tuple(palabras)


def _significativas(nucleo: Sequence[str]) -> tuple[str, ...]:
    return tuple(p for p in nucleo if p not in _VACIAS)


def nombres_calzan(buscado: str | None, osm: str | None) -> bool:
    """¿El nombre de OSM es la calle que nombró la fuente?

    Calza si, sin tildes ni tipo de vía, las palabras significativas son las
    mismas, o si las de la fuente son el final de las de OSM: la central
    escribe «ALVAREZ» y OSM «Doctor Álvarez». Al revés no: si la fuente nombra
    más que OSM («ANDRES BELLO» contra una «Bello»), es otra calle.
    """
    a = _significativas(nombre_nucleo(buscado))
    b = _significativas(nombre_nucleo(osm))
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) < len(b) and b[-len(a) :] == a


def variantes_con_tilde(palabra: str) -> list[str]:
    """La palabra sin tildes y con una tilde (o eñe) en cada posición posible.

    En castellano una palabra lleva a lo más una tilde, así que «andres» da
    «andres», «ándres», «andrés», «añdres»…: pocas variantes, y una de ellas es
    la de OSM. La inicial va además en mayúscula con tilde («Ángel»), porque la
    búsqueda insensible a mayúsculas puede no saber plegar letras acentuadas.
    """
    variantes = [palabra]
    for i, letra in enumerate(palabra):
        con_tilde = _TILDES.get(letra)
        if con_tilde is None:
            continue
        variantes.append(palabra[:i] + con_tilde + palabra[i + 1 :])
        if i == 0:
            variantes.append(con_tilde.upper() + palabra[1:])
    return list(dict.fromkeys(variantes))


def patron_servidor(nombre: str) -> str | None:
    """Filtro amplio para Overpass: la palabra más larga del nombre, con sus tildes posibles.

    Sólo sirve para que la respuesta sea chica; la comparación de verdad es
    `nombres_calzan`. None si el nombre no tiene ninguna palabra útil.
    """
    palabras = sorted(_significativas(nombre_nucleo(nombre)), key=len, reverse=True)
    if not palabras or len(palabras[0]) < 3:
        return None
    return "(" + "|".join(re.escape(v) for v in variantes_con_tilde(palabras[0])) + ")"


# --- Geometría ------------------------------------------------------------------

_RADIO_TIERRA_M = 6_371_008.8


def distancia_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine entre dos (lat, lon)."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * _RADIO_TIERRA_M * math.asin(min(1.0, math.sqrt(h)))


def _a_metros(p: tuple[float, float], origen: tuple[float, float]) -> tuple[float, float]:
    """Proyección equirectangular local: sobra para tramos de decenas de metros."""
    escala = math.cos(math.radians(origen[0]))
    return (
        (p[1] - origen[1]) * escala * 111_320.0,
        (p[0] - origen[0]) * 110_574.0,
    )


def _de_metros(x: float, y: float, origen: tuple[float, float]) -> tuple[float, float]:
    escala = math.cos(math.radians(origen[0]))
    return (origen[0] + y / 110_574.0, origen[1] + x / (escala * 111_320.0))


def _punto_mas_cercano_en_segmento(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    largo = dx * dx + dy * dy
    if largo == 0:
        return a
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / largo))
    return (ax + t * dx, ay + t * dy)


def _segmentos_mas_cercanos(
    s1: tuple[tuple[float, float], tuple[float, float]],
    s2: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, tuple[float, float]]:
    """(distancia, punto medio) entre dos segmentos en metros locales."""
    candidatos = []
    for p, (a, b) in ((s1[0], s2), (s1[1], s2), (s2[0], s1), (s2[1], s1)):
        q = _punto_mas_cercano_en_segmento(p, a, b)
        candidatos.append((math.dist(p, q), ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)))
    # Segmentos que se cruzan sin nodo común: distancia cero en el cruce.
    cruce = _interseccion(s1, s2)
    if cruce is not None:
        candidatos.append((0.0, cruce))
    return min(candidatos, key=lambda c: c[0])


def _interseccion(
    s1: tuple[tuple[float, float], tuple[float, float]],
    s2: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float] | None:
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


@dataclass(frozen=True, slots=True)
class Via:
    """Una `way` de OSM: nombre, nodos y su geometría, alineados."""

    id: int
    nombre: str
    nodos: tuple[int, ...]
    puntos: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Cruce:
    lat: float
    lon: float
    #: `nodo` (comparten un nodo de OSM) o `proximidad` (a menos de N metros).
    metodo: str
    #: Separación entre las dos calles en el punto, en metros. 0 con `nodo`.
    separacion_m: float
    #: Nombres de OSM de las dos vías que se cruzaron.
    via_1: str
    via_2: str
    #: Cuántos cruces distintos había con esos nombres en la zona.
    candidatos: int


def vias_desde_respuesta(payload: Any) -> list[Via]:
    """Las `way` con nombre, nodos y geometría de una respuesta de Overpass."""
    vias: list[Via] = []
    elementos = payload.get("elements") if isinstance(payload, Mapping) else None
    for elemento in elementos or []:
        if not isinstance(elemento, Mapping) or elemento.get("type") != "way":
            continue
        nombre = str((elemento.get("tags") or {}).get("name") or "").strip()
        nodos = elemento.get("nodes") or []
        geometria = elemento.get("geometry") or []
        if not nombre or not nodos or len(nodos) != len(geometria):
            continue
        try:
            puntos = tuple((float(g["lat"]), float(g["lon"])) for g in geometria)
            ids = tuple(int(n) for n in nodos)
        except (KeyError, TypeError, ValueError):
            continue
        vias.append(Via(id=int(elemento.get("id") or 0), nombre=nombre, nodos=ids, puntos=puntos))
    return vias


def _agrupar(
    puntos: Iterable[tuple[tuple[float, float], str, str, float, str]], radio_m: float
) -> list[list[tuple[tuple[float, float], str, str, float, str]]]:
    """Junta los cruces que son la misma esquina (avenidas de doble calzada)."""
    grupos: list[list[tuple[tuple[float, float], str, str, float, str]]] = []
    for item in puntos:
        for grupo in grupos:
            if distancia_m(grupo[0][0], item[0]) <= radio_m:
                grupo.append(item)
                break
        else:
            grupos.append([item])
    return grupos


def calcular_cruce(
    vias: Sequence[Via],
    calle_1: str,
    calle_2: str,
    *,
    cerca_de: tuple[float, float] | None = None,
    max_separacion_m: float | None = None,
) -> Cruce | None:
    """El cruce de dos calles a partir de las vías de OSM. Sin red: testeable."""
    max_sep = settings.OVERPASS_MAX_GAP_M if max_separacion_m is None else max_separacion_m
    a = [v for v in vias if nombres_calzan(calle_1, v.nombre)]
    b = [v for v in vias if nombres_calzan(calle_2, v.nombre)]
    # La misma vía no puede ser las dos calles («Alemania / Alemania»).
    b = [v for v in b if v.id not in {w.id for w in a}]
    if not a or not b:
        return None

    coordenadas: dict[int, tuple[float, float]] = {}
    nombre_de_nodo_a: dict[int, str] = {}
    for via in a:
        for nodo, punto in zip(via.nodos, via.puntos, strict=True):
            coordenadas[nodo] = punto
            nombre_de_nodo_a[nodo] = via.nombre

    hallados: list[tuple[tuple[float, float], str, str, float, str]] = []
    for via in b:
        for nodo in via.nodos:
            if nodo in nombre_de_nodo_a:
                hallados.append(
                    (coordenadas[nodo], nombre_de_nodo_a[nodo], via.nombre, 0.0, "nodo")
                )

    if not hallados and max_sep > 0:
        # Proximidad: el par de segmentos más cercano entre las dos calles.
        origen = a[0].puntos[0]
        mejor: tuple[float, tuple[float, float], str, str] | None = None
        for via_a in a:
            pa = [_a_metros(p, origen) for p in via_a.puntos]
            for via_b in b:
                pb = [_a_metros(p, origen) for p in via_b.puntos]
                for i in range(len(pa) - 1):
                    for j in range(len(pb) - 1):
                        dist, medio = _segmentos_mas_cercanos(
                            (pa[i], pa[i + 1]), (pb[j], pb[j + 1])
                        )
                        if mejor is None or dist < mejor[0]:
                            mejor = (dist, medio, via_a.nombre, via_b.nombre)
        if mejor is not None and mejor[0] <= max_sep:
            punto = _de_metros(mejor[1][0], mejor[1][1], origen)
            hallados.append((punto, mejor[2], mejor[3], round(mejor[0], 1), "proximidad"))

    if not hallados:
        return None

    grupos = _agrupar(hallados, radio_m=60.0)

    def centro(
        grupo: list[tuple[tuple[float, float], str, str, float, str]],
    ) -> tuple[float, float]:
        return (
            sum(p[0][0] for p in grupo) / len(grupo),
            sum(p[0][1] for p in grupo) / len(grupo),
        )

    if cerca_de is not None:
        grupos.sort(key=lambda g: distancia_m(centro(g), cerca_de))
    elegido = grupos[0]
    lat, lon = centro(elegido)
    _, via_1, via_2, separacion, metodo = elegido[0]
    return Cruce(
        lat=round(lat, 7),
        lon=round(lon, 7),
        metodo=metodo,
        separacion_m=separacion,
        via_1=via_1,
        via_2=via_2,
        candidatos=len(grupos),
    )


def armar_consulta(
    calle_1: str,
    calle_2: str,
    *,
    caja: tuple[float, float, float, float] | None,
    cerca_de: tuple[float, float] | None,
) -> str | None:
    """Overpass QL. `caja` en (oeste, sur, este, norte); si no, un radio alrededor."""
    patrones = [patron_servidor(calle_1), patron_servidor(calle_2)]
    if not all(patrones):
        return None
    if caja is not None:
        oeste, sur, este, norte = caja
        zona = f"({sur},{oeste},{norte},{este})"
    elif cerca_de is not None:
        zona = f"(around:{int(settings.OVERPASS_AROUND_M)},{cerca_de[0]},{cerca_de[1]})"
    else:
        return None
    filtros = "".join(f'way["highway"]["name"~"{patron}",i]{zona};' for patron in patrones)
    return (
        f"[out:json][timeout:{int(settings.OVERPASS_TIMEOUT_SECONDS)}];({filtros});out body geom;"
    )


async def consultar(client: httpx.AsyncClient, consulta: str) -> Any | None:
    """POST a Overpass, probando los servidores en orden. None si todos fallan."""
    for url in settings.OVERPASS_URLS:
        await _LIMITER.acquire()
        try:
            respuesta = await client.post(
                url, data={"data": consulta}, timeout=settings.OVERPASS_TIMEOUT_SECONDS + 5
            )
        except httpx.HTTPError as exc:
            logger.info("Overpass no respondió", extra={"url": url, "error": type(exc).__name__})
            continue
        if respuesta.status_code == 200:
            try:
                return respuesta.json()
            except ValueError:
                logger.info("Overpass devolvió algo que no es JSON", extra={"url": url})
                continue
        logger.info(
            "Overpass rechazó la consulta", extra={"url": url, "status": respuesta.status_code}
        )
    return None


async def cruce(
    client: httpx.AsyncClient,
    calle_1: str,
    calle_2: str,
    *,
    caja: tuple[float, float, float, float] | None = None,
    cerca_de: tuple[float, float] | None = None,
) -> Cruce | None:
    """El cruce de dos calles, o None. **Nunca lanza.**"""
    if not settings.OVERPASS_ENABLED or not settings.OVERPASS_URLS:
        return None
    consulta = armar_consulta(calle_1, calle_2, caja=caja, cerca_de=cerca_de)
    if consulta is None:
        return None
    try:
        payload = await consultar(client, consulta)
        if payload is None:
            return None
        return calcular_cruce(vias_desde_respuesta(payload), calle_1, calle_2, cerca_de=cerca_de)
    except Exception as exc:
        logger.warning(
            "el cálculo del cruce falló; queda el punto sobre la calle",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return None


__all__ = [
    "PRECISION_INTERSECTION",
    "Cruce",
    "Via",
    "armar_consulta",
    "calcular_cruce",
    "cruce",
    "nombre_nucleo",
    "nombres_calzan",
    "patron_servidor",
    "vias_desde_respuesta",
]
