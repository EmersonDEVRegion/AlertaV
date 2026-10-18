"""Área afectada de un corte: la zona sin servicio, no sólo un punto.

Las tres empresas publican más que un punto, cada una a su manera:

* **Chilquinta** lista en `segmentos` la ubicación de los clientes y
  transformadores de la orden (cientos o miles de puntos). Su visor dibuja la
  envolvente convexa de todos ellos, que exagera: en la orden `1718636-1`
  (Rodelillo, 17-10-2026) la envolvente mide 2,4 km² y los clientes ocupan
  ~1,2. Acá se dibuja la **mancha**: cada punto con un margen de
  `MANCHA_RADIO_M`, unidos. Es la misma idea que el polígono que publica CGE.
* **CGE** publica el polígono del sector en su KMZ (`<Polygon>`): se usa tal cual.
* **Esval** publica los polígonos de cada sector en el KML de su visor.

Por qué la mancha se calcula sobre una grilla y no con PostGIS
---------------------------------------------------------------
`ST_Buffer` sobre los 1 718 puntos de esa orden tardó 18 s (PostGIS 3.4); las
órdenes más grandes traen 9 671. Con una grilla de `CELDA_M` metros y numpy, la
misma mancha sale en milisegundos: se marcan las celdas con puntos, se dilatan
con un disco del radio pedido y se recorre el borde de lo marcado. El borde
escalonado se suaviza con Douglas-Peucker. Todo es puro: se prueba sin base.

Las coordenadas salen en GeoJSON (`[lon, lat]`) con 5 decimales (~1 m).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

import numpy as np

#: Margen alrededor de cada cliente. Dos clientes a menos del doble quedan en
#: una sola mancha: una calle con casas cada 20–40 m se ve continua.
MANCHA_RADIO_M = 60.0
#: Lado de la celda. Con 20 m el escalón queda bajo la tolerancia de abajo.
CELDA_M = 20.0
#: Douglas-Peucker: borra el escalón de la grilla (diagonal de 14 m).
TOLERANCIA_M = 15.0
#: Tope de celdas de la grilla. Una orden rural de 70 km² cabe con 20 m; si
#: alguna fuese más grande, la celda crece en vez de pedir memoria sin límite.
MAX_CELDAS = 1_500_000
DECIMALES = 5
#: Vértices máximos de una mancha. Una orden rural con miles de clientes
#: dispersos da un borde muy recortado; pasado este tope se simplifica más
#: fuerte (la tolerancia se duplica) hasta caber: la ficha y el mapa no
#: necesitan más detalle, y cada corte viaja en `/incidents/active`.
MAX_VERTICES = 1_200

#: Metros por grado de latitud. La longitud se escala por cos(lat).
_M_POR_GRADO_LAT = 110_574.0
_M_POR_GRADO_LON_ECUADOR = 111_320.0

Punto = tuple[float, float]  # (lat, lon)


def as_float(valor: Any) -> float | None:
    """Número finito desde número o texto («-33.06»), o `None`.

    Propio y no el de `collectors.geoservices`: este módulo lo usan también el
    repositorio y los servicios, y depender de los collectors cerraría un ciclo
    de importaciones.
    """
    if isinstance(valor, bool):
        return None
    try:
        numero = float(str(valor).strip()) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


# --- Lectura de vértices -----------------------------------------------------


def vertices_de_segmentos(segmentos: Any) -> list[Punto]:
    """Los vértices `latitud_min`/`longitud_min` de Chilquinta, venga como venga.

    El archivo publica una lista de listas; se acepta también una lista plana.
    Lo ilegible se salta: un vértice roto no puede tumbar la orden.
    """
    puntos: list[Punto] = []
    if not isinstance(segmentos, Sequence) or isinstance(segmentos, str | bytes):
        return puntos

    def leer(elemento: Any) -> None:
        if not isinstance(elemento, Mapping):
            return
        lat = as_float(elemento.get("latitud_min"))
        lon = as_float(elemento.get("longitud_min"))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            puntos.append((lat, lon))

    for elemento in segmentos:
        if isinstance(elemento, Mapping):
            leer(elemento)
        elif isinstance(elemento, Sequence) and not isinstance(elemento, str | bytes):
            for punto in elemento:
                leer(punto)
    return puntos


# --- La mancha ---------------------------------------------------------------


class _Proyeccion:
    """Plano local en metros alrededor de los puntos (equirectangular).

    Para áreas de pocos kilómetros el error es de centímetros, y evita depender
    de pyproj sólo para esto.
    """

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = lat0
        self.lon0 = lon0
        self.kx = _M_POR_GRADO_LON_ECUADOR * math.cos(math.radians(lat0))
        self.ky = _M_POR_GRADO_LAT

    def a_metros(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def a_grados(self, x: float, y: float) -> list[float]:
        return [
            round(self.lon0 + x / self.kx, DECIMALES),
            round(self.lat0 + y / self.ky, DECIMALES),
        ]


def mancha(
    puntos: Sequence[Punto],
    *,
    radio_m: float = MANCHA_RADIO_M,
    celda_m: float = CELDA_M,
    tolerancia_m: float = TOLERANCIA_M,
) -> dict[str, Any] | None:
    """Puntos `(lat, lon)` → MultiPolygon GeoJSON con un margen de `radio_m`.

    `None` sin puntos. Un punto solo da un círculo (poligonal) de `radio_m`.
    """
    if not puntos:
        return None
    lats = np.fromiter((p[0] for p in puntos), dtype=np.float64, count=len(puntos))
    lons = np.fromiter((p[1] for p in puntos), dtype=np.float64, count=len(puntos))
    proyeccion = _Proyeccion(float(lats.mean()), float(lons.mean()))
    xs = (lons - proyeccion.lon0) * proyeccion.kx
    ys = (lats - proyeccion.lat0) * proyeccion.ky

    ancho = float(xs.max() - xs.min()) + 2 * radio_m
    alto = float(ys.max() - ys.min()) + 2 * radio_m
    if (ancho / celda_m) * (alto / celda_m) > MAX_CELDAS:
        celda_m = math.sqrt(ancho * alto / MAX_CELDAS)

    radio_celdas = max(1, round(radio_m / celda_m))
    # Margen de celdas vacías alrededor: la dilatación con `np.roll` no puede
    # dar la vuelta y ensuciar el borde opuesto.
    margen = radio_celdas + 2
    x0 = float(xs.min()) - margen * celda_m
    y0 = float(ys.min()) - margen * celda_m
    nx = int((xs.max() - x0) // celda_m) + margen + 1
    ny = int((ys.max() - y0) // celda_m) + margen + 1

    marcadas = np.zeros((ny, nx), dtype=bool)
    ix = ((xs - x0) // celda_m).astype(np.int64)
    iy = ((ys - y0) // celda_m).astype(np.int64)
    marcadas[iy, ix] = True

    llenas = np.zeros_like(marcadas)
    for dy in range(-radio_celdas, radio_celdas + 1):
        for dx in range(-radio_celdas, radio_celdas + 1):
            if dx * dx + dy * dy <= radio_celdas * radio_celdas:
                llenas |= np.roll(np.roll(marcadas, dy, axis=0), dx, axis=1)

    anillos = _bordes(llenas)
    tolerancia = tolerancia_m / celda_m
    # Agujeros menores que un disco de la mancha: un patio sin clientes, no
    # una zona con luz. Se rellenan.
    agujero_minimo = math.pi * radio_celdas * radio_celdas
    poligonos = _armar_poligonos(anillos, tolerancia, agujero_minimo)
    tope = tolerancia * 4
    while sum(len(a) for p in poligonos for a in p) > MAX_VERTICES and tolerancia < tope:
        tolerancia *= 2
        poligonos = _armar_poligonos(anillos, tolerancia, agujero_minimo)
    if not poligonos:
        return None

    def a_grados(anillo: Sequence[tuple[float, float]]) -> list[list[float]]:
        return [proyeccion.a_grados(x0 + i * celda_m, y0 + j * celda_m) for i, j in anillo]

    return {
        "type": "MultiPolygon",
        "coordinates": [[a_grados(anillo) for anillo in poligono] for poligono in poligonos],
    }


#: Direcciones del borde y su giro a la izquierda (antihorario).
_E, _N, _O, _S = (1, 0), (0, 1), (-1, 0), (0, -1)
_IZQUIERDA = {_E: _N, _N: _O, _O: _S, _S: _E}
_DERECHA = {v: k for k, v in _IZQUIERDA.items()}


def _bordes(llenas: np.ndarray) -> list[list[tuple[int, int]]]:
    """Anillos del borde de las celdas llenas, sobre las esquinas de la grilla.

    Cada celda llena aporta los lados que dan a una vacía, orientados con el
    interior a la izquierda: los contornos exteriores salen antihorarios y los
    agujeros, horarios. Donde dos celdas se tocan sólo por la esquina hay dos
    salidas posibles; se gira a la izquierda, que sigue pegado a la misma celda
    y deja dos anillos que se tocan en un punto en vez de uno que se cruza.
    """
    relleno = np.pad(llenas, 1)
    centro = relleno[1:-1, 1:-1]
    salidas: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def agregar(mascara: np.ndarray, desde: tuple[int, int], direccion: tuple[int, int]) -> None:
        filas, columnas = np.nonzero(mascara)
        for j, i in zip(filas.tolist(), columnas.tolist(), strict=True):
            inicio = (i + desde[0], j + desde[1])
            salidas.setdefault(inicio, []).append(direccion)

    # Lado inferior (hacia el este), derecho (norte), superior (oeste), izquierdo (sur).
    agregar(centro & ~relleno[:-2, 1:-1], (0, 0), _E)
    agregar(centro & ~relleno[1:-1, 2:], (1, 0), _N)
    agregar(centro & ~relleno[2:, 1:-1], (1, 1), _O)
    agregar(centro & ~relleno[1:-1, :-2], (0, 1), _S)

    anillos: list[list[tuple[int, int]]] = []
    while salidas:
        inicio = next(iter(salidas))
        anillo = [inicio]
        punto = inicio
        direccion = salidas[punto][0]
        while True:
            opciones = salidas.get(punto)
            if not opciones:
                break
            if len(opciones) == 1:
                elegida = opciones[0]
            else:
                preferidas = (_IZQUIERDA[direccion], direccion, _DERECHA[direccion])
                elegida = next((d for d in preferidas if d in opciones), opciones[0])
            opciones.remove(elegida)
            if not opciones:
                del salidas[punto]
            direccion = elegida
            punto = (punto[0] + elegida[0], punto[1] + elegida[1])
            anillo.append(punto)
            if punto == inicio:
                break
        if len(anillo) >= 4 and anillo[0] == anillo[-1]:
            anillos.append(anillo)
    return anillos


def _area_con_signo(anillo: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in pairwise(anillo))


def _sin_colineales(anillo: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Quita los vértices intermedios de los tramos rectos del escalón."""
    puntos = anillo[:-1]
    if len(puntos) < 3:
        return anillo
    salida = []
    total = len(puntos)
    for k in range(total):
        a, b, c = puntos[k - 1], puntos[k], puntos[(k + 1) % total]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            salida.append(b)
    if len(salida) < 3:
        return anillo
    return [*salida, salida[0]]


