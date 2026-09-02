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

Dos capas, no una
-----------------
El portal publica dos clases de aviso y este collector emite las dos, con tipos
distintos:

* **`ACCIDENT`** — el siniestro. Correlacionable, familia `traffic`, se une con
  Waze y con los despachos de Bomberos.
* **`ROAD_CLOSURE`** — la capa táctica: desvíos, faenas, cortes y restricciones.
  **NO correlacionable.** No genera incidentes, no mueve confianzas y no se
  agrupa con nada; existe para superponerse en el mapa.

La separación es la pieza que hace segura la ampliación, y el motivo está en la
escala: el radio del Paso A son 1500 m, que es exactamente la distancia a la que
una faena programada y un choque conviven en la misma avenida sin tener nada que
ver. Si lo táctico entrara como `ACCIDENT`, el MTT se estaría corroborando a sí
mismo y un choque subiría de confianza porque hay obras a tres cuadras. Ver
`EventType.ROAD_CLOSURE` en `app/models/enums.py`.

Quién decide cuál es cuál: `vocabulary.clasificar_transito`, con reglas
deterministas y consultando el accidente primero — casi todo choque produce un
desvío, así que el orden inverso archivaría siniestros como faenas.

Cadencia y trato con la fuente
------------------------------
**Una tarea propia dentro del proceso de workers, cada 600 s.** No es un cronjob
aparte y no va acoplado al ciclo de la prensa local; el porqué de las tres
opciones está en el docstring de `app/collectors/runner.py`.

Sobre el riesgo de bloqueo, que es la pregunta real detrás de la cadencia: la
carga que este collector pone sobre el MTT es **un GET por ciclo**, 144 al día
—menos que una persona revisando el portal en el almuerzo—, y ampliarlo a la
capa táctica no la cambió en nada, porque lo que creció es lo que se hace con el
HTML *después* de traerlo. Bajar la frecuencia habría pagado con lo único que
esta fuente aporta —ser el canal oficial más rápido— a cambio de reducir un
volumen que ya era despreciable.

Lo que sí expone a un bloqueo, y es donde se trabajó:

* **El patrón.** Una petición cada 600 s exactos es una firma de bot más
  reconocible que cualquier volumen. La dispersa `COLLECTOR_JITTER_RATIO` en el
  runner, que además desalinea a este collector de la prensa local (600 y 900
  comparten período 1800: coincidían cada media hora).
* **El 429.** Se trataba como un 4xx cualquiera: fallaba al instante y el ciclo
  siguiente volvía a pedir como si nada. Es el comportamiento que convierte un
  aviso recuperable en una IP vetada. Hoy `geoservices.request_response` respeta
  `Retry-After`.
* **La identidad.** UA propio con el nombre del proyecto y un contacto, en vez
  del de Nominatim que usaba antes. A un scraper identificado le piden bajar el
  ritmo; a uno anónimo le bloquean la IP.

El cuello de botella real de la corrida no es el MTT sino **Nominatim**, que es
el servicio genuinamente limitado (1 req/s global, compartido con la prensa
local). Por eso el presupuesto de geocodificación se gasta con los accidentes
primero: ver el bloque de prioridad en `fetch()`.

Confianza
---------
0.80: un organismo del Estado informando por su canal oficial. Ese número
califica **el hecho**, no el punto. El error de la geocodificación es un eje
aparte y queda en `raw_data._geocoding` (`importance`, `display_name`, consulta
usada) para que un operador pueda mirar el punto y desconfiar de él sin
desconfiar del aviso.

