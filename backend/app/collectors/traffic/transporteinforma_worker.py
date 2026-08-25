"""Transporte Informa (MTT) — avisos en texto libre, georreferenciados en dos pasos.

El problema que resuelve
------------------------
El MTT publica en prosa: *"Accidente vehicular en Av. España con Uno Norte, Viña
del Mar. Tránsito lento hacia el poniente."* Es la fuente oficial más rápida
sobre accidentes en carretera y no entrega ni una coordenada.

Ninguna de las dos soluciones obvias funciona:

* **Expresiones regulares.** El texto no tiene formato fijo. "Av. España con Uno
  Norte", "cruce de Av. España y Uno Norte", "Ruta 68 a la altura del km 42" y
  "acceso sur a Viña" son todas la misma clase de dato escrita de cuatro maneras.
  Una regex que las cubra todas se vuelve inmantenible en un mes.
* **Mandarle el texto crudo a Nominatim.** El geocodificador espera una
  dirección, no una oración. "Accidente vehicular en Av. España con Uno Norte,
  Viña del Mar" no resuelve; "Av. España y Uno Norte, Viña del Mar" sí.

El enfoque híbrido
------------------
**Paso 0 — clasificación (`looks_like_accident`).** Reglas deterministas deciden
si el aviso describe un siniestro. Va antes del modelo y no después, por dos
motivos: el MTT publica cortes programados y desvíos que no son accidentes, y
cada llamada al LLM se paga.

**Paso A — extracción (`extract_streets_via_llm`).** Gemini convierte prosa en
`{street_1, street_2, city}`. Es exactamente la tarea para la que sirve un modelo
de lenguaje —comprensión de texto no estructurado— y exactamente donde NO debe
tomar decisiones: no inventa coordenadas, no juzga gravedad y no decide si hubo
accidente. Sólo extrae.

**Paso B — geocodificación (`app.collectors.nominatim`).** El diccionario limpio
se convierte en una consulta que Nominatim sí entiende, con el rate limit de
1 req/s respetado por un limitador global al proceso.

La división importa porque acota el daño de un error del modelo: lo peor que
puede hacer es devolver una calle mal leída, y eso produce una geocodificación
fallida o un punto discutible —marcado como tal en `raw_data._geocoding`— pero
jamás un accidente inventado ni un punto sin trazabilidad.

Modelo y respaldo
-----------------
La llamada vive en `app.collectors.traffic.gemini` y es **asíncrona nativa**: no
bloquea el event loop, que en producción comparte con el motor de correlación
(ver `app/workers.py`).

Si falta `GEMINI_API_KEY`, o si el modelo falla o alucina, se cae a
`extract_streets_heuristic`: reglas que resuelven la estructura "en <calle> con
<calle>, <ciudad>". No es tan buena, pero media capa funcionando es mejor que
ninguna, y sirve de línea base para medir al modelo sobre datos reales. Qué
camino produjo cada señal queda en `raw_data._extraction.mode`.

Confianza
---------
0.80: un organismo del Estado informando por su canal oficial. Ese número
califica **el hecho**, no el punto. El error de la geocodificación es un eje
aparte y queda en `raw_data._geocoding` (`importance`, `display_name`, consulta
usada) para que un operador pueda mirar el punto y desconfiar de él sin
desconfiar del aviso.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.collectors.base import BaseCollector
from app.collectors.geoservices import normalise_text, parse_timestamp, request_text
from app.collectors.nominatim import GeocodeResult, build_client, geocode
from app.collectors.traffic import gemini
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Canal oficial del MTT. Ver el docstring: califica el hecho, no el punto.
TRANSPORTE_INFORMA_CONFIDENCE = 0.80


#: Palabras que denotan un siniestro vial. Se comparan sin tildes.
_ACCIDENT_MARKERS: tuple[str, ...] = (
    "accidente",
    "colision",
    "choque",
    "volcamiento",
    "atropello",
    "siniestro",
)

#: Conectores de intersección tal como los escribe el MTT.
_INTERSECTION_SPLIT = re.compile(
    r"\s+(?:con|esquina(?:\s+de)?|/|y)\s+", re.IGNORECASE
)

#: Preposición que introduce el lugar dentro de la oración.
_PLACE_LEAD = re.compile(
    r"\b(?:en|sobre|a la altura de|frente a)\s+(?P<place>.+)", re.IGNORECASE
)

#: Corta el lugar cuando empieza la parte narrativa del aviso.
_PLACE_STOP = re.compile(
    r"\s*[.;]|\s+(?:transito|tránsito|se recomienda|precaucion|precaución|"
    r"personal|carabineros|equipos)\b",
    re.IGNORECASE,
)

#: Abreviaturas que llevan punto y NO terminan la oración. Sin protegerlas,
#: `_PLACE_STOP` corta "Av. España" en "Av" — y como casi toda calle chilena se
#: escribe abreviada, el extractor devolvería basura para la mayoría de los
#: avisos. El punto se sustituye por un centinela antes de buscar el final de la
#: frase y se restaura después.
_ABBREVIATIONS: tuple[str, ...] = (
    "av",
    "avda",
    "gral",
    "pdte",
    "pje",
    "ptje",
    "sta",
    "sto",
    "dr",
    "cnel",
    "cap",
    "km",
    "psje",
)
_ABBREV_SENTINEL = "\x00"
_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(_ABBREVIATIONS) + r")\.", re.IGNORECASE
)


def _protect_abbreviations(text: str) -> str:
    return _ABBREV_PATTERN.sub(lambda m: f"{m.group(1)}{_ABBREV_SENTINEL}", text)


def _restore_abbreviations(value: str | None) -> str | None:
    if value is None:
        return None
    restored = value.replace(_ABBREV_SENTINEL, ".").strip(" ,")
    return restored or None

#: Comunas de la V Región. Sirven para separar ciudad de calle cuando el aviso
#: las mezcla en la misma frase. No pretende ser exhaustiva: es la lista de las
#: que aparecen en los avisos de tránsito.
_KNOWN_CITIES: tuple[str, ...] = (
    "valparaiso",
    "vina del mar",
    "quilpue",
    "villa alemana",
    "concon",
    "casablanca",
    "quillota",
    "la calera",
    "san antonio",
    "cartagena",
    "el quisco",
    "algarrobo",
    "limache",
    "olmue",
    "los andes",
    "san felipe",
    "la ligua",
    "petorca",
    "quintero",
    "puchuncavi",
)

_DEFAULT_REGION = "Región de Valparaíso"


@dataclass(frozen=True, slots=True)
class TrafficNotice:
    """Un aviso del MTT, tal como llega."""

    notice_id: str
    text: str
    published_at: datetime | None
    raw: Mapping[str, Any] = field(default_factory=dict)


# --- Paso A: extracción del lugar -------------------------------------------


def looks_like_accident(text: str) -> bool:
    """¿El aviso describe un siniestro vial?

    **Esta decisión NO la toma el modelo, y es deliberado.** El contrato de
    salida de Gemini son tres campos —dos calles y una ciudad— sin ningún juicio
    sobre la naturaleza del hecho. La clasificación se queda acá, en reglas
    deterministas y auditables.

    El motivo es la asimetría del daño. Si el modelo se equivoca extrayendo una
    calle, el resultado es una geocodificación fallida o un punto discutible:
    visible, marcado en `raw_data._geocoding`, corregible. Si se le permitiera
    decidir "esto es un accidente", podría inventar un siniestro que nadie
    reportó, y eso llegaría al mapa como un hecho con la confianza 0.80 de una
    fuente oficial detrás. Se le da al modelo la tarea donde sus errores son
    baratos.

    Es también el filtro que separa las palabras clave del scraper —que incluyen
    "Restricción" y "Tránsito suspendido", avisos de tránsito que NO son
    siniestros— de lo que entra a la capa de accidentes.
    """
    return any(marker in normalise_text(text or "") for marker in _ACCIDENT_MARKERS)


async def extract_streets_via_llm(text: str) -> dict[str, Any] | None:
    """Prosa → `{street_1, street_2, city}`. None si no se pudo extraer.

    Llama a Gemini de forma asíncrona (ver `app.collectors.traffic.gemini`). Si
    no hay `GEMINI_API_KEY`, o si el modelo falla o alucina, cae a la heurística
    de reglas en vez de perder el aviso.

    Ese respaldo es una decisión, no una comodidad: la alternativa era que una
    clave sin provisionar apagara la mitad de la capa de accidentes en silencio.
    La heurística resuelve los avisos con estructura "en <calle> con <calle>,
    <ciudad>" —que son la mayoría— y falla devolviendo None con los tramos de
    ruta por kilómetro, que es el fallo correcto.

    Qué camino se usó queda en `raw_data._extraction.mode` de cada señal, para
    poder medir uno contra otro sobre datos reales.
    """
    payload = " ".join(str(text or "").split())
    if not payload:
        return None

    if gemini.is_configured():
        streets = await gemini.extract_streets(payload)
        if streets is not None:
            return streets
        logger.debug("Gemini no resolvió el aviso; se intenta con la heurística")

    return extract_streets_heuristic(payload)


def extract_streets_heuristic(text: str) -> dict[str, Any] | None:
    """Extracción por reglas. Respaldo y línea base contra la que medir el modelo.

    Resuelve la estructura "en <calle> con <calle>, <ciudad>", que cubre la
    mayoría de los avisos del MTT. Devuelve None cuando no reconoce una vía —el
    fallo correcto: una calle inventada geocodifica a un punto plausible y falso,
    peor que no tener ubicación.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return None

    # A partir de acá se trabaja con los puntos de las abreviaturas neutralizados;
    # se restauran al construir el resultado.
    protected = _protect_abbreviations(cleaned)

    place_match = _PLACE_LEAD.search(protected)
    if not place_match:
        return None

    place = place_match.group("place")
    stop = _PLACE_STOP.search(place)
    if stop:
        place = place[: stop.start()]
    place = place.strip(" ,.")

    # La ciudad es el último segmento separado por coma si coincide con una
    # comuna conocida. Sin ese cotejo, "Av. España, sentido poniente" dejaría
    # "sentido poniente" como ciudad y Nominatim buscaría una localidad que no
    # existe.
    city: str | None = None
    segments = [segment.strip() for segment in place.split(",") if segment.strip()]
    if len(segments) > 1 and normalise_text(segments[-1]) in _KNOWN_CITIES:
        city = segments[-1]
        segments = segments[:-1]

    street_part = ", ".join(segments).strip()
    if not street_part:
        return None

    pieces = [piece.strip(" ,.") for piece in _INTERSECTION_SPLIT.split(street_part, 1)]
    street = pieces[0] or None
    cross = pieces[1] if len(pieces) > 1 and pieces[1] else None

    # La ciudad puede venir pegada a la transversal sin coma:
    # "Uno Norte Viña del Mar". Se recorta si el final coincide con una comuna.
    if city is None and cross:
        cross, city = _split_trailing_city(cross)
    if city is None and street:
        street, city = _split_trailing_city(street)

    street_1 = _restore_abbreviations(street)
    if not street_1:
        return None

    return {
        "street_1": street_1,
        "street_2": _restore_abbreviations(cross),
        "city": _restore_abbreviations(city),
    }


