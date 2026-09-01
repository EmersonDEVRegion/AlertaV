#!/usr/bin/env python3
"""Extractor de una sola vez del Mapa de Amenaza Sísmica del CSN (MASCSN26).

    python -m scripts.fetch_seismic_hazard
    python -m scripts.fetch_seismic_hazard --dry-run
    python -m scripts.fetch_seismic_hazard --out static/geo/amenaza_sismica_valpo.json

Qué es esta capa, y por qué NO va por el pipeline de cinco minutos
==================================================================
Todo lo que recolecta AlertaV hasta ahora responde a la pregunta «¿qué está
pasando ahora?». Esta capa responde a otra: «¿cuánto puede llegar a moverse el
suelo acá?». Es un modelo probabilístico publicado una vez, con un número de
versión, que cambiará cuando el CSN publique el MASCSN27 — probablemente en
años.

Meterlo en el CRON sería consultar cada cinco minutos, para siempre, un archivo
que no cambia: 105 120 peticiones al año a un servidor universitario para
descubrir 105 120 veces que nada cambió. Y consultarlo en vivo cuando un usuario
abre el mapa sería peor: sumaría la latencia del CSN a la nuestra y ataría
nuestra disponibilidad a la suya.

Por eso: se extrae una vez, se procesa, se deja como archivo estático, y se
vuelve a correr a mano el día que el CSN publique una versión nueva. El
resultado queda versionado en el repositorio como cualquier otro artefacto.

De dónde salen los datos, y por qué no del visor
================================================
El visor por comunas (`MASCSN26_visor_comunas.html`) es un contenedor que
embebe `owl.csn.uchile.cl/visu_pga_comunas.html`, una aplicación JavaScript que
carga sus geometrías por rutas internas no documentadas.

**Se descartó rasparlo.** El CSN publica el mismo modelo como producto
descargable en su página de descargas:

    https://owl.csn.uchile.cl/MapaAmenazaSismica/MASCSN26.csv

Esa es la fuente sancionada: documentada, estable, con nombre versionado y
pensada para que terceros la usen. Reconstruir las llamadas internas de un visor
habría dado el mismo dato por un camino que se rompe con el próximo rediseño y
que además nadie prometió mantener.

La regla que dejó escrita la saga de Chilquinta —seis iteraciones peleando con
una ruta que el visor nunca llamaba— aplica al revés esta vez: **antes de
deducir cómo funciona un visor, conviene mirar si la institución ya publica el
dato**. Acá lo publicaba.

Qué trae el CSV, y qué hay que hacerle
======================================
Es una **grilla de puntos**, no polígonos::

    lon,lat,PGA-0.1,SA(0.3)-0.1,SA(1.0)-0.1,SA(3.0)-0.1,PGA-0.02,SA(0.3)-0.02,…
    -76.03354,-16.99716,0.1342392,0.2877012,0.1158134,0.02881919,0.2774902,…

* `PGA` — aceleración máxima del suelo, en *g*.
* `SA(T)` — aceleración espectral al período T segundos, en *g*. Es lo que mira
  un ingeniero estructural: `SA(0.3)` pesa sobre edificios bajos y `SA(3.0)`
  sobre los altos.
* El sufijo es la **probabilidad de excedencia en 50 años**: `-0.1` es 10 %
  (período de retorno ≈ 475 años, el del diseño sísmico habitual) y `-0.02` es
  2 % (≈ 2475 años, el de estructuras críticas).

Como son puntos y el mapa necesita superficie, cada punto se convierte en la
**celda** que representa —un rectángulo centrado en él— igual que hace el visor
del CSN, que rotula ese control como «Opacidad celdas». El paso de la grilla no
se escribe a mano: se **infiere de los propios datos** (ver `infer_grid_step`),
porque un paso hardcodeado que no coincida deja franjas en blanco o solapes, y
ninguna de las dos cosas se ve como un error — se ven como el mapa.

Decisiones técnicas
===================
**Streaming, no `.read()`.** El CSV es la grilla de Chile continental completo;
la V Región es una fracción diminuta. Descargarlo entero a memoria para
descartar el 99 % sería gastar memoria por un dato que se tira. Con
`client.stream()` y parseo línea a línea, el pico de memoria es una fila, no un
archivo, y el filtro espacial actúa mientras la descarga aún baja.

**Sin dependencias nuevas.** El recorte es contra una caja alineada a los ejes y
las celdas son rectángulos: `shapely` no aporta nada que no haga una
comparación de cuatro flotantes, y añadir una dependencia compilada al
`requirements` de producción por un script que corre una vez al año es un mal
negocio. `csv` y `json` de la biblioteca estándar bastan.

**El bounding box sale de `settings`.** `REGION_NORTH/SOUTH/EAST/WEST`, la misma
caja que usan los collectors de sismos, cortes y accidentes. Una segunda
definición del territorio es exactamente la deriva que este proyecto lleva
evitando desde el principio: mover la región tiene que mover todas las capas a
la vez. Se puede sobrescribir por CLI para exportar otra zona sin tocar el
`.env`.

**Aislado del pipeline en vivo.** No importa nada de `app.collectors`, no toca
la base de datos, no se registra en el `COLLECTORS` del runner y no aparece en
`collector_runs`. Comparte con la aplicación sólo la configuración del
territorio, que es un dato del despliegue y no del pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import statistics
import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("alertav.amenaza_sismica")

# --- Fuente ------------------------------------------------------------------

#: Producto descargable del CSN. El nombre lleva la versión del modelo
#: (MASCSN**26**), así que cuando publiquen el siguiente esta constante cambia y
#: el artefacto se regenera — no hay que adivinar si el contenido mutó.
CSN_HAZARD_CSV_URL = "https://owl.csn.uchile.cl/MapaAmenazaSismica/MASCSN26.csv"

#: Página donde el CSN enlista sus productos. Va en la procedencia del artefacto
#: para que quien lo encuentre dentro de dos años sepa de dónde salió.
CSN_DOWNLOADS_URL = (
    "https://owl.csn.uchile.cl/MapaAmenazaSismica/MASCSN26_descargas.html"
)

MODEL_VERSION = "MASCSN26"

#: Columnas de coordenadas, tal como vienen en la cabecera.
LON_COLUMN = "lon"
LAT_COLUMN = "lat"

#: Traducción de las columnas del CSV a nombres consumibles desde el frontend.
#:
#: Los nombres del CSV (`PGA-0.1`) mezclan la variable con la probabilidad de
#: excedencia y llevan puntos y paréntesis, que en una propiedad de GeoJSON son
#: incómodos de leer en una expresión de MapLibre. Se renombran a algo que dice
#: lo mismo sin necesitar una leyenda: `pga_475` es «PGA para período de retorno
#: de 475 años».
HAZARD_COLUMNS: dict[str, str] = {
    "PGA-0.1": "pga_475",
    "SA(0.3)-0.1": "sa03_475",
    "SA(1.0)-0.1": "sa10_475",
    "SA(3.0)-0.1": "sa30_475",
    "PGA-0.02": "pga_2475",
    "SA(0.3)-0.02": "sa03_2475",
    "SA(1.0)-0.02": "sa10_2475",
    "SA(3.0)-0.02": "sa30_2475",
}

#: Sin esta columna el artefacto no sirve para nada: es la variable que el mapa
#: pinta. Si el CSN la renombra, mejor fallar que exportar celdas vacías.
REQUIRED_HAZARD_COLUMN = "PGA-0.1"

# --- Red ---------------------------------------------------------------------

#: Un CSV de varios MB por una conexión universitaria. Generoso a propósito: es
#: un script manual, no hay nadie esperando, y un timeout corto sólo garantiza
#: reintentos innecesarios.
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0

#: Cortesía e identificación. Es la misma política que con Nominatim: si este
#: script molesta a alguien, que sepan a quién escribirle.
USER_AGENT = "AlertaV/0.1 (extractor de amenaza sismica; contacto: alertav@example.cl)"

# --- Salida ------------------------------------------------------------------

DEFAULT_OUTPUT = Path("static/geo/amenaza_sismica_valpo.json")

#: Decimales de las coordenadas. Cinco son ~1 m en el ecuador: mucho más fino
#: que una grilla de 5 km. Recortar acá reduce el archivo casi a la mitad sin
#: mover un píxel en pantalla.
COORD_PRECISION = 5

#: Decimales de los valores de aceleración. El modelo publica siete; a partir
#: del cuarto son ruido numérico frente a la incertidumbre real de un modelo
#: probabilístico, y ocupan bytes en cada una de las miles de celdas.
VALUE_PRECISION = 4


class HazardExtractionError(RuntimeError):
    """Fallo del que no se puede seguir: red agotada o formato irreconocible."""


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Caja envolvente en WGS84. Réplica local para no acoplar el script al ORM."""

    west: float
    south: float
    east: float
    north: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)