def _douglas_peucker(
    puntos: Sequence[tuple[float, float]], tolerancia: float
) -> list[tuple[float, float]]:
    if len(puntos) < 3:
        return list(puntos)
    (x1, y1), (x2, y2) = puntos[0], puntos[-1]
    dx, dy = x2 - x1, y2 - y1
    largo = math.hypot(dx, dy)
    peor, indice = -1.0, 0
    for k in range(1, len(puntos) - 1):
        px, py = puntos[k]
        if largo == 0:
            distancia = math.hypot(px - x1, py - y1)
        else:
            distancia = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / largo
        if distancia > peor:
            peor, indice = distancia, k
    if peor <= tolerancia:
        return [puntos[0], puntos[-1]]
    izquierda = _douglas_peucker(puntos[: indice + 1], tolerancia)
    derecha = _douglas_peucker(puntos[indice:], tolerancia)
    return izquierda[:-1] + derecha


def simplificar_anillo(
    anillo: Sequence[tuple[float, float]], tolerancia: float
) -> list[tuple[float, float]]:
    """Douglas-Peucker sobre un anillo cerrado. Nunca baja de un triángulo.

    El anillo se parte en el vértice más lejano del primero, para que el corte
    del anillo no dependa de dónde empezó el recorrido.
    """
    puntos = list(anillo)
    if len(puntos) < 5 or tolerancia <= 0:
        return puntos
    origen = puntos[0]
    lejano = max(
        range(len(puntos) - 1),
        key=lambda k: (puntos[k][0] - origen[0]) ** 2 + (puntos[k][1] - origen[1]) ** 2,
    )
    ida = _douglas_peucker(puntos[: lejano + 1], tolerancia)
    vuelta = _douglas_peucker(puntos[lejano:], tolerancia)
    simplificado = ida[:-1] + vuelta
    if len(simplificado) < 4:
        return puntos
    return simplificado