def _split_trailing_city(value: str) -> tuple[str | None, str | None]:
    """Separa una comuna pegada al final de un nombre de calle."""
    normalised = normalise_text(value)
    for city in sorted(_KNOWN_CITIES, key=len, reverse=True):
        if normalised.endswith(city) and normalised != city:
            cut = len(value) - len(city)
            head = value[:cut].strip(" ,.")
            return (head or None, value[cut:].strip(" ,."))
    return (value, None)


# --- Ingesta -----------------------------------------------------------------


def parse_notice(payload: Any) -> TrafficNotice | None:
    """Normaliza un elemento estructurado. None si no trae texto aprovechable.

    Se conserva aunque el portal se lea como HTML: si el MTT publica alguna vez
    un JSON o un RSS, el mapeo ya está y sólo cambia de dónde salen los dicts.
    """
    if not isinstance(payload, Mapping):
        return None

    text = ""
    for key in ("text", "titulo", "title", "descripcion", "description", "mensaje"):
        value = payload.get(key)
        if value and str(value).strip():
            text = str(value).strip()
            break
    if not text:
        return None

    notice_id = ""
    for key in ("id", "uuid", "guid", "link", "url"):
        value = payload.get(key)
        if value and str(value).strip():
            notice_id = str(value).strip()
            break

    published_at = None
    for key in ("published_at", "fecha", "date", "pubDate", "created_at"):
        if payload.get(key) is not None:
            published_at = parse_timestamp(payload.get(key))
            if published_at is not None:
                break

    if not notice_id:
        notice_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]

    return TrafficNotice(
        notice_id=notice_id, text=text, published_at=published_at, raw=dict(payload)
    )


