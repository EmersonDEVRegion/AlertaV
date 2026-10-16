"""La esquina de dos calles, desde el índice de OSM guardado en la base.

Por qué existe
--------------
Casi todos los despachos nombran un cruce («ARLEGUI / TRASLAVIÑA»): 211 de 229
en los 30 días anteriores al 16-10-2026. Nominatim no sabe ubicar cruces y deja
el punto donde OSM ancla una de las dos calles, que en esos días quedó entre 30
metros y 3,5 km de la esquina. El cálculo del cruce lo hacía Overpass, y sus
servidores públicos fallan casi siempre desde Render: sólo 5 de 229 despachos
llegaron a la esquina.

`osm_cruces` tiene todos los nodos de la región donde se tocan dos vías de
nombre distinto (lo arma `scripts/osm_cruces.py`). Buscar ahí es una consulta a
la base: sin servicios externos, sin límite por segundo, en milisegundos.

Cómo busca
----------
1. Trae los nodos de la comuna cuya vía termina con la misma palabra que alguna
   de las dos calles (`nombres_via.ultima_palabra`, con índice).
2. Se queda con los que calzan con las dos calles según `nombres_calzan`, la
   misma regla que ya usaba Overpass. Si ninguno calza exacto, acepta uno donde
   una calle calza exacta y la otra se parece lo suficiente (un error de tipeo
   de la central, `CRUCES_PARECIDO_MIN`).
3. Junta los nodos a menos de `CRUCES_AGRUPAR_M`: una avenida de doble calzada
   cruza dos veces a la transversal y es la misma esquina.

Devuelve **todas** las esquinas posibles, y quien llama decide: con una sola, es
ésa; con varias (dos calles que se cruzan dos veces en la comuna), la más
cercana al punto de Nominatim, o ninguna.

Sin comuna no se busca: «PRAT / YUNGAY» existe en media región, y un pin en la
ciudad equivocada es peor que no tener pin.

**Nunca lanza.** Sin índice cargado, sin base o con un error, devuelve `[]` y la
geocodificación sigue por Nominatim como antes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text

from app.collectors.geoservices import normalise_text
from app.collectors.overpass import distancia_m
from app.core.config import settings
from app.core.nombres_via import (
    nombre_nucleo,
    nombres_calzan,
    parecidos,
    sin_numeracion,
    ultima_palabra,
)

logger = logging.getLogger(__name__)

METODO_NODO = "nodo"
METODO_PARECIDO = "parecido"


@dataclass(frozen=True, slots=True)
class CruceLocal:
    """Una esquina del índice: el centro de los nodos que la forman."""

    lat: float
    lon: float
    #: Nombre de OSM de la vía que calzó con la primera calle, y con la segunda.
    via_1: str
    via_2: str
    #: Cuántos nodos de OSM forman la esquina (2 o más en doble calzada).
    nodos: int
    #: `nodo` (los dos nombres calzan) o `parecido` (uno con error de tipeo).
    metodo: str
    #: Similitud del nombre aproximado, con `metodo="parecido"`.
    parecido: float | None = None
    #: Comunas del índice para la esquina, normalizadas («vina del mar»).
    comunas: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Nodo:
    lat: float
    lon: float
    via_1: str
    via_2: str
    metodo: str
    parecido: float | None
    comunas: tuple[str, ...] = ()


_CONSULTA = text(
    f"""
    SELECT nombre_a, nombre_b, ST_Y(geom) AS lat, ST_X(geom) AS lon, comunas
    FROM {settings.DB_SCHEMA}.osm_cruces
    WHERE comunas && CAST(:comunas AS VARCHAR[])
      AND (ultima_a = ANY(CAST(:ultimas AS VARCHAR[]))
           OR ultima_b = ANY(CAST(:ultimas AS VARCHAR[])))
    """
)


Fila = tuple[str, str, float, float, tuple[str, ...]]


def _clasificar(filas: Sequence[Fila], calle_1: str, calle_2: str) -> list[_Nodo]:
    """Los nodos que son el cruce pedido: exactos si hay; si no, los parecidos."""
    exactos: list[_Nodo] = []
    aproximados: list[_Nodo] = []
    minimo = settings.CRUCES_PARECIDO_MIN
    for nombre_a, nombre_b, lat, lon, comunas in filas:
        orientaciones = ((nombre_a, nombre_b), (nombre_b, nombre_a))
        exacta = next(
            (
                (osm_1, osm_2)
                for osm_1, osm_2 in orientaciones
                if nombres_calzan(calle_1, osm_1) and nombres_calzan(calle_2, osm_2)
            ),
            None,
        )
        if exacta is not None:
            exactos.append(_Nodo(lat, lon, exacta[0], exacta[1], METODO_NODO, None, comunas))
            continue
        for osm_1, osm_2 in orientaciones:
            if nombres_calzan(calle_1, osm_1):
                razon = parecidos(calle_2, osm_2, minimo)
            elif nombres_calzan(calle_2, osm_2):
                razon = parecidos(calle_1, osm_1, minimo)
            else:
                continue
            if razon is not None:
                aproximados.append(
                    _Nodo(lat, lon, osm_1, osm_2, METODO_PARECIDO, razon, comunas)
                )
                break
    return exactos or aproximados


def agrupar(nodos: Sequence[_Nodo], radio_m: float) -> list[CruceLocal]:
    """Junta los nodos de una misma esquina. La de más nodos primero."""
    grupos: list[list[_Nodo]] = []
    for nodo in nodos:
        for grupo in grupos:
            if distancia_m((grupo[0].lat, grupo[0].lon), (nodo.lat, nodo.lon)) <= radio_m:
                grupo.append(nodo)
                break
        else:
            grupos.append([nodo])

    cruces = []
    for grupo in grupos:
        razones = [n.parecido for n in grupo if n.parecido is not None]
        cruces.append(
            CruceLocal(
                lat=round(sum(n.lat for n in grupo) / len(grupo), 7),
                lon=round(sum(n.lon for n in grupo) / len(grupo), 7),
                via_1=grupo[0].via_1,
                via_2=grupo[0].via_2,
                nodos=len(grupo),
                metodo=grupo[0].metodo,
                parecido=round(max(razones), 3) if razones else None,
                comunas=tuple(sorted({c for n in grupo for c in n.comunas})),
            )
        )
    cruces.sort(key=lambda c: c.nodos, reverse=True)
    return cruces


async def buscar(
    calle_1: str | None, calle_2: str | None, comunas: Sequence[str | None]
) -> list[CruceLocal]:
    """Las esquinas de `calle_1` con `calle_2` en esas comunas. `[]` si nada."""
    if not settings.CRUCES_LOCALES_ENABLED:
        return []
    calle_1 = sin_numeracion(calle_1)
    calle_2 = sin_numeracion(calle_2)
    normalizadas = sorted({normalise_text(c) for c in comunas if c and str(c).strip()})
    if not calle_1 or not calle_2 or not normalizadas:
        return []
    # «AVENIDA SAN MARTÍN / SAN MARTIN» no es un cruce: es la misma calle dos veces.
    if nombre_nucleo(calle_1) == nombre_nucleo(calle_2):
        return []
    ultimas = sorted({u for u in (ultima_palabra(calle_1), ultima_palabra(calle_2)) if u})
    if not ultimas:
        return []

    try:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            resultado = await session.execute(
                _CONSULTA, {"comunas": normalizadas, "ultimas": ultimas}
            )
            filas: list[Fila] = [
                (str(a), str(b), float(lat), float(lon), tuple(comunas or ()))
                for a, b, lat, lon, comunas in resultado
            ]
    except Exception as exc:
        logger.warning(
            "no se pudo consultar el índice de cruces; sigue Nominatim",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return []

    return agrupar(_clasificar(filas, calle_1, calle_2), settings.CRUCES_AGRUPAR_M)


def comuna_de(cruce: CruceLocal, pedidas: Sequence[str | None]) -> str | None:
    """La primera comuna pedida que la esquina tiene, con su nombre tal como vino."""
    for comuna in pedidas:
        if comuna and normalise_text(comuna) in cruce.comunas:
            return str(comuna)
    return None


def mas_cercano(
    cruces: Sequence[CruceLocal], punto: tuple[float, float], *, maximo_m: float
) -> CruceLocal | None:
    """La esquina más cercana a `punto` (lat, lon), si está a menos de `maximo_m`."""
    if not cruces:
        return None
    mejor = min(cruces, key=lambda c: distancia_m((c.lat, c.lon), punto))
    return mejor if distancia_m((mejor.lat, mejor.lon), punto) <= maximo_m else None


__all__ = [
    "METODO_NODO",
    "METODO_PARECIDO",
    "CruceLocal",
    "agrupar",
    "buscar",
    "comuna_de",
    "mas_cercano",
]