@dataclass(slots=True)
class GridPoint:
    """Un nodo de la grilla con sus valores de amenaza."""

    lon: float
    lat: float
    values: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractionStats:
    """Traza de la corrida. Es lo que se imprime al terminar."""

    rows_read: int = 0
    rows_malformed: int = 0
    points_kept: int = 0
    lat_step: float | None = None
    lon_step: float | None = None
    bytes_downloaded: int = 0

    @property
    def rows_discarded(self) -> int:
        return self.rows_read - self.points_kept - self.rows_malformed


# --- Descarga ----------------------------------------------------------------


async def stream_lines(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
    stats: ExtractionStats | None = None,
) -> AsyncIterator[str]:
    """Descarga el CSV y lo va entregando línea a línea.

    Reintenta ante errores de red y 5xx, que en servidores universitarios son
    frecuentes y transitorios. **No reintenta ante 4xx**: un 404 significa que
    el CSN movió o renombró el archivo, y reintentarlo tres veces sólo retrasa
    el diagnóstico de que hay que actualizar `CSN_HAZARD_CSV_URL`.

    Un reintento reanuda desde cero. Se podría pedir un `Range` y continuar donde
    se cortó, pero eso obliga a llevar estado del parseo a medias por una
    optimización que aquí no paga: el archivo pesa megabytes y el script corre
    una vez al año.
    """
    ultimo_error = "sin intentos"

    for intento in range(retries + 1):
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*;q=0.5"},
                ) as client,
                client.stream("GET", url) as response,
            ):
                    if response.status_code >= 500:
                        ultimo_error = f"HTTP {response.status_code}"
                        raise httpx.HTTPStatusError(
                            ultimo_error, request=response.request, response=response
                        )
                    if response.status_code >= 400:
                        raise HazardExtractionError(
                            f"HTTP {response.status_code} al descargar {url}. "
                            f"Si es 404, el CSN movió el archivo: revisa "
                            f"{CSN_DOWNLOADS_URL} y actualiza CSN_HAZARD_CSV_URL."
                        )

                    async for linea in response.aiter_lines():
                        if stats is not None:
                            # `aiter_lines` ya descartó el salto de línea; se
                            # suma para que el conteo se parezca al tamaño real.
                            stats.bytes_downloaded += len(linea) + 1
                        yield linea
                    return

        except HazardExtractionError:
            raise
        except httpx.HTTPStatusError as exc:
            ultimo_error = f"HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            ultimo_error = f"{type(exc).__name__}: {exc}"

        if intento < retries:
            espera = backoff * (2**intento)
            logger.warning(
                "descarga fallida (%s); reintento %d/%d en %.1f s",
                ultimo_error,
                intento + 1,
                retries,
                espera,
            )
            await asyncio.sleep(espera)

    raise HazardExtractionError(
        f"no se pudo descargar {url} tras {retries + 1} intentos ({ultimo_error})"
    )