# --- Scraping del portal -----------------------------------------------------
#
# transporteinforma.cl es un WordPress con Elementor. No hay API, no hay RSS y
# no hay clases semánticas: los avisos viven en `<article>` o, más
# frecuentemente, en `<div class="elementor-widget-container">`, que es un
# contenedor de maquetación usado para absolutamente todo — menús, pies de
# página, banners.
#
# Por eso la selección tiene dos etapas y no una:
#
#   1. **Estructura**: se recogen los bloques candidatos por etiqueta y clase.
#      Es una red amplia y trae mucha basura.
#   2. **Contenido**: sobrevive el bloque cuyo texto contiene una palabra clave
#      de tránsito. Es lo que separa un aviso de emergencia del menú de
#      navegación, y es mucho más estable frente a rediseños que cualquier
#      selector CSS: Elementor renumera sus clases en cada edición de la página,
#      pero "Accidente" seguirá diciéndose "Accidente".
#
# La tercera etapa —decidir si el aviso es un *siniestro*— NO es de este bloque.
# "Restricción vehicular" y "Tránsito suspendido" son palabras clave válidas
# porque marcan un aviso de tránsito, pero no son accidentes; esa distinción la
# hace `extract_streets_via_llm` con `is_accident`.

#: Selectores donde el portal publica sus avisos, en orden de especificidad.
#:
#: `div.card--state` es el del portal **actual** (rediseño de 2026) y el único
#: que devuelve algo hoy: cada tarjeta es un aviso con ubicación, categoría
#: —`Incidentes` o `Trabajos`—, fecha, descripción y enlace al detalle.
#:
#: Los dos siguientes son del portal anterior, construido con Elementor. Se
#: conservan a propósito aunque hoy no encuentren nada: el rediseño llegó
#: primero a Valparaíso y otras regiones pueden seguir con la maqueta vieja, y
#: un selector que no empareja no cuesta nada. Si dentro de un año siguen sin
#: usarse, se borran.
_BLOCK_SELECTORS: tuple[str, ...] = (
    "div.card--state",
    "article",
    "div.elementor-widget-container",
    # Respaldo: si Elementor desaparece en un rediseño, los avisos casi siempre
    # quedan en algún contenedor con estas clases genéricas de WordPress.
    "div.entry-content",
    "div.post-content",
)

