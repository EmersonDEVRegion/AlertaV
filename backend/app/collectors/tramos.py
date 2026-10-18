"""De «Congestión en Viana desde Sucre hasta Von Schroeders» a una línea en el mapa.

Recibe lo que extrajo Gemini (`gemini.extraer_tramo`: vía, desde, hasta, punto,
ciudad) y lo ubica con datos propios de OSM:

1. **La vía** sale de `osm_vias` (su forma real, por nombre y comuna).
2. **Cada extremo o punto** se busca, en orden:

   * la esquina de la vía con esa calle (`osm_cruces`);
   * otra vía de OSM con ese nombre (una rotonda, un enlace, un puente): el
     punto de la vía principal más cercano a ella, si está a menos de 300 m;
   * el nomenclátor propio de lugares viales (`hitos.py`);
   * Nominatim, como último recurso.

   El punto se pega siempre a la vía, y uno a más de `MAX_DESVIO_M` de ella se
   descarta: un «Escuela de Operaciones» que Nominatim ubica en otra parte no
   puede torcer el tramo.
3. **El tramo** es el pedazo de la vía entre los dos extremos
   (`ST_LineSubstring`). Si la vía está partida en piezas que no se unen (una
   doble calzada mal conectada), se dibuja por las esquinas de la vía entre los
   dos extremos, que en una calle de damero queda igual de bien.

Lo que no se ubica no se inventa: el aviso entra sin línea y se lista en
«Ahora», y los nombres que faltaron quedan en `sin_ubicar` para completar el
nomenclátor.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from sqlalchemy import text

from app.collectors import cruces_locales, hitos
from app.collectors.geoservices import normalise_text
from app.collectors.lugares import COMUNAS_NORMALIZADAS
from app.core.config import settings
from app.core.nombres_via import nombres_calzan, sin_numeracion, ultima_palabra

logger = logging.getLogger(__name__)

#: Lo más lejos de la vía que puede quedar un extremo antes de pegarlo a ella.
MAX_DESVIO_M = 500.0
#: Una rotonda u otra vía nombrada como extremo: hasta dónde puede estar.
MAX_DESVIO_OTRA_VIA_M = 300.0
#: Un tramo más largo que esto es un error de ubicación, no un taco.
MAX_LARGO_M = 15_000.0
#: Dos piezas de la vía cuentan como la misma si el extremo está a menos de esto.
TOLERANCIA_PIEZA_M = 60.0

#: Sin ciudad en el aviso, el MTT habla casi siempre del Gran Valparaíso.
GRAN_VALPARAISO: tuple[str, ...] = (
    "valparaiso",
    "vina del mar",
    "concon",
    "quilpue",
    "villa alemana",
)

#: Métodos de ubicación, en `raw_data._mtt.ubicacion.metodo`.
TRAMO = "tramo"
TRAMO_ESQUINAS = "tramo_esquinas"
VIA_COMPLETA = "via_completa"
PUNTO = "punto"


@dataclass(slots=True)
class Ubicacion:
    """Dónde dibujar un aviso: una línea o un punto, en GeoJSON."""

    geometria: dict[str, Any]
    lat: float
    lon: float
    metodo: str
    largo_m: float | None = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "metodo": self.metodo,
            "largo_m": round(self.largo_m) if self.largo_m is not None else None,
        }


def comunas_de(ciudad: str | None) -> list[str]:
    """La comuna (normalizada, como en `osm_vias`) de la ciudad del aviso.

    El MTT la escribe como etiqueta («#ViñadelMar» → «ViñadelMar»), sin
    espacios: se compara sin espacios. Sin ciudad o sin calce, el Gran
    Valparaíso.
    """
    pegada = normalise_text(ciudad or "").replace(" ", "")
    if pegada:
        for normalizada, _ in COMUNAS_NORMALIZADAS:
            if normalizada.replace(" ", "") == pegada:
                return [normalizada]
    return list(GRAN_VALPARAISO)


def nombre_de_comuna(comunas: Sequence[str]) -> str | None:
    """El nombre canónico («Viña del Mar») si la búsqueda es en una sola comuna."""
    if len(comunas) != 1:
        return None
    return next((n for norm, n in COMUNAS_NORMALIZADAS if norm == comunas[0]), None)


def _punto_wkt(lat: float, lon: float) -> str:
    return f"POINT({lon} {lat})"


def distancia_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia aproximada (equirectangular) entre dos (lat, lon), en metros."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat) * 6_371_000
    dy = math.radians(b[0] - a[0]) * 6_371_000
    return math.hypot(dx, dy)


def ordenar_por_tramo(
    a: tuple[float, float], b: tuple[float, float], esquinas: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Las esquinas que caen entre `a` y `b`, en orden de `a` hacia `b`. Función pura.

    Se proyecta cada esquina sobre el segmento a→b: entra si cae dentro y no se
    aparta más de un tercio del largo (o 150 m). Sirve para calles de damero,
    que es donde se usa (ver el docstring del módulo).
    """
    lat0 = math.radians((a[0] + b[0]) / 2)

    def xy(p: tuple[float, float]) -> tuple[float, float]:
        return (
            math.radians(p[1] - a[1]) * math.cos(lat0) * 6_371_000,
            math.radians(p[0] - a[0]) * 6_371_000,
        )

    bx, by = xy(b)
    largo2 = bx * bx + by * by
    if largo2 < 1.0:
        return []
    largo = math.sqrt(largo2)
    margen = max(150.0, largo / 3)
    dentro: list[tuple[float, tuple[float, float]]] = []
    for esquina in esquinas:
        px, py = xy(esquina)
        t = (px * bx + py * by) / largo2
        if not 0.0 < t < 1.0:
            continue
        lejania = abs(px * by - py * bx) / largo
        if lejania <= margen:
            dentro.append((t, esquina))
    return [p for _, p in sorted(dentro)]


async def _ids_de_via(session: Any, nombre: str, comunas: Sequence[str]) -> list[int]:
    nombre = sin_numeracion(nombre)
    ultima = ultima_palabra(nombre)
    if not ultima:
        return []
    filas = (
        await session.execute(
            text(
                f"SELECT id, nombre FROM {settings.DB_SCHEMA}.osm_vias "
                "WHERE ultima = :ultima AND comunas && CAST(:comunas AS VARCHAR[])"
            ),
            {"ultima": ultima, "comunas": list(comunas)},
        )
    ).all()
    return [int(f.id) for f in filas if nombres_calzan(nombre, f.nombre)]


async def _pegar(
    session: Any, ids: Sequence[int], lat: float, lon: float
) -> tuple[float, float, float] | None:
    """El punto de la vía más cercano a (lat, lon) y a cuántos metros estaba."""
    fila = (
        await session.execute(
            text(
                "WITH v AS (SELECT ST_Union(geom) g FROM "
                f"{settings.DB_SCHEMA}.osm_vias WHERE id = ANY(:ids)), "
                "p AS (SELECT ST_GeomFromText(:p, 4326) g) "
                "SELECT ST_Y(ST_ClosestPoint(v.g, p.g)) lat, ST_X(ST_ClosestPoint(v.g, p.g)) lon, "
                "ST_Distance(v.g::geography, p.g::geography) d FROM v, p"
            ),
            {"ids": list(ids), "p": _punto_wkt(lat, lon)},
        )
    ).first()
    if fila is None or fila.lat is None:
        return None
    return float(fila.lat), float(fila.lon), float(fila.d)


async def _por_otra_via(
    session: Any, ids: Sequence[int], lugar: str, comunas: Sequence[str]
) -> tuple[float, float] | None:
    otros = [i for i in await _ids_de_via(session, lugar, comunas) if i not in ids]
    if not otros:
        return None
    fila = (
        await session.execute(
            text(
                "WITH v AS (SELECT ST_Union(geom) g FROM "
                f"{settings.DB_SCHEMA}.osm_vias WHERE id = ANY(:ids)), "
                f"o AS (SELECT ST_Union(geom) g FROM {settings.DB_SCHEMA}.osm_vias "
                "WHERE id = ANY(:otros)) "
                "SELECT ST_Y(ST_ClosestPoint(v.g, o.g)) lat, ST_X(ST_ClosestPoint(v.g, o.g)) lon, "
                "ST_Distance(v.g::geography, o.g::geography) d FROM v, o"
            ),
            {"ids": list(ids), "otros": otros},
        )
    ).first()
    if fila is None or fila.lat is None or float(fila.d) > MAX_DESVIO_OTRA_VIA_M:
        return None
    return float(fila.lat), float(fila.lon)


async def _por_nominatim(lugar: str, ciudad: str | None) -> tuple[float, float] | None:
    from app.collectors.nominatim import build_client, geocode, get_limiter

    try:
        async with build_client() as client:
            resultado = await geocode(
                client,
                {"street_1": lugar, "street_2": None, "city": ciudad},
                limiter=get_limiter(),
                comuna=ciudad,
            )
    except Exception as exc:
        logger.warning("Nominatim falló ubicando un lugar del MTT", extra={"error": str(exc)})
        return None
    return (resultado.lat, resultado.lon) if resultado else None


async def _extremo(
    session: Any,
    ids: Sequence[int],
    via: str,
    lugar: str,
    comunas: Sequence[str],
    ciudad: str | None,
    *,
    nominatim: bool,
) -> tuple[float, float] | None:
    """Un extremo o punto de la vía, ya pegado a ella. None si no se ubica."""
    esquinas = await cruces_locales.buscar(via, lugar, list(comunas))
    if esquinas:
        candidato: tuple[float, float] | None = (esquinas[0].lat, esquinas[0].lon)
    else:
        candidato = await _por_otra_via(session, ids, lugar, comunas)
    if candidato is None:
        hito = hitos.buscar(lugar, comunas)
        candidato = (hito.lat, hito.lon) if hito else None
    if candidato is None and nominatim:
        candidato = await _por_nominatim(lugar, ciudad)
    if candidato is None:
        return None
    pegado = await _pegar(session, ids, *candidato)
    if pegado is None or pegado[2] > MAX_DESVIO_M:
        return None
    return pegado[0], pegado[1]


async def _subtramo(
    session: Any, ids: Sequence[int], a: tuple[float, float], b: tuple[float, float]
) -> tuple[dict[str, Any], float, tuple[float, float]] | None:
    """El pedazo de la vía entre a y b, si una misma pieza los contiene a los dos."""
    fila = (
        await session.execute(
            text(
                "WITH piezas AS (SELECT (ST_Dump(ST_LineMerge(ST_Union(geom)))).geom g FROM "
                f"{settings.DB_SCHEMA}.osm_vias WHERE id = ANY(:ids)), "
                "pa AS (SELECT ST_GeomFromText(:a, 4326) g), "
                "pb AS (SELECT ST_GeomFromText(:b, 4326) g), "
                "c AS (SELECT piezas.g, ST_Distance(piezas.g::geography, pa.g::geography) da, "
                "ST_Distance(piezas.g::geography, pb.g::geography) db, "
                "ST_LineLocatePoint(piezas.g, pa.g) fa, ST_LineLocatePoint(piezas.g, pb.g) fb "
                "FROM piezas, pa, pb), "
                "t AS (SELECT ST_LineSubstring(g, LEAST(fa, fb), GREATEST(fa, fb)) g FROM c "
                "WHERE da <= :tol AND db <= :tol ORDER BY da + db LIMIT 1) "
                "SELECT ST_AsGeoJSON(g, 6) geo, ST_Length(g::geography) largo, "
                "ST_Y(ST_LineInterpolatePoint(g, 0.5)) lat, "
                "ST_X(ST_LineInterpolatePoint(g, 0.5)) lon "
                "FROM t"
            ),
            {
                "ids": list(ids),
                "a": _punto_wkt(*a),
                "b": _punto_wkt(*b),
                "tol": TOLERANCIA_PIEZA_M,
            },
        )
    ).first()
    if fila is None or fila.geo is None:
        return None
    return json.loads(fila.geo), float(fila.largo), (float(fila.lat), float(fila.lon))


async def _por_esquinas(
    via: str, comunas: Sequence[str], a: tuple[float, float], b: tuple[float, float]
) -> tuple[dict[str, Any], float, tuple[float, float]]:
    """El tramo dibujado por las esquinas de la vía entre a y b."""
    puntos = await cruces_locales.puntos_de_calle(
        via, list(comunas), separacion_m=0.0, maximo=10_000
    )
    medio = ordenar_por_tramo(a, b, puntos)
    recorrido = [a, *medio, b]
    largo = sum(distancia_m(p, q) for p, q in pairwise(recorrido))
    centro = recorrido[len(recorrido) // 2]
    geometria = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in recorrido]}
    return geometria, largo, centro