# --- Parseo y filtro ---------------------------------------------------------


def validate_header(header: Sequence[str]) -> dict[str, str]:
    """Comprueba que la cabecera es la esperada. Devuelve el mapa de columnas útiles.

    Falla fuerte si falta lo esencial. Un CSV con otra cabecera produciría celdas
    sin valores —geometría perfecta, propiedades vacías— y eso se ve en el mapa
    como una capa gris uniforme, que es indistinguible de «acá la amenaza es
    baja». Un artefacto que miente en silencio es peor que no tener artefacto.
    """
    columnas = {nombre.strip() for nombre in header}

    faltantes = {LON_COLUMN, LAT_COLUMN} - columnas
    if faltantes:
        raise HazardExtractionError(
            f"el CSV no trae las columnas de coordenadas {sorted(faltantes)}. "
            f"Cabecera recibida: {list(header)[:12]}"
        )

    if REQUIRED_HAZARD_COLUMN not in columnas:
        raise HazardExtractionError(
            f"el CSV no trae '{REQUIRED_HAZARD_COLUMN}', que es la variable que "
            f"el mapa pinta. ¿Cambió el formato del CSN? Cabecera recibida: "
            f"{list(header)[:12]}"
        )

    presentes = {
        original: destino
        for original, destino in HAZARD_COLUMNS.items()
        if original in columnas
    }
    ausentes = set(HAZARD_COLUMNS) - set(presentes)
    if ausentes:
        # No es fatal: se exporta con las que haya. Pero tiene que verse, porque
        # el frontend podría estar esperando una que ya no viene.
        logger.warning(
            "columnas de amenaza ausentes en el CSV: %s", sorted(ausentes)
        )
    return presentes