#: Iconos tipográficos que **contaminan el texto** si se extrae a lo bruto.
#:
#: El portal usa Material Symbols, que funcionan por ligadura: el nombre del
#: icono va como texto dentro de la etiqueta y la fuente lo dibuja. En pantalla
#: se ve un pin; en el HTML dice `<i class="material-symbols-rounded">location_on</i>`,
#: y un `get_text()` normal produce «location_on Av. España» en vez de
#: «Av. España».
#:
#: No es cosmético. Ese texto es lo que se manda al LLM para extraer calles y lo
#: que se guarda en `raw_data`: un nombre de icono metido en medio de una
#: dirección empeora la geocodificación y queda archivado como si la fuente lo
#: hubiera escrito.
_ICON_CLASS_PREFIX = "material-"

#: Palabras que delatan un aviso de tránsito. Se comparan sin tildes y en
#: minúsculas, así que "Precaución" y "PRECAUCION" entran igual.
TRAFFIC_KEYWORDS: tuple[str, ...] = (
    "precaucion",
    "accidente",
    "colision",
    "restriccion",
    "transito suspendido",
    "volcamiento",
    "atropello",
    # El portal nuevo **etiqueta** cada tarjeta con `Categoría: Incidentes` o
    # `Categoría: Trabajos`. Es la propia fuente diciendo de qué habla, y eso
    # vale más que adivinar por vocabulario: entra en singular porque la
    # comparación es por subcadena y así cubre las dos formas.
    #
    # No convierte esto en un filtro de siniestros —un "incidente" puede ser un
    # paso fronterizo habilitado— y no pretende serlo: sigue siendo la etapa 2,
    # la que separa un aviso de tránsito del menú de navegación. Quien decide si
    # es un accidente es `is_accident`, más adelante.
    "incidente",
)