def _armar_poligonos(
    anillos: Sequence[list[tuple[int, int]]], tolerancia: float, agujero_minimo: float = 0.0
) -> list[list[list[tuple[float, float]]]]:
    """Exteriores (antihorarios) con sus agujeros (horarios) adentro."""
    exteriores: list[list[tuple[float, float]]] = []
    agujeros: list[list[tuple[float, float]]] = []
    for anillo in anillos:
        limpio = simplificar_anillo(_sin_colineales(anillo), tolerancia)
        if len(limpio) < 4:
            continue
        area = _area_con_signo(limpio)
        if area > 0:
            exteriores.append(limpio)
        elif area < -agujero_minimo:
            agujeros.append(limpio)

    poligonos: list[list[list[tuple[float, float]]]] = [[e] for e in exteriores]
    for agujero in agujeros:
        x, y = agujero[0]
        candidatos = [
            k for k, exterior in enumerate(exteriores) if _dentro_del_anillo(x, y, exterior)
        ]
        if candidatos:
            menor = min(candidatos, key=lambda k: _area_con_signo(exteriores[k]))
            poligonos[menor].append(agujero)
    return poligonos


# --- Polígonos que publica la empresa -----------------------------------------


def multipoligono(
    anillos: Iterable[Sequence[Sequence[float]]], *, tolerancia_m: float = 0.0
) -> dict[str, Any] | None:
    """Anillos `[[lon, lat], …]` (uno por polígono, sin agujeros) → MultiPolygon.

    Es la forma de CGE (`<Polygon>` del KMZ) y de Esval (un anillo por sector).
    Los anillos con menos de tres vértices distintos se descartan; los abiertos
    se cierran. `tolerancia_m` > 0 los simplifica.
    """
    poligonos: list[list[list[list[float]]]] = []
    for anillo in anillos:
        puntos: list[tuple[float, float]] = []
        for vertice in anillo:
            if not isinstance(vertice, Sequence) or len(vertice) < 2:
                continue
            lon, lat = as_float(vertice[0]), as_float(vertice[1])
            if lon is None or lat is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            puntos.append((lon, lat))
        if len(set(puntos)) < 3:
            continue
        if puntos[0] != puntos[-1]:
            puntos.append(puntos[0])
        if tolerancia_m > 0:
            lat0 = sum(p[1] for p in puntos) / len(puntos)
            proyeccion = _Proyeccion(lat0, puntos[0][0])
            planos = [proyeccion.a_metros(lat, lon) for lon, lat in puntos]
            planos = simplificar_anillo(planos, tolerancia_m)
            redondeados = [proyeccion.a_grados(x, y) for x, y in planos]
        else:
            redondeados = [[round(lon, DECIMALES), round(lat, DECIMALES)] for lon, lat in puntos]
        poligonos.append([redondeados])
    if not poligonos:
        return None
    return {"type": "MultiPolygon", "coordinates": poligonos}