def split_row(line: str) -> list[str]:
    """Una línea → campos, usando el lector de CSV de la biblioteca estándar.

    `csv.reader([linea])` en vez de `linea.split(",")`: respeta comillas y
    escapes sin que haya que pensarlos. La limitación conocida —un campo
    entrecomillado que abarque varias líneas— no aplica a esta fuente, que son
    diez columnas de números sin comillas, y a cambio permite consumir el
    archivo **línea a línea desde un flujo asíncrono**, que es justo lo que
    `csv.reader` sobre un iterador completo impide.
    """
    return next(csv.reader([line]), [])


def build_row_parser(
    header_line: str,
) -> tuple[dict[str, str], dict[str, int], int, int]:
    """Valida la cabecera y devuelve los índices para leer las filas."""
    cabecera = split_row(header_line)
    if not cabecera:
        raise HazardExtractionError("el CSV llegó vacío")

    columnas = validate_header(cabecera)
    indices = {nombre.strip(): posicion for posicion, nombre in enumerate(cabecera)}
    return columnas, indices, indices[LON_COLUMN], indices[LAT_COLUMN]


def parse_row(
    fields: Sequence[str],
    *,
    bbox: BoundingBox,
    columnas: dict[str, str],
    indices: dict[str, int],
    lon_idx: int,
    lat_idx: int,
) -> GridPoint | None:
    """Una fila → nodo, o None si es ilegible o cae fuera de la caja.

    Lanza `ValueError` sólo cuando la fila no se puede leer, para que el
    llamador distinga «ilegible» de «fuera de la región»: son dos cosas muy
    distintas y confundirlas escondería un cambio de formato detrás de una
    estadística de recorte.
    """
    try:
        lon = float(fields[lon_idx])
        lat = float(fields[lat_idx])
    except (IndexError, ValueError) as exc:
        raise ValueError("coordenadas ilegibles") from exc

    if not bbox.contains(lat, lon):
        return None

    valores: dict[str, float] = {}
    for original, destino in columnas.items():
        try:
            valores[destino] = round(float(fields[indices[original]]), VALUE_PRECISION)
        except (IndexError, ValueError):
            # Un valor suelto ilegible no invalida el nodo: las demás variables
            # siguen siendo utilizables y el frontend ya trata la ausencia como
            # «sin dato».
            continue

    return GridPoint(lon=lon, lat=lat, values=valores)