#: Un bloque más corto que esto es una etiqueta suelta ("Accidente") sin
#: información; más largo, es la página entera capturada por un contenedor
#: padre. Ninguno de los dos es un aviso.
_MIN_BLOCK_CHARS = 25
_MAX_BLOCK_CHARS = 1200


def _es_icono(tag: Any) -> bool:
    """¿Esta etiqueta es un icono tipográfico? Ver `_ICON_CLASS_PREFIX`."""
    clases = getattr(tag, "get", lambda *_: None)("class") or ()
    return any(str(clase).startswith(_ICON_CLASS_PREFIX) for clase in clases)


def _block_text(node: Tag) -> str:
    """Texto **visible** de un nodo, con los espacios normalizados.

    Visible de verdad: se saltan las ligaduras de los iconos, que un
    `get_text()` normal incluiría. Ver `_ICON_CLASS_PREFIX` para por qué eso
    importa más de lo que parece.

    Se recorren las cadenas en vez de eliminar los `<i>` del árbol porque el
    árbol es compartido —el mismo `soup` alimenta varias pasadas— y arrancarle
    nodos a mitad de camino haría que el resultado dependiera del orden en que
    se llame a esta función.
    """
    partes = [
        str(cadena)
        for cadena in node.find_all(string=True)
        if not _es_icono(cadena.parent)
    ]
    return " ".join(" ".join(partes).split())


def matched_keywords(text: str) -> list[str]:
    """Palabras clave de tránsito presentes en un texto."""
    haystack = normalise_text(text)
    return [word for word in TRAFFIC_KEYWORDS if word in haystack]


def _candidate_blocks(soup: BeautifulSoup) -> Iterator[Tag]:
    for selector in _BLOCK_SELECTORS:
        yield from soup.select(selector)


def _deduplicate(blocks: Sequence[str]) -> list[str]:
    """Se queda con los bloques más específicos de cada anidamiento.

    Elementor anida contenedores dentro de contenedores: el mismo aviso aparece
    en el `<div>` que lo contiene, en su padre y en el padre de su padre. Sin
    esto, un solo accidente entraría tres veces al sistema y el motor lo leería
    como tres corroboraciones independientes del mismo hecho — inflando su
    confianza con evidencia que es una sola.

    Se ordena de más corto a más largo y se descarta todo bloque que contenga
    íntegramente a otro ya aceptado: el hijo gana, el padre se va.
    """
    kept: list[str] = []
    for text in sorted(set(blocks), key=len):
        if any(inner in text for inner in kept):
            continue
        kept.append(text)
    return kept