def unir(areas: Iterable[Any]) -> dict[str, Any] | None:
    """Varias áreas (Polygon o MultiPolygon) en un solo MultiPolygon.

    No disuelve los solapes: para dibujar y para preguntar «¿este punto está
    adentro?» da lo mismo, y disolver pediría una librería de geometría.
    """
    poligonos: list[Any] = []
    for area in areas:
        for poligono in poligonos_de(area):
            poligonos.append(poligono)
    if not poligonos:
        return None
    return {"type": "MultiPolygon", "coordinates": poligonos}


def poligonos_de(area: Any) -> list[Any]:
    """Los polígonos de un Polygon o MultiPolygon GeoJSON. `[]` si no lo es."""
    if not isinstance(area, Mapping):
        return []
    coordenadas = area.get("coordinates")
    if not isinstance(coordenadas, list):
        return []
    if area.get("type") == "Polygon":
        return [coordenadas] if coordenadas else []
    if area.get("type") == "MultiPolygon":
        return [p for p in coordenadas if isinstance(p, list) and p]
    return []


# --- Consultas ---------------------------------------------------------------


def _dentro_del_anillo(x: float, y: float, anillo: Sequence[Sequence[float]]) -> bool:
    """Rayo hacia el este (par/impar). Sobre `[x, y]`, sea plano o `[lon, lat]`."""
    dentro = False
    total = len(anillo)
    for k in range(total):
        x1, y1 = anillo[k][0], anillo[k][1]
        x2, y2 = anillo[(k + 1) % total][0], anillo[(k + 1) % total][1]
        if (y1 > y) != (y2 > y):
            cruce = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cruce:
                dentro = not dentro
    return dentro


def contiene(area: Any, lat: float, lon: float) -> bool:
    """¿El punto cae dentro del área (y fuera de sus agujeros)?"""
    for poligono in poligonos_de(area):
        exterior, *agujeros = poligono
        if _dentro_del_anillo(lon, lat, exterior) and not any(
            _dentro_del_anillo(lon, lat, agujero) for agujero in agujeros
        ):
            return True
    return False


def caja(area: Any) -> tuple[float, float, float, float] | None:
    """`(sur, oeste, norte, este)` del área, o `None` si no tiene vértices."""
    lats: list[float] = []
    lons: list[float] = []
    for poligono in poligonos_de(area):
        for vertice in poligono[0] if poligono else []:
            lons.append(float(vertice[0]))
            lats.append(float(vertice[1]))
    if not lats:
        return None
    return min(lats), min(lons), max(lats), max(lons)


__all__ = [
    "CELDA_M",
    "MANCHA_RADIO_M",
    "TOLERANCIA_M",
    "caja",
    "contiene",
    "mancha",
    "multipoligono",
    "poligonos_de",
    "simplificar_anillo",
    "unir",
    "vertices_de_segmentos",
]