async def parse_stream(
    lines: AsyncIterator[str],
    bbox: BoundingBox,
    *,
    stats: ExtractionStats,
) -> list[GridPoint]:
    """Filtra la grilla al vuelo, mientras la descarga todavía baja.

    El recorte ocurre **dentro del mismo bucle que lee**, no después sobre una
    lista completa. Es la diferencia entre tener en memoria unos miles de nodos
    de la V Región o los cientos de miles de Chile continental para descartar el
    99 %: los nodos de Arica se descartan mientras los de Aysén aún viajan por
    la red.

    Una fila ilegible se cuenta y se salta. Un CSV de medio millón de líneas
    puede traer una fila truncada por un corte de transferencia —de hecho, la
    inspección inicial de esta fuente se cortó exactamente así—; perder el
    archivo entero por ella sería cambiar un dato faltante por todos.
    """
    puntos: list[GridPoint] = []
    contexto: tuple[dict[str, str], dict[str, int], int, int] | None = None

    async for linea in lines:
        if not linea.strip():
            continue

        if contexto is None:
            contexto = build_row_parser(linea)
            continue

        columnas, indices, lon_idx, lat_idx = contexto
        stats.rows_read += 1

        try:
            punto = parse_row(
                split_row(linea),
                bbox=bbox,
                columnas=columnas,
                indices=indices,
                lon_idx=lon_idx,
                lat_idx=lat_idx,
            )
        except ValueError:
            stats.rows_malformed += 1
            continue

        if punto is not None:
            stats.points_kept += 1
            puntos.append(punto)

    if contexto is None:
        raise HazardExtractionError("el CSV llegó vacío")

    return puntos


# --- Geometría ---------------------------------------------------------------


def infer_grid_step(values: Sequence[float], *, fallback: float) -> float:
    """Paso de la grilla a lo largo de UN eje regular, deducido de los datos.

    Se toma la **mediana** de las diferencias positivas entre valores
    consecutivos ordenados. La mediana y no la media porque un solo salto
    grande, en un hueco de la cobertura, arrastraría la media hacia arriba y
    engordaría todas las celdas.

    Un paso hardcodeado que no coincida con el real deja franjas sin pintar
    entre celdas o las hace solaparse. Ninguna de las dos cosas parece un error
    al mirar el mapa: parecen el mapa.

    .. warning::
       **Sólo sirve en el eje que se repite EXACTO entre líneas de la grilla.**
       En el MASCSN26 ese eje es la latitud: las 40 filas comparten los mismos
       40 valores, así que ``set()`` los colapsa y quedan 40 números separados
       por el paso real. La longitud NO cumple eso — ver `infer_row_step`, y la
       nota larga que hay ahí, que documenta el bug que costó este arreglo.
    """
    unicos = sorted(set(values))
    if len(unicos) < 2:
        return fallback

    saltos = [b - a for a, b in pairwise(unicos) if b > a]
    if not saltos:
        return fallback

    paso = statistics.median(saltos)
    return paso if paso > 0 else fallback


def infer_row_step(
    points: Sequence[GridPoint], *, lat_step: float, fallback: float
) -> float:
    """Paso en longitud, medido **dentro de cada fila** y no sobre el conjunto.

    ===========================================================================
    EL BUG QUE ESTA FUNCIÓN EXISTE PARA NO REPETIR
    ===========================================================================

    La versión anterior llamaba a `infer_grid_step` con **todas** las longitudes
    del recorte. Sobre el MASCSN26 eso devolvía ``0.00134°`` —unos 125 m— cuando
    el paso real de la grilla es ``0.0537°``, unos 5 km. Las celdas salían
    **cuarenta veces más angostas de lo que representan**, y el resultado en
    pantalla era exactamente lo que reportó el usuario: una cuadrícula de puntos
    sobre un territorio vacío, en vez de una superficie continua.

    El motivo es geométrico y no estadístico. La grilla del CSN está definida en
    una proyección métrica y convertida a geográficas, así que **cada fila tiene
    sus propias longitudes**: la columna que a ``lat = -33.14`` cae en
    ``-71.99927`` cae a ``lat = -32.87`` en ``-71.99861``. Con 40 filas y 41
    columnas, ``sorted(set(lons))`` no da 41 valores separados por el paso: da
    1 640 valores agrupados en 41 racimos de 40 lecturas casi idénticas. La
    mediana de las diferencias consecutivas mide entonces **la deriva dentro del
    racimo**, no la distancia entre racimos.

    Es un modo de falla silencioso de manual: ni el script ni el frontend lanzan
    nada, el ``metadata.cell_size_deg`` queda escrito con el número equivocado y
    el mapa se ve mal sin que nada explique por qué.

    ---------------------------------------------------------------------------
    LA CORRECCIÓN
    ---------------------------------------------------------------------------

    Se agrupan los nodos por fila —redondeando la latitud contra `lat_step`, que
    sí es fiable— y se mide la mediana de los saltos **dentro** de cada fila.
    Dentro de una fila no hay racimos: hay 41 columnas separadas por el paso
    real. La mediana de las medianas por fila descarta además cualquier fila
    incompleta del borde del recorte.
    """
    if lat_step <= 0 or not points:
        return fallback

    filas: dict[int, list[float]] = {}
    for punto in points:
        filas.setdefault(round(punto.lat / lat_step), []).append(punto.lon)

    pasos: list[float] = []
    for lons in filas.values():
        if len(lons) < 2:
            continue
        ordenadas = sorted(lons)
        saltos = [b - a for a, b in pairwise(ordenadas) if b > a]
        if saltos:
            pasos.append(statistics.median(saltos))

    if not pasos:
        return fallback

    paso = statistics.median(pasos)
    return paso if paso > 0 else fallback


