"""Lectura de la tabla de sismos del Centro Sismológico Nacional.

Estructura verificada (2026-08)
--------------------------------
El CSN publica un catálogo diario en
``/sismicidad/catalogo/AAAA/MM/AAAAMMDD.html`` con una tabla de cinco columnas::

    | Fecha Local / Lugar        | Fecha UTC           | Latitud / Longitud | Prof. | Magnitud |
    | 2026-08-19 19:33:16        | 2026-08-19 23:33:16 | -21.074  -69.015   | 109 km| 2.5 Mlv  |
    |   46 km al SO de Collahuasi|                     |                    |       |          |

Cuatro detalles del formato que condicionan todo el parseo:

1. **La primera celda contiene un enlace**, y ese enlace es lo más valioso de la
   fila: ``/sismicidad/informes/2026/08/379889.html``. El ``379889`` es el
   identificador del evento en el catálogo del CSN, y es lo único estable que
   entrega la página. Sin él habría que hashear los atributos, y los atributos
   cambian: el CSN revisa magnitud y profundidad horas después del sismo.
2. **La página ya publica la hora UTC** en su propia columna. Ver
   `parse_row` y el docstring del módulo del collector: eso convierte el
   problema de zona horaria en un problema de *preferencia de columna*, que es
   mucho más seguro que convertir a mano.
3. **Latitud y longitud viven en la misma celda**, separadas por espacios.
4. **La magnitud trae la escala pegada**: ``2.5 Mlv``, ``6.1 Mww``. La escala no
   es decorativa —Ml y Mw no son comparables entre sí— y se guarda aparte.

Este módulo es puro: entra HTML, salen dataclases. Es lo que permite testear el
parseo contra una copia real de la página sin tocar la red, que es la única
forma honesta de verificar un scraper.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

#: Zona horaria oficial de Chile continental. Se resuelve con la base de datos
#: IANA y **no** con un desplazamiento fijo: Chile cambia la hora dos veces al
#: año, así que un `-4` escrito a mano acierta en invierno y falla en verano.
#: Ver `parse_local_datetime`.
CHILE_TZ = ZoneInfo("America/Santiago")

#: Identificador del evento dentro del enlace al informe.
_REPORT_ID = re.compile(r"/(?P<id>\d+)\.html?$")

#: `2026-08-19 19:33:16`. Se acepta también sin segundos, por si el CSN los
#: omite en alguna vista.
_TIMESTAMP = re.compile(
    r"(?P<fecha>\d{4}-\d{2}-\d{2})[\sT]+(?P<hora>\d{2}:\d{2}(?::\d{2})?)"
)

#: `-21.074` / `-69.015`. Dos números con signo en la misma celda.
_COORD = re.compile(r"-?\d+\.\d+")

#: `109 km`, `5 km`, `109.5 km`.
_DEPTH = re.compile(r"(?P<valor>-?\d+(?:\.\d+)?)\s*km", re.IGNORECASE)

#: `2.5 Mlv`, `6.1 Mww`, `4.0 Ml`.
_MAGNITUDE = re.compile(
    r"(?P<valor>-?\d+(?:\.\d+)?)\s*(?P<escala>M[a-z]*)?", re.IGNORECASE
)

#: Columnas mínimas para que una fila sea un sismo. Menos que esto es una fila
#: de cabecera, un separador o una maquetación que cambió.
_MIN_CELLS = 5

#: Rangos físicos plausibles. No son validación de negocio sino detección de
#: parseo roto: si una "profundidad" sale de acá, se leyó la columna equivocada.
_MAX_DEPTH_KM = 800.0
_MAGNITUDE_RANGE = (-2.0, 10.5)

#: Chile continental e insular, con holgura. Un sismo fuera de esto no es un
#: sismo chileno mal ubicado: es una celda mal leída.
_CHILE_BOUNDS = {"lat": (-60.0, -15.0), "lon": (-115.0, -65.0)}


@dataclass(frozen=True, slots=True)
class CsnQuake:
    """Un sismo del catálogo, con los tipos ya resueltos."""

    csn_id: str
    #: Instante del sismo en UTC. Siempre con `tzinfo`.
    time: datetime
    lat: float
    lon: float
    depth_km: float | None
    magnitude: float | None
    mag_type: str | None
    place: str | None
    report_url: str | None
    #: De dónde salió la hora: `utc_column` o `local_column`. Queda en
    #: `raw_data` para poder auditar después si alguna vez aparece un desfase.
    time_source: str


def parse_utc_datetime(text: str) -> datetime | None:
    """Lee la columna «Fecha UTC». Ya viene en UTC: sólo se le pone `tzinfo`."""
    match = _TIMESTAMP.search(text or "")
    if not match:
        return None

    crudo = f"{match.group('fecha')} {match.group('hora')}"
    formato = "%Y-%m-%d %H:%M:%S" if crudo.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(crudo, formato).replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_local_datetime(text: str) -> datetime | None:
    """Lee la columna «Fecha Local» y la convierte a UTC. Respaldo, no camino normal.

    Chile cambia la hora dos veces al año: UTC-4 en invierno, UTC-3 en verano.
    Un desplazamiento fijo escrito a mano acierta la mitad del año y desplaza los
    sismos una hora la otra mitad — y ese error de una hora es exactamente el
    tamaño que hace que el motor de correlación empareje un sismo con señales que
    no le corresponden, o lo declare caducado antes de tiempo.

    Por eso se usa `zoneinfo` con `America/Santiago`, que conoce las fechas
    reales de cambio de cada año (Chile las ha movido varias veces por decreto).

    **Las dos horas ambiguas del año.** En el salto de otoño, cuando el reloj
    retrocede, cada hora local ocurre dos veces. `fold=0` elige la primera —la
    anterior al cambio— que es la convención de Python y la lectura correcta
    para un catálogo que se lee en orden cronológico descendente. En el salto de
    primavera hay una hora que no existe; si el CSN publicara un sismo dentro de
    ella, Python lo resuelve desplazándolo, y por eso esta función es el respaldo
    y no el camino principal.
    """
    match = _TIMESTAMP.search(text or "")
    if not match:
        return None

    crudo = f"{match.group('fecha')} {match.group('hora')}"
    formato = "%Y-%m-%d %H:%M:%S" if crudo.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        local = datetime.strptime(crudo, formato)
    except ValueError:
        return None

    return local.replace(tzinfo=CHILE_TZ, fold=0).astimezone(UTC)


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """«-21.074  -69.015» → (lat, lon). None si no hay dos números plausibles.

    El orden es el que publica la página —latitud primero— y se valida contra el
    territorio chileno. Esa validación no está para descartar sismos lejanos: está
    para detectar que se leyó la celda equivocada, que es el modo de fallo real
    de un scraper cuando la tabla cambia de columnas.
    """
    numeros = [float(valor) for valor in _COORD.findall(text or "")]
    if len(numeros) < 2:
        return None

    lat, lon = numeros[0], numeros[1]
    lat_min, lat_max = _CHILE_BOUNDS["lat"]
    lon_min, lon_max = _CHILE_BOUNDS["lon"]
    if not (lat_min <= lat <= lat_max) or not (lon_min <= lon <= lon_max):
        return None
    return (lat, lon)


def parse_depth_km(text: str) -> float | None:
    match = _DEPTH.search(text or "")
    if not match:
        return None
    valor = float(match.group("valor"))
    return valor if 0.0 <= valor <= _MAX_DEPTH_KM else None


def parse_magnitude(text: str) -> tuple[float | None, str | None]:
    """«2.5 Mlv» → (2.5, 'Mlv').

    La escala se conserva porque Ml y Mw **no son comparables**: para un mismo
    sismo grande, la magnitud local satura y la de momento no. Guardar sólo el
    número perdería la información que permite saber si dos cifras distintas son
    un desacuerdo o dos maneras de medir.
    """
    limpio = (text or "").strip()
    if not limpio:
        return (None, None)

    match = _MAGNITUDE.search(limpio)
    if not match:
        return (None, None)

    valor = float(match.group("valor"))
    if not (_MAGNITUDE_RANGE[0] <= valor <= _MAGNITUDE_RANGE[1]):
        return (None, None)

    escala = match.group("escala")
    return (valor, escala if escala else None)


def _cell_text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def parse_report_id(cell: Tag) -> tuple[str | None, str | None]:
    """Identificador y URL del informe, desde el enlace de la primera celda."""
    enlace = cell.find("a", href=True)
    if not isinstance(enlace, Tag):
        return (None, None)

    href = str(enlace["href"]).strip()
    match = _REPORT_ID.search(href)
    return (match.group("id") if match else None, href or None)


def parse_place(cell: Tag) -> str | None:
    """Descripción del lugar: lo que queda de la celda tras quitar la fecha.

    El CSN escribe «2026-08-19 19:33:16  46 km al SO de Mina Collahuasi» en una
    sola celda, con la fecha dentro del enlace. Se retira el texto del enlace y
    lo que sobra es el topónimo.
    """
    texto = _cell_text(cell)
    enlace = cell.find("a")
    if isinstance(enlace, Tag):
        texto = texto.replace(" ".join(enlace.get_text(" ", strip=True).split()), "")
    limpio = " ".join(texto.split())
    return limpio or None


def parse_row(row: Tag, *, base_url: str = "") -> CsnQuake | None:
    """Una `<tr>` → `CsnQuake`. None si la fila no es un sismo.

    Devolver None en vez de lanzar es deliberado: la tabla trae cabeceras y
    separadores, y una fila ilegible entre treinta buenas no puede costar la
    corrida entera.

    **La hora sale de la columna UTC.** El CSN publica ambas —local y UTC— y
    tomar la que ya está en UTC elimina de raíz la conversión de zona horaria:
    no hay horario de verano que resolver, no hay hora ambigua en el cambio de
    reloj, no hay decreto chileno que pueda mover una fecha bajo nuestros pies.
    La columna local queda como respaldo por si el CSN cambia el formato, y
    `time_source` deja registrado cuál se usó.
    """
    cells = row.find_all(["td", "th"])
    if len(cells) < _MIN_CELLS:
        return None

    coords = parse_coordinates(_cell_text(cells[2]))
    if coords is None:
        return None

    csn_id, href = parse_report_id(cells[0])
    if not csn_id:
        # Sin identificador estable no hay idempotencia posible: releer la página
        # cada cinco minutos crearía un sismo nuevo cada vez.
        return None

    tiempo = parse_utc_datetime(_cell_text(cells[1]))
    time_source = "utc_column"
    if tiempo is None:
        tiempo = parse_local_datetime(_cell_text(cells[0]))
        time_source = "local_column"
    if tiempo is None:
        return None

    magnitude, mag_type = parse_magnitude(_cell_text(cells[4]))
    lat, lon = coords

    return CsnQuake(
        csn_id=csn_id,
        time=tiempo,
        lat=lat,
        lon=lon,
        depth_km=parse_depth_km(_cell_text(cells[3])),
        magnitude=magnitude,
        mag_type=mag_type,
        place=parse_place(cells[0]),
        report_url=_absolute(href, base_url),
        time_source=time_source,
    )


def _absolute(href: str | None, base_url: str) -> str | None:
    if not href:
        return None
    if href.startswith(("http://", "https://")):
        return href
    if not base_url:
        return href
    return f"{base_url.rstrip('/')}/{href.lstrip('/')}"


def parse_catalog(html: str, *, base_url: str = "") -> list[CsnQuake]:
    """Todos los sismos de una página del catálogo. Función pura."""
    soup = BeautifulSoup(html, _html_parser())
    sismos: list[CsnQuake] = []
    vistos: set[str] = set()

    for row in soup.find_all("tr"):
        sismo = parse_row(row, base_url=base_url)
        if sismo is None or sismo.csn_id in vistos:
            continue
        vistos.add(sismo.csn_id)
        sismos.append(sismo)

    return sismos


def page_looks_broken(html: str) -> tuple[bool, str | None]:
    """¿La página cambió de estructura? Devuelve `(rota, motivo)`.

    Separa lo que un `len(sismos) == 0` confunde: un día sin sismos publicados
    —posible, aunque raro en Chile— de una maquetación que dejó ciego al parser.
    Se mira si hay tabla y si tiene filas; que ninguna fila sea un sismo es
    información legítima, que no haya tabla es una alarma.
    """
    soup = BeautifulSoup(html, _html_parser())
    if not soup.find("table"):
        return (True, "la respuesta no contiene ninguna tabla")

    filas = soup.find_all("tr")
    if len(filas) < 2:
        return (True, f"la tabla tiene {len(filas)} filas; se esperaban cabecera y datos")
    return (False, None)


def _html_parser() -> str:
    """`lxml` si está; si no, el parser de la stdlib. Mismo criterio que el MTT."""
    try:
        import lxml  # noqa: F401
    except ImportError:  # pragma: no cover — lxml está en requirements-prod
        return "html.parser"
    return "lxml"


def catalog_path(day: datetime) -> str:
    """Ruta relativa del catálogo de un día: `sismicidad/catalogo/AAAA/MM/AAAAMMDD.html`.

    Se construye con la fecha **en hora chilena**, no UTC, porque el CSN organiza
    sus páginas por día local. Pedir el catálogo de "hoy UTC" a las 22:00 de
    Chile traería el del día siguiente, que a esa hora aún no existe.
    """
    local = day.astimezone(CHILE_TZ)
    return (
        f"sismicidad/catalogo/{local:%Y}/{local:%m}/{local:%Y%m%d}.html"
    )


def recent_catalog_paths(now: datetime, *, days: int = 2) -> list[str]:
    """Rutas de los últimos `days` días, del más reciente al más antiguo.

    Se leen dos días por defecto y no uno: a las 00:30 de Chile el catálogo del
    día nuevo tiene una o dos filas, y todo lo de las últimas horas está en el
    del día anterior. Con un solo día, cada medianoche el sistema perdería de
    vista los sismos recientes durante un rato.
    """
    local = now.astimezone(CHILE_TZ)
    return [catalog_path(local - timedelta(days=offset)) for offset in range(days)]


__all__ = [
    "CHILE_TZ",
    "CsnQuake",
    "catalog_path",
    "page_looks_broken",
    "parse_catalog",
    "parse_coordinates",
    "parse_depth_km",
    "parse_local_datetime",
    "parse_magnitude",
    "parse_row",
    "parse_utc_datetime",
    "recent_catalog_paths",
]