def parse_notices(html: str) -> list[TrafficNotice]:
    """Extrae los avisos de tránsito del HTML del portal. Función pura.

    Devuelve lista vacía si la página no trae ningún bloque reconocible. Esa
    ambigüedad —¿no hay avisos o cambió el DOM?— la resuelve `page_looks_broken`,
    que se consulta aparte.
    """
    soup = BeautifulSoup(html, _html_parser())

    texts = [
        text
        for block in _candidate_blocks(soup)
        if _MIN_BLOCK_CHARS <= len(text := _block_text(block)) <= _MAX_BLOCK_CHARS
        and matched_keywords(text)
    ]

    notices: list[TrafficNotice] = []
    for text in _deduplicate(texts):
        notices.append(
            TrafficNotice(
                notice_id=hashlib.sha256(text.encode("utf-8")).hexdigest()[:24],
                text=text,
                published_at=extract_datetime(text),
                raw={"keywords": matched_keywords(text), "origen": "html"},
            )
        )
    return notices


def page_looks_broken(html: str) -> tuple[bool, str | None]:
    """¿La página cambió de estructura? Devuelve `(rota, motivo)`.

    Distingue lo que un `len(notices) == 0` confunde: una jornada sin incidentes
    —normal y silenciosa— de un rediseño que dejó el scraper ciego. Se mira si
    existen los contenedores; que estén vacíos de palabras clave es información
    legítima, que no existan es una alarma.
    """
    soup = BeautifulSoup(html, _html_parser())
    if not soup.find("body"):
        return (True, "la respuesta no parece HTML")

    bloques = sum(1 for _ in _candidate_blocks(soup))
    if bloques == 0:
        return (
            True,
            "no se encontró ningún bloque <article> ni .elementor-widget-container",
        )
    return (False, None)


#: Fecha embebida en el texto del aviso, si la hay. El portal no expone un campo
#: de fecha por aviso: publica la página entera y fecha algunos avisos en prosa.
_NOTICE_DATE = re.compile(
    r"(?P<date>\d{1,2}[-/]\d{1,2}[-/]\d{2,4})(?:[\sT]+(?P<time>\d{1,2}:\d{2}))?"
)


def extract_datetime(text: str) -> datetime | None:
    match = _NOTICE_DATE.search(text)
    if not match:
        return None
    raw = match.group("date")
    if match.group("time"):
        raw = f"{raw} {match.group('time')}"
    return parse_timestamp(raw)


def _html_parser() -> str:
    """`lxml` si está disponible; si no, el parser de la stdlib.

    lxml es notoriamente más indulgente con el HTML roto que produce un CMS con
    plugins, y este portal es exactamente ese caso. Pero degradar a `html.parser`
    es preferible a que el collector no arranque: un scraper que funciona un poco
    peor sigue recolectando; uno que no importa, no.
    """
    try:
        import lxml  # noqa: F401
    except ImportError:  # pragma: no cover — lxml está en requirements-prod
        return "html.parser"
    return "lxml"