def check_grid_coverage(
    points: Sequence[GridPoint], *, lon_step: float, lat_step: float
) -> None:
    """Avisa si el paso inferido no explica la nube de puntos que se recortó.

    La comprobación es una invariante barata: una grilla regular de ``F`` filas
    por ``C`` columnas tiene ``F × C`` nodos. Si el paso inferido fuera cuarenta
    veces menor de lo real —el bug de `infer_row_step`—, ``C`` saldría cuarenta
    veces mayor y el producto se dispararía frente al número de nodos que de
    verdad hay.

    No aborta: una cobertura con huecos legítimos (el recorte corta la costa en
    diagonal) también desvía el producto, y ese caso es normal. Pero un desvío
    de un orden de magnitud significa que el paso está mal y eso tiene que
    aparecer en la traza del operador, no descubrirse mirando el mapa.
    """
    if not points or lon_step <= 0 or lat_step <= 0:
        return

    lons = [punto.lon for punto in points]
    lats = [punto.lat for punto in points]
    columnas = round((max(lons) - min(lons)) / lon_step) + 1
    filas = round((max(lats) - min(lats)) / lat_step) + 1
    esperados = columnas * filas

    if esperados > len(points) * 4 or esperados * 4 < len(points):
        logger.warning(
            "el paso inferido no explica la nube: %d nodos recortados pero la "
            "grilla %d×%d implicaría ~%d. ¿Es correcto el paso "
            "(%.5f° lon × %.5f° lat)?",
            len(points),
            filas,
            columnas,
            esperados,
            lon_step,
            lat_step,
        )


def cell_polygon(
    point: GridPoint, lon_step: float, lat_step: float
) -> list[list[list[float]]]:
    """Rectángulo centrado en el nodo, en el orden de anillos que pide GeoJSON.

    Media celda a cada lado: el nodo representa el centro de su área de
    influencia, que es lo que el modelo calcula. El anillo se cierra repitiendo
    el primer vértice, como exige la RFC 7946 — sin eso, varios renderizadores
    dibujan el polígono pero fallan al calcular su interior.
    """
    dx, dy = lon_step / 2.0, lat_step / 2.0
    oeste = round(point.lon - dx, COORD_PRECISION)
    este = round(point.lon + dx, COORD_PRECISION)
    sur = round(point.lat - dy, COORD_PRECISION)
    norte = round(point.lat + dy, COORD_PRECISION)

    # Orden antihorario, empezando por el vértice suroeste.
    return [
        [
            [oeste, sur],
            [este, sur],
            [este, norte],
            [oeste, norte],
            [oeste, sur],
        ]
    ]