async def _via_completa(
    session: Any, ids: Sequence[int]
) -> tuple[dict[str, Any], float, tuple[float, float]] | None:
    fila = (
        await session.execute(
            text(
                f"WITH v AS (SELECT ST_Union(geom) g FROM {settings.DB_SCHEMA}.osm_vias "
                "WHERE id = ANY(:ids)) "
                "SELECT ST_AsGeoJSON(g, 6) geo, ST_Length(g::geography) largo, "
                "ST_Y(ST_PointOnSurface(g)) lat, ST_X(ST_PointOnSurface(g)) lon FROM v"
            ),
            {"ids": list(ids)},
        )
    ).first()
    if fila is None or fila.geo is None:
        return None
    return json.loads(fila.geo), float(fila.largo), (float(fila.lat), float(fila.lon))


async def ubicar(
    extraccion: dict[str, Any] | None,
    *,
    permitir_via_completa: bool = False,
    nominatim: bool = True,
    session: Any | None = None,
) -> tuple[Ubicacion | None, list[str]]:
    """Ubica un aviso del MTT. Devuelve `(ubicación o None, nombres sin ubicar)`.

    **Nunca lanza**: un fallo de la base o de Nominatim deja el aviso sin línea.
    """
    if not extraccion or not extraccion.get("via"):
        return None, []
    if session is None:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as propia:
            return await ubicar(
                extraccion,
                permitir_via_completa=permitir_via_completa,
                nominatim=nominatim,
                session=propia,
            )
    via = str(extraccion["via"])
    comunas = comunas_de(extraccion.get("ciudad"))
    ciudad = nombre_de_comuna(comunas)
    try:
        ids = await _ids_de_via(session, via, comunas)
        if not ids:
            return None, [via]
        desde, hasta = extraccion.get("desde"), extraccion.get("hasta")
        punto = extraccion.get("punto")
        sin_ubicar: list[str] = []

        if desde and hasta:
            a = await _extremo(session, ids, via, desde, comunas, ciudad, nominatim=nominatim)
            b = await _extremo(session, ids, via, hasta, comunas, ciudad, nominatim=nominatim)
            sin_ubicar = [n for n, p in ((desde, a), (hasta, b)) if p is None]
            if a and b and distancia_m(a, b) > 1.0:
                tramo = await _subtramo(session, ids, a, b)
                metodo = TRAMO
                if tramo is None:
                    tramo = await _por_esquinas(via, comunas, a, b)
                    metodo = TRAMO_ESQUINAS
                geometria, largo, centro = tramo
                if largo <= MAX_LARGO_M:
                    return Ubicacion(geometria, centro[0], centro[1], metodo, largo), []
                sin_ubicar = [desde, hasta]
            # Un solo extremo ubicado: se muestra como punto.
            unico = a or b
            if unico:
                geometria = {"type": "Point", "coordinates": [unico[1], unico[0]]}
                return Ubicacion(geometria, unico[0], unico[1], PUNTO), sin_ubicar

        if punto:
            p = await _extremo(session, ids, via, punto, comunas, ciudad, nominatim=nominatim)
            if p:
                geometria = {"type": "Point", "coordinates": [p[1], p[0]]}
                return Ubicacion(geometria, p[0], p[1], PUNTO), sin_ubicar
            sin_ubicar.append(punto)

        if permitir_via_completa and extraccion.get("toda_la_via"):
            completa = await _via_completa(session, ids)
            if completa is not None:
                geometria, largo, centro = completa
                return Ubicacion(geometria, centro[0], centro[1], VIA_COMPLETA, largo), sin_ubicar
        return None, sin_ubicar
    except Exception as exc:
        logger.warning(
            "no se pudo ubicar un aviso del MTT",
            extra={"via": via, "error": f"{type(exc).__name__}: {exc}"},
        )
        await session.rollback()
        return None, [via]


__all__ = [
    "GRAN_VALPARAISO",
    "MAX_DESVIO_M",
    "PUNTO",
    "TRAMO",
    "TRAMO_ESQUINAS",
    "VIA_COMPLETA",
    "Ubicacion",
    "comunas_de",
    "distancia_m",
    "ordenar_por_tramo",
    "ubicar",
]