class TransporteInformaCollector(BaseCollector):
    """Avisos del MTT: extracción del lugar con LLM y geocodificación con Nominatim.

    Rompe una convención del proyecto y conviene decirlo en voz alta: en el resto
    de los collectors `normalize()` es una función pura y todo el I/O vive en
    `fetch()`. Acá la geocodificación **también ocurre en `fetch()`**, no en
    `normalize()`, precisamente para no romperla. `fetch` devuelve avisos ya
    resueltos a coordenadas y `normalize` sigue siendo pura y testeable sin red.
    """

    name = "transporte_informa"
    source = EventSource.TRANSPORTE_INFORMA
    default_interval_seconds = 600

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.TRANSPORTE_INFORMA_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.TRANSPORTE_INFORMA_URL.strip():
            raise CollectorError(
                "TRANSPORTE_INFORMA_URL no está configurada; el collector no "
                "tiene de dónde leer."
            )
        self.url = settings.TRANSPORTE_INFORMA_URL.strip()
        self.max_geocodes = settings.TRANSPORTE_INFORMA_MAX_GEOCODES
        self.max_llm_calls = settings.GEMINI_MAX_CALLS_PER_RUN

        if not gemini.is_configured():
            # No es fatal: la heurística cubre la mayoría de los avisos. Pero
            # tiene que verse, porque la diferencia de cobertura entre ambos
            # caminos no es despreciable y nadie debería descubrir meses después
            # que el modelo nunca se llamó.
            logger.warning(
                "GEMINI_API_KEY no está configurada: la extracción de calles "
                "usará la heurística de reglas",
                extra={"collector": self.name},
            )

    def run_params(self) -> dict[str, Any]:
        return {
            "max_geocodes": self.max_geocodes,
            "max_llm_calls": self.max_llm_calls,
            "extraction_mode": (
                gemini.MODE_GEMINI if gemini.is_configured() else gemini.MODE_HEURISTIC
            ),
            "gemini_model": settings.GEMINI_MODEL if gemini.is_configured() else None,
            "nominatim_min_interval_s": settings.NOMINATIM_MIN_INTERVAL_SECONDS,
        }

    async def fetch(self) -> Sequence[tuple[TrafficNotice, dict[str, Any], Any]]:
        """Trae los avisos, extrae el lugar y geocodifica los que son accidentes.

        Devuelve tripletas `(aviso, extracción, geocodificación|None)`.

        Todo fallo sale de acá como `CollectorError` y de ninguna otra forma:
        `request_text` ya traduce timeouts, 5xx, DNS y TLS, y los `except
        Exception` cubren lo que no anticipamos al cruzar la frontera con una
        fuente ajena. `BaseCollector.run()` lo registra en `collector_runs` y el
        orquestador no se entera.
        """
        try:
            async with httpx.AsyncClient(
                timeout=settings.TRANSPORTE_INFORMA_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            ) as client:
                html = await request_text(
                    client, self.url, origin="transporte_informa"
                )
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                f"transporte_informa: fallo inesperado al leer el portal: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": self.url},
            ) from exc

        try:
            broken, reason = page_looks_broken(html)
            notices = parse_notices(html)
        except Exception as exc:
            raise CollectorError(
                f"transporte_informa: el HTML no se pudo interpretar: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": self.url, "muestra": html[:200]},
            ) from exc

        if broken:
            # El DOM cambió. Se avisa —la corrida queda `partial` y el motivo
            # viaja a `collector_runs`— y se sigue con lo que haya. Un rediseño
            # necesita a una persona, no un reintento, y el aviso es la forma de
            # convocarla antes de que pasen semanas.
            self.warn(f"la estructura del portal cambió: {reason}")

        resolved: list[tuple[TrafficNotice, dict[str, Any], GeocodeResult | None]] = []
        geocoded = 0
        llm_calls = 0

        # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
        # conexión TLS y, sobre todo, mantiene un único User-Agent identificable,
        # que es parte del contrato de uso del servicio.
        async with build_client() as geo_client:
            for notice in notices:
                # El filtro de siniestro va ANTES del modelo, no después. Dos
                # razones: el MTT publica cortes programados y desvíos que no
                # son accidentes, y cada llamada al LLM se paga — filtrar
                # primero evita gastar tokens en avisos que se van a descartar.
                if not looks_like_accident(notice.text):
                    continue

                if llm_calls >= self.max_llm_calls:
                    self.warn(
                        f"se alcanzó el tope de {self.max_llm_calls} llamadas al "
                        f"modelo por corrida; el resto queda sin ubicación"
                    )
                    streets = None
                else:
                    streets = await extract_streets_via_llm(notice.text)
                    llm_calls += 1

                if streets is None:
                    # Ni el modelo ni la heurística reconocieron una vía. El
                    # aviso entra igual, sin coordenadas: es un accidente que el
                    # MTT informó y descartarlo por no saber dónde sería perder
                    # el hecho por no tener el punto.
                    resolved.append((notice, {}, None))
                    continue

                point: GeocodeResult | None = None
                if geocoded < self.max_geocodes and streets.get("street_1"):
                    try:
                        point = await geocode(geo_client, streets)
                        geocoded += 1
                    except Exception as exc:
                        # Una geocodificación fallida NO pierde el aviso: la señal
                        # entra sin coordenadas. Perder un accidente confirmado por
                        # el MTT porque OpenStreetMap no conoce una esquina sería
                        # el peor intercambio posible — y perder los otros
                        # diecinueve avisos del lote por esa misma esquina sería
                        # todavía peor, que es lo que ocurriría si esta captura
                        # sólo contemplara `CollectorError`.
                        self.warn(
                            f"Nominatim falló para un aviso "
                            f"({type(exc).__name__}): {exc}"
                        )
                        # El contador igual avanza: un servicio que falla consumió
                        # su segundo de rate limit lo mismo que uno que responde.
                        geocoded += 1
                elif geocoded >= self.max_geocodes:
                    self.warn(
                        f"se alcanzó el tope de {self.max_geocodes} geocodificaciones "
                        f"por corrida; el resto queda sin coordenadas"
                    )

                resolved.append((notice, streets, point))

        logger.info(
            "avisos del MTT procesados",
            extra={
                "collector": self.name,
                "avisos": len(notices),
                "accidentes": len(resolved),
                "extracciones_llm": llm_calls,
                "geocodificados": geocoded,
                "modo": gemini.MODE_GEMINI
                if gemini.is_configured()
                else gemini.MODE_HEURISTIC,
            },
        )
        return resolved

    def normalize(
        self, records: Sequence[tuple[TrafficNotice, dict[str, Any], Any]]
    ) -> list[EventCreate]:
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        sin_punto = 0

        for notice, streets, point in records:
            timestamp = notice.published_at or now
            if timestamp > now:
                timestamp = now
            if point is None:
                sin_punto += 1

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.TRANSPORTE_INFORMA,
                    type=EventType.ACCIDENT,
                    lat=point.lat if point else None,
                    lon=point.lon if point else None,
                    text=notice.text[:10_000],
                    external_id=f"mtt:{notice.notice_id}",
                    confidence=TRANSPORTE_INFORMA_CONFIDENCE,
                    raw_data={
                        **dict(notice.raw),
                        "comuna": streets.get("city"),
                        "_collector": self.name,
                        # Los dos pasos quedan separados y auditables: qué leyó el
                        # extractor y qué resolvió el geocodificador. Si mañana un
                        # punto está mal, esto dice cuál de los dos falló.
                        "_extraction": {
                            **streets,
                            "mode": (
                                gemini.MODE_GEMINI
                                if gemini.is_configured()
                                else gemini.MODE_HEURISTIC
                            ),
                        },
                        "_geocoding": point.as_dict() if point else None,
                    },
                )
            )

        if sin_punto:
            self.warn(
                f"{sin_punto} avisos quedaron sin coordenadas; no entran al Paso A "
                f"del motor pero sí quedan registrados"
            )
        return events


__all__ = [
    "TRAFFIC_KEYWORDS",
    "TRANSPORTE_INFORMA_CONFIDENCE",
    "TrafficNotice",
    "TransporteInformaCollector",
    "extract_streets_heuristic",
    "extract_streets_via_llm",
    "looks_like_accident",
    "page_looks_broken",
    "parse_notice",
    "parse_notices",
]