def build_feature_collection(
    points: Sequence[GridPoint],
    *,
    bbox: BoundingBox,
    stats: ExtractionStats,
    source_url: str = CSN_HAZARD_CSV_URL,
) -> dict[str, Any]:
    """Nodos → `FeatureCollection` de celdas, con su procedencia adjunta.

    El bloque `metadata` no es adorno: este archivo va a vivir en el repositorio
    durante años sin que nadie lo toque, y alguien tendrá que decidir si sigue
    vigente. Que el propio artefacto diga de qué versión del modelo salió, de
    qué URL, cuándo se generó y con qué caja, evita que esa persona tenga que
    reconstruirlo leyendo git.
    """
    if not points:
        raise HazardExtractionError(
            f"ningún nodo de la grilla cayó dentro de {bbox.as_tuple()}. "
            f"¿Es correcta la caja? El CSV cubre Chile continental."
        )

    # El orden importa: la latitud es el eje regular del MASCSN26 —las filas
    # comparten valor exacto— y su paso es lo que permite agrupar por fila para
    # medir el de la longitud. Al revés no funciona. Ver `infer_row_step`.
    lat_step = infer_grid_step([punto.lat for punto in points], fallback=0.045)
    lon_step = infer_row_step(points, lat_step=lat_step, fallback=lat_step)
    stats.lat_step, stats.lon_step = lat_step, lon_step
    check_grid_coverage(points, lon_step=lon_step, lat_step=lat_step)

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": cell_polygon(punto, lon_step, lat_step),
            },
            "properties": {
                # El centro se conserva además de la celda: sirve para etiquetar
                # y para volver a cruzar con la fuente sin recalcular nada.
                "lon": round(punto.lon, COORD_PRECISION),
                "lat": round(punto.lat, COORD_PRECISION),
                **punto.values,
            },
        }
        for punto in points
    ]

    return {
        "type": "FeatureCollection",
        "metadata": {
            "title": "Modelo Probabilistico de Amenaza Sismica — Region de Valparaiso",
            "model": MODEL_VERSION,
            "producer": "Centro Sismologico Nacional, Universidad de Chile",
            "source_url": source_url,
            "downloads_page": CSN_DOWNLOADS_URL,
            "license_note": (
                "Producto publico del CSN. Las referencias geograficas no tienen "
                "caracter oficial; ver la nota de DIFROL en la pagina del visor."
            ),
            "generated_at": datetime.now(UTC).isoformat(),
            "generator": "scripts/fetch_seismic_hazard.py",
            "bbox": list(bbox.as_tuple()),
            "cell_size_deg": {
                "lon": round(lon_step, 6),
                "lat": round(lat_step, 6),
            },
            "feature_count": len(features),
            "variables": {
                "pga_475": "PGA (g), 10% de excedencia en 50 anios (~475 anios)",
                "pga_2475": "PGA (g), 2% de excedencia en 50 anios (~2475 anios)",
                "sa03_475": "Aceleracion espectral T=0.3 s (g), 475 anios",
                "sa10_475": "Aceleracion espectral T=1.0 s (g), 475 anios",
                "sa30_475": "Aceleracion espectral T=3.0 s (g), 475 anios",
            },
            "note": (
                "Capa ESTATICA y probabilistica: describe la amenaza esperada, no "
                "un evento en curso. No pasa por el motor de correlacion."
            ),
        },
        "features": features,
    }


def summarise(collection: dict[str, Any], stats: ExtractionStats) -> str:
    """Resumen legible para el operador que corrió el script."""
    valores = [
        feature["properties"]["pga_475"]
        for feature in collection["features"]
        if "pga_475" in feature["properties"]
    ]
    lineas = [
        f"  filas leídas del CSV      {stats.rows_read:>9,}",
        f"  descartadas por la caja   {stats.rows_discarded:>9,}",
        f"  filas ilegibles           {stats.rows_malformed:>9,}",
        f"  celdas exportadas         {stats.points_kept:>9,}",
        f"  descargado                {stats.bytes_downloaded / 1_048_576:>9.1f} MB",
    ]
    if stats.lat_step and stats.lon_step:
        km = stats.lat_step * 111.32
        lineas.append(
            f"  tamaño de celda           {stats.lat_step:.5f}° lat "
            f"× {stats.lon_step:.5f}° lon  (~{km:.1f} km)"
        )
    if valores:
        lineas.append(
            f"  PGA 475 años (g)          min {min(valores):.3f} · "
            f"med {statistics.median(valores):.3f} · max {max(valores):.3f}"
        )
    return "\n".join(lineas)