La capa táctica lleva **el mismo 0.80**: el MTT es tan autoridad sobre un corte
que él decretó como sobre un choque que le reportaron. Lo que aísla esa capa es
el tipo de evento, no una confianza rebajada — bajarle el número para conseguir
el mismo efecto habría sido decir algo falso sobre la fuente.
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
from app.collectors.vocabulary import (
    ACCIDENT_TERMS,
    ROAD_OPS_TERMS,
    clasificar_transito,
    es_accidente_vial,
    es_operacion_vial,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Canal oficial del MTT. Ver el docstring: califica el hecho, no el punto.
TRANSPORTE_INFORMA_CONFIDENCE = 0.80

#: Confianza de la capa táctica. **El mismo 0.80 que un accidente, y no es un
#: descuido.** La confianza mide cuánto vale la palabra de la fuente sobre el
#: hecho que informa, y el MTT es tan autoridad sobre un corte de calzada que él
#: mismo decretó como sobre un choque que le reportaron — más, en rigor.
#:
#: Que un corte de vía no pese en ningún incidente no se resuelve bajándole la
#: confianza: se resuelve manteniéndolo fuera de `CORRELATABLE_EVENT_TYPES`, que
#: es donde ya está. Rebajar el número para conseguir el mismo efecto habría
#: sido decir algo falso sobre la fuente para obtener un comportamiento que el
#: tipo de evento ya garantiza.
ROAD_CLOSURE_CONFIDENCE = 0.80

#: Conectores de intersección tal como los escribe el MTT.
_INTERSECTION_SPLIT = re.compile(
    r"\s+(?:con|esquina(?:\s+de)?|/|y)\s+", re.IGNORECASE
)

#: Preposición que introduce el lugar dentro de la oración.
_PLACE_LEAD = re.compile(
    r"\b(?:en|sobre|a la altura del?|frente a[l]?|cerca de[l]?)\s+(?P<place>.+)",
    re.IGNORECASE,
)

#: Conectores de PUNTO DE REFERENCIA, que no son lo mismo que una intersección.
#:
#: «Av. España **con** Pedro Montt» nombra dos calles que se cruzan y Nominatim
#: resuelve el cruce. «Av. España **a la altura del** nudo Barón» nombra una
#: calle y un punto de referencia, y son cosas distintas: el nudo Barón no es
#: una calle y buscar su intersección con Av. España no devuelve nada.
#:
#: Sin esta distinción el extractor metía la frase entera en `street_1` y
#: producía consultas como «Av. España, a la altura del nudo Barón, Región de
#: Valparaíso», que Nominatim no resuelve. El evento entraba sin coordenadas y
#: —porque `cluster_unassigned_events` filtra por `geom IS NOT NULL`— nunca
#: llegaba al mapa. Así se perdió el accidente de Av. España del 2026-09-02, que
#: quedó guardado y mudo en `raw_events`.
#:
#: Es la forma en que escribe la prensa chilena y las cuentas locales, así que
#: no es un caso de borde: es la mitad del corpus.
_REFERENCE_SPLIT = re.compile(
    r",?\s+(?:a la altura del?|frente a[l]?|cerca de[l]?|"
    r"en el sector del?|sector del?)\s+",
    re.IGNORECASE,
)

#: Corta el lugar cuando empieza la parte narrativa del aviso.
#:
#: `sentido` entra porque «Ruta 68, sentido a Santiago» es una dirección de
#: circulación, no un lugar: dejarla dentro ensucia la consulta a Nominatim con
#: un nombre de ciudad que está a 100 km del hecho.
_PLACE_STOP = re.compile(
    r"\s*[.;]|\s+(?:transito|tránsito|se recomienda|precaucion|precaución|"
    r"personal|carabineros|equipos|sentido)\b",
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


#: Lo que `fetch()` entrega a `normalize()`: el aviso, su tipo ya decidido, lo
#: que el extractor leyó y lo que el geocodificador resolvió.
#:
#: El **tipo viaja en la tupla** en vez de recalcularse en `normalize()`, y esa
#: es una decisión con consecuencia. `fetch()` ya clasificó cada aviso para
#: ordenar la cola de presupuesto; volver a clasificarlo al normalizar
#: significaría correr el mismo léxico dos veces y —lo que importa— dejar abierta
#: la posibilidad de que las dos pasadas discrepen. Un aviso que compitió por el
#: cupo como accidente y se guarda como corte de vía sería un defecto invisible:
#: nada falla, sólo aparece en la capa equivocada.
ResolvedNotice = tuple[
    TrafficNotice, EventType, dict[str, Any], "GeocodeResult | None"
]


# --- Paso A: extracción del lugar -------------------------------------------


def looks_like_accident(text: str) -> bool:
    """¿El aviso describe un siniestro vial?

    Delega en `vocabulary.es_accidente_vial`. Vivió acá, con su propia lista de
    marcadores, hasta que el vocabulario central creció lo suficiente para
    cubrirla: mantener dos listas de palabras de siniestro que se editan por
    separado es exactamente la deuda que `app/collectors/vocabulary.py` existe
    para pagar. La versión local ignoraba "desbarrancamiento" y "vuelco", que la
    compartida sí tiene — y esa divergencia es la forma en que estos defectos se
    manifiestan: no como un error, como avisos que dejan de verse.

    Se conserva el nombre porque es el que usan los tests y porque nombra bien lo
    que hace desde la perspectiva de este collector. La decisión de fondo —que la
    clasificación es determinista y NO la toma el modelo— está documentada en
    `vocabulary.es_accidente_vial`.
    """
    return es_accidente_vial(text or "")


def looks_like_road_ops(text: str) -> bool:
    """¿El aviso informa una intervención de la vía (desvío, faena, corte)?

    La contraparte táctica de `looks_like_accident`. Ver
    `vocabulary.es_operacion_vial`.
    """
    return es_operacion_vial(text or "")


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
        # La condición mira `street_1`, NO `is not None`, y esa diferencia costó
        # un accidente.
        #
        # El modelo tiene tres desenlaces, no dos: resuelve, revienta, o
        # **responde bien y no encuentra calle** —un `{}` o un `street_1` vacío,
        # que es una respuesta válida—. El tercero se colaba por el primero: como
        # el diccionario no era `None`, se devolvía tal cual y la heurística no
        # llegaba a correr nunca. Aguas abajo, `geocode_text` ve que no hay
        # `street_1`, devuelve `({}, None)`, y el evento entra sin coordenadas
        # para no volver a salir: `cluster_unassigned_events` filtra por
        # `geom IS NOT NULL`.
        #
        # Es el mismo patrón del `feed_is_broken` de Bomberos: dos categorías
        # donde había tres, y la tercera colándose por la que no le corresponde.
        if streets and str(streets.get("street_1") or "").strip():
            # Se normaliza la forma, no el contenido. Los dos caminos tienen que
            # entregar las MISMAS claves o el consumidor acabaría preguntando
            # cuál corrió, que es justo lo que este adaptador existe para
            # evitar. `reference` la produce sólo la heurística —el esquema que
            # se le pide al modelo no la contempla— así que se rellena en nulo
            # en vez de faltar.
            return {**streets, "reference": streets.get("reference")}
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

    # El punto de referencia se separa ANTES que la intersección: «Av. España, a
    # la altura del nudo Barón y Pedro Montt» tiene las dos formas, y la
    # referencia es la que delimita dónde termina la calle principal.
    reference: str | None = None
    ref_pieces = [p.strip(" ,.") for p in _REFERENCE_SPLIT.split(street_part, 1)]
    if len(ref_pieces) > 1:
        street_part = ref_pieces[0]
        reference = ref_pieces[1] or None

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

    if city is None and reference:
        reference, city = _split_trailing_city(reference)

    return {
        "street_1": street_1,
        "street_2": _restore_abbreviations(cross),
        "city": _restore_abbreviations(city),
        # `reference` va SEPARADA de `street_2` y `build_query` no la usa.
        #
        # Un punto de referencia no es una calle, así que meterlo en la consulta
        # como si lo fuera —«Av. España y nudo Barón»— hace que Nominatim busque
        # una intersección inexistente y no devuelva nada. Sin él, «Av. España,
        # Valparaíso» resuelve limpio.
        #
        # Se conserva igual porque es lo que la fuente dijo y porque el día que
        # un punto esté mal, esto es lo que permite distinguir «leímos mal la
        # calle» de «Nominatim la resolvió a otra cuadra». Va a
        # `raw_data._extraction`, junto al resto del Paso A.
        "reference": _restore_abbreviations(reference),
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

#: Marcadores propios de la MAQUETA del portal, no del idioma.
#:
#: El portal nuevo **etiqueta** cada tarjeta con `Categoría: Incidentes` o
#: `Categoría: Trabajos`: es la propia fuente diciendo de qué habla, y eso vale
#: más que adivinar por vocabulario. Entran en singular porque la comparación es
#: por subcadena y así cubren las dos formas.
#:
#: Viven acá y NO en `vocabulary.py` por la regla de ese archivo: allá va lo que
#: significan las palabras, acá lo que sabe este portal. "Categoría: Trabajos"
#: no quiere decir nada en un titular de diario.
_PORTAL_MARKERS: tuple[str, ...] = (
    "incidente",
    "trabajos",
    "precaucion",
)

#: Palabras que delatan un aviso de tránsito. Se comparan sin tildes y en
#: minúsculas, así que "Precaución" y "PRECAUCION" entran igual.
#:
#: Es la **red gruesa**: separa un aviso de tránsito del menú de navegación y del
#: pie de página. NO decide si hay un siniestro ni si hay un corte — de eso se
#: encarga `clasificar_transito` más adelante, y por eso acá conviene pasarse de
#: ancho antes que quedarse corto.
#:
#: Se deriva del vocabulario compartido en vez de escribirse a mano. Antes era
#: una tupla literal de siete palabras y le faltaban la mitad de los términos de
#: cierre que el propio worker necesitaba reconocer: sin "desvío" ni "faena" en
#: la red gruesa, un aviso de desvío no llegaba siquiera a ser un bloque
#: candidato, así que ninguna clasificación posterior podía rescatarlo.
TRAFFIC_KEYWORDS: tuple[str, ...] = tuple(
    sorted(set(ACCIDENT_TERMS) | set(ROAD_OPS_TERMS) | set(_PORTAL_MARKERS))
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

    async def fetch(self) -> Sequence[ResolvedNotice]:
        """Trae los avisos, los clasifica, extrae el lugar y geocodifica.

        Devuelve cuádruplas `(aviso, tipo, extracción, geocodificación|None)`.

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
                # UA propio, no el de Nominatim. El anterior estaba a mano y era
                # falso: le decía al portal del Ministerio que quien lo visitaba
                # era el cliente de OpenStreetMap. Ver
                # `TRANSPORTE_INFORMA_USER_AGENT` en `core/config.py`.
                headers={"User-Agent": settings.TRANSPORTE_INFORMA_USER_AGENT},
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

        resolved: list[ResolvedNotice] = []
        geocoded = 0
        llm_calls = 0

        # -- Clasificación y orden de prioridad -------------------------------
        #
        # El filtro va ANTES del modelo, no después. Dos razones: el portal
        # publica bastante que no es ni siniestro ni intervención —recorridos de
        # micros, horarios de terminal, pasos fronterizos habilitados— y cada
        # llamada al LLM se paga. Filtrar primero evita gastar tokens en avisos
        # que se van a descartar igual.
        #
        # El **orden** es la pieza nueva y la que hay que entender. Los dos
        # presupuestos de la corrida son escasos y compartidos:
        # `GEMINI_MAX_CALLS_PER_RUN` y `TRANSPORTE_INFORMA_MAX_GEOCODES`. Antes
        # sólo competían accidentes entre sí; ahora los avisos tácticos
        # compiten por los mismos cupos, y en un día de temporal —cuando más
        # importa— hay muchos más cortes que choques.
        #
        # Recorrer los avisos en el orden del DOM dejaría que catorce faenas
        # programadas se comieran el presupuesto y que el único accidente de la
        # página quedara sin coordenadas. Sería la peor forma de gastarlo: un
        # corte sin punto pierde una capa de contexto; un accidente sin punto no
        # entra al Paso A del motor y deja de poder corroborarse con Waze o con
        # Bomberos, que es la razón entera por la que esta fuente existe.
        #
        # `sort` estable: dentro de cada clase se conserva el orden del portal,
        # que es cronológico descendente.
        clasificados: list[tuple[TrafficNotice, EventType]] = [
            (notice, tipo)
            for notice in notices
            if (tipo := clasificar_transito(notice.text)) is not None
        ]
        clasificados.sort(key=lambda par: par[1] is not EventType.ACCIDENT)

        # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
        # conexión TLS y, sobre todo, mantiene un único User-Agent identificable,
        # que es parte del contrato de uso del servicio.
        async with build_client() as geo_client:
            for notice, tipo in clasificados:
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
                    # aviso entra igual, sin coordenadas: es un hecho que el MTT
                    # informó y descartarlo por no saber dónde sería perder el
                    # hecho por no tener el punto.
                    resolved.append((notice, tipo, {}, None))
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

                resolved.append((notice, tipo, streets, point))

        accidentes = sum(1 for _, tipo, _, _ in resolved if tipo is EventType.ACCIDENT)

        # Qué descartó el pre-filtro, y no sólo cuántos.
        #
        # `avisos: 4 · retenidos: 0` es una línea ambigua: puede ser el MTT
        # publicando cuatro faenas programadas —correcto, y el silencio es la
        # respuesta correcta— o `clasificar_transito` habiéndose quedado corto de
        # vocabulario. Las dos se ven idénticas en producción, y el portal ya se
        # rediseñó una vez.
        #
        # Con una muestra del texto la pregunta se contesta leyendo el log en vez
        # de reproduciendo el raspado. Va en INFO y no en WARNING a propósito:
        # descartar avisos irrelevantes es el trabajo del filtro haciéndose bien,
        # no una degradación. Lo que se busca es poder auditarlo, no que grite.
        clasificados_ids = {id(notice) for notice, _ in clasificados}
        descartados = [n for n in notices if id(n) not in clasificados_ids]

        logger.info(
            "avisos del MTT procesados",
            extra={
                "collector": self.name,
                "avisos": len(notices),
                "retenidos": len(resolved),
                "accidentes": accidentes,
                "cortes_de_via": len(resolved) - accidentes,
                "extracciones_llm": llm_calls,
                "geocodificados": geocoded,
                "descartados": len(descartados),
                # Recortada dos veces: cinco avisos y 120 caracteres cada uno. Es
                # una muestra para decidir si el filtro está bien calibrado, no
                # un volcado del portal en el log de producción.
                "descartados_muestra": [
                    " ".join(n.text.split())[:120] for n in descartados[:5]
                ],
                "modo": gemini.MODE_GEMINI
                if gemini.is_configured()
                else gemini.MODE_HEURISTIC,
            },
        )
        return resolved

    def normalize(self, records: Sequence[ResolvedNotice]) -> list[EventCreate]:
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        sin_punto = 0

        for notice, tipo, streets, point in records:
            timestamp = notice.published_at or now
            if timestamp > now:
                timestamp = now
            # Sólo se cuentan los accidentes sin punto. Un corte de vía sin
            # coordenadas no pierde nada: no entra al motor de ninguna manera,
            # porque `road_closure` está fuera de `CORRELATABLE_EVENT_TYPES`.
            # Contarlo inflaría un aviso que describe un problema que no tiene.
            if point is None and tipo is EventType.ACCIDENT:
                sin_punto += 1

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.TRANSPORTE_INFORMA,
                    type=tipo,
                    lat=point.lat if point else None,
                    lon=point.lon if point else None,
                    text=notice.text[:10_000],
                    # **El prefijo de los accidentes NO cambia, y eso no es
                    # pereza.** `external_id` es la clave de idempotencia: la
                    # tentación obvia acá era uniformar a `mtt:<tipo>:<hash>`,
                    # que se lee mejor y habría reinsertado en la primera corrida
                    # tras el despliegue **todos** los accidentes que el sistema
                    # ya conocía, con identificador nuevo. Duplicados que además
                    # se corroboran entre sí en el motor, porque son idénticos en
                    # texto, tiempo y lugar: un choque con la confianza inflada
                    # por su propio fantasma.
                    #
                    # La capa táctica es nueva y no tiene historia que preservar,
                    # así que lleva su prefijo propio. La asimetría es el precio
                    # de no romper lo que ya está escrito en la base.
                    external_id=(
                        f"mtt:{notice.notice_id}"
                        if tipo is EventType.ACCIDENT
                        else f"mtt:closure:{notice.notice_id}"
                    ),
                    confidence=(
                        TRANSPORTE_INFORMA_CONFIDENCE
                        if tipo is EventType.ACCIDENT
                        else ROAD_CLOSURE_CONFIDENCE
                    ),
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
                f"{sin_punto} accidentes quedaron sin coordenadas; no entran al "
                f"Paso A del motor pero sí quedan registrados"
            )
        return events


__all__ = [
    "ROAD_CLOSURE_CONFIDENCE",
    "TRAFFIC_KEYWORDS",
    "TRANSPORTE_INFORMA_CONFIDENCE",
    "ResolvedNotice",
    "TrafficNotice",
    "TransporteInformaCollector",
    "extract_streets_heuristic",
    "extract_streets_via_llm",
    "looks_like_accident",
    "looks_like_road_ops",
    "page_looks_broken",
    "parse_notice",
    "parse_notices",
]