# --- Orquestación ------------------------------------------------------------


async def extract(
    *,
    url: str,
    bbox: BoundingBox,
    timeout: float,
    retries: int,
) -> tuple[dict[str, Any], ExtractionStats]:
    """Descarga, filtra y arma el GeoJSON. Sin tocar disco."""
    stats = ExtractionStats()

    logger.info("descargando %s", url)
    lineas = stream_lines(url, timeout=timeout, retries=retries, stats=stats)
    puntos = await parse_stream(lineas, bbox, stats=stats)

    coleccion = build_feature_collection(puntos, bbox=bbox, stats=stats)
    return coleccion, stats


def write_output(collection: dict[str, Any], destination: Path) -> int:
    """Escribe el GeoJSON de forma atómica. Devuelve los bytes escritos.

    Se escribe a un temporal y se renombra: `os.replace` es atómico en el mismo
    sistema de archivos, así que un fallo a mitad de la escritura deja el
    artefacto anterior intacto en vez de un JSON truncado que el frontend
    intentaría parsear. Importa porque este archivo se sirve en producción.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporal = destination.with_suffix(destination.suffix + ".tmp")

    # `separators` sin espacios: en un archivo con miles de features, los
    # espacios tras cada coma son decenas de KB de nada.
    payload = json.dumps(collection, ensure_ascii=False, separators=(",", ":"))
    temporal.write_text(payload, encoding="utf-8")
    temporal.replace(destination)
    return len(payload.encode("utf-8"))


def resolve_bbox(args: argparse.Namespace) -> BoundingBox:
    """Caja del recorte: la de `settings` salvo que la CLI diga otra cosa.

    Importar `settings` acopla el script a la configuración de la aplicación, y
    es deliberado: la alternativa era escribir los cuatro números otra vez, y
    dos definiciones del territorio derivan. Si la importación falla —por
    ejemplo al correr el script fuera del entorno del backend— se cae a los
    valores de la V Región con un aviso, en vez de reventar.
    """
    if args.bbox:
        west, south, east, north = args.bbox
        return BoundingBox(west=west, south=south, east=east, north=north)

    try:
        from app.core.config import settings

        return BoundingBox(
            west=settings.REGION_WEST,
            south=settings.REGION_SOUTH,
            east=settings.REGION_EAST,
            north=settings.REGION_NORTH,
        )
    except Exception as exc:
        logger.warning(
            "no se pudo leer la configuración de la app (%s); se usa la caja "
            "por defecto de la V Región",
            exc,
        )
        return BoundingBox(west=-72.0, south=-33.8, east=-69.8, north=-32.0)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extrae el Mapa de Amenaza Sísmica del CSN y genera un GeoJSON de "
            "celdas recortado a la Región de Valparaíso."
        )
    )
    parser.add_argument(
        "--url", default=CSN_HAZARD_CSV_URL, help="CSV de origen del CSN."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Ruta del GeoJSON resultante (por defecto {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("OESTE", "SUR", "ESTE", "NORTE"),
        help="Caja del recorte. Por defecto, REGION_* de la configuración.",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Segundos."
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Descarga y procesa, pero no escribe el archivo.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    bbox = resolve_bbox(args)
    logger.info("recorte: %s", bbox.as_tuple())

    try:
        coleccion, stats = asyncio.run(
            extract(
                url=args.url, bbox=bbox, timeout=args.timeout, retries=args.retries
            )
        )
    except HazardExtractionError as exc:
        logger.error("extracción fallida: %s", exc)
        return 1

    print(summarise(coleccion, stats))

    if args.dry_run:
        print("\n  --dry-run: no se escribió nada.")
        return 0

    escritos = write_output(coleccion, args.out)
    print(f"\n  escrito {args.out} ({escritos / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
