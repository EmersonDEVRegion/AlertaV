"""Cuentas hiperlocales de Instagram, leídas a través de Apify.

Por qué Apify y no `httpx` contra Meta
--------------------------------------
Ya se probó: el WAF de Meta bloquea al segundo o tercer intento desde una IP de
datacenter, y el bloqueo no se anuncia con un 429 reintentable — devuelve un
login wall con HTTP 200. Un scraper propio no es un problema de código, es un
compromiso permanente de rotación de proxies y de perseguir cambios de DOM.
Apify es exactamente eso comprado hecho.

Qué clase de fuente es esto, dicho sin adornos
----------------------------------------------
Una cuenta como `@alertanoticiasvalparaiso` es rápida —suele publicar antes que
el MTT— y **no verifica nada**. Republica lo que le llega por mensaje directo.
Que se llame "noticias" no la convierte en un medio: no hay redacción, no hay
segunda fuente y no hay corrección. Por eso entra como
`EventSource.SOCIAL_MEDIA` y no como `MEDIA`, y por eso la confianza por defecto
es 0.35 y no los 0.70 de un medio.

Ojo con un detalle que ya está en el catálogo y conviene tener presente:
`SOURCE_BASE_CONFIDENCE[SOCIAL_MEDIA]` vale 0.45, pero
`RULES[SOCIAL_MEDIA].max_weight` en `confidence.py` vale 0.35 — el motor recorta
a 0.35 igual. Emitimos 0.35 para que lo que se guarda en `raw_events` sea lo
mismo que el motor va a usar, en vez de archivar un número que nadie respeta.
(La discrepancia entre esas dos tablas es anterior a este collector y no se toca
desde acá: arreglarla mueve la confianza de todas las fuentes sociales a la vez
y merece su propio commit.)

El camino de un post
--------------------
1. **Apify** raspa la cuenta según su propio Schedule (ver `apify_client`: NO lo
   disparamos nosotros) y deja los posts en el dataset de la corrida.
2. **Frescura** — se descartan los posts más viejos que
   `INSTAGRAM_MAX_AGE_MINUTES`. Filtro puro, sin costo.
3. **Clasificación determinista** (`classify_event_type`) — decide si el post
   describe una emergencia y de qué tipo. **Esta decisión NO la toma el modelo**,
   por la misma asimetría de daño que está escrita en el worker del MTT: una
   calle mal extraída produce un punto discutible y visible; un "esto es un
   accidente" alucinado produce un siniestro que nadie reportó.
4. **Delta** (`unseen`) — una consulta a `raw_events` descarta los posts que ya
   procesamos. Va **antes** del LLM, que es lo único caro de esta lista.
5. **Geocodificador LLM** (`geocode_text`) — el pipeline que ya existe:
   `gemini.extract_streets` para convertir prosa en `{street_1, street_2, city}`
   y `nominatim.geocode` para resolver eso a coordenadas.
6. **Ingesta** — `EventCreate` con `external_id = ig:<shortCode>`, idempotente
   por el índice único `uq_raw_events_source_external_id`.

Los pasos 2, 3 y 4 existen para que el paso 5 se ejecute lo menos posible. De 40
posts en el dataset, un día normal sobreviven dos o tres.

Lo que este collector NO resuelve
---------------------------------
* **Posts recopilatorios.** "Resumen del día: choque en Ruta 68, incendio en
  Playa Ancha, corte en Quilpué" produce UNA señal con UNA ubicación. El
  extractor devuelve un solo par de calles y no hay forma honesta de partir el
  texto sin que el modelo decida cuántos hechos hay. Se acepta la pérdida y
  queda anotada en `raw_data._extraction.multi_hint`.
* **Ediciones.** Instagram permite editar un caption. Un post ya ingerido no se
  vuelve a mirar (ver `unseen`): reprocesarlo costaría una llamada al modelo por
  corrida durante toda la vida del post, a cambio de una corrección que casi
  nunca ocurre.
* **La imagen.** `displayUrl` es una URL firmada del CDN de Instagram y
  **caduca**. Se guarda porque es trazabilidad de dónde salió la señal, no
  porque se pueda mostrar en el mapa dentro de una semana. Si algún día hay que
  mostrarla, hay que rehospedarla en el momento de la ingesta.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.geoservices import normalise_text, parse_timestamp
from app.collectors.nominatim import GeocodeResult, geocode
from app.collectors.nominatim import build_client as build_geo_client
from app.collectors.social import apify_client
from app.collectors.traffic import gemini
from app.collectors.traffic.bomberos_10_4_worker import find_codes
from app.collectors.traffic.transporteinforma_worker import extract_streets_via_llm
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)


# =============================================================================
#  Pre-filtro de relevancia
# =============================================================================
#
# Qué protege
# -----------
# El presupuesto del LLM. De cada corrida del Actor salen decenas de posts y la
# mayoría no son emergencias: política municipal, farándula, atardeceres desde
# el cerro, promociones. Mandarlos al extractor cuesta tokens y no produce nada.
#
# Es un filtro de RECALL, no de precisión. Un falso positivo cuesta una llamada
# al modelo y termina descartado más adelante; un falso negativo pierde un
# accidente para siempre y en silencio. Ante la duda, pasa.
#
# Todo se compara sobre el texto ya normalizado por `normalise_text`
# (`unicodedata` NFD → se descartan las marcas combinantes → minúsculas), así
# que **todos los términos de este bloque se escriben sin tildes y en
# minúscula**. Un término con tilde no coincidiría nunca y el fallo sería mudo:
# `test_los_terminos_estan_normalizados` lo impide.
#
# Coincidencia por SUBCADENA, no por palabra. "atropell" cubre atropello,
# atropellado y atropellaron sin enumerar conjugaciones; el precio es que hay
# que elegir raíces que no aparezcan dentro de otra palabra.

#: Tránsito. `accidente` a secas entra porque en estas cuentas casi siempre es
#: vial; cuando no lo es, `classify_event_type` lo manda igual a la familia
#: correcta o a `OTHER`.
TRAFFIC_TERMS: frozenset[str] = frozenset(
    {
        "choque",
        "colision",
        "volcamiento",
        "atropell",  # atropello, atropellado, atropellaron
        "desbarrancamiento",
        "desbarranc",  # desbarrancó, desbarrancado
        "accidente de transito",
        "accidente vehicular",
        "accidente",
        "alta energia",  # trauma de alta energía: jerga de rescate vehicular
        "transito suspendido",
        "siniestro vial",
        "vuelco",
    }
)

#: Incendios y materiales peligrosos.
FIRE_TERMS: frozenset[str] = frozenset(
    {
        "incendio",
        "fuego",
        "emanacion",
        "siniestro",
        "primera alarma",
        "segunda alarma",
        "tercera alarma",
        "estructural",
        "pastizales",
        "pastizal",
        "forestal",
        "llamas",
        "amago",
    }
)

#: Rescate de personas.
RESCUE_TERMS: frozenset[str] = frozenset(
    {
        "rescate",
        "persona atrapada",
        "atrapad",  # atrapado, atrapada, atrapados
        "caida de altura",
    }
)

#: Términos que por sí solos bastan para pasar el filtro.
CRITICAL_TERMS: frozenset[str] = TRAFFIC_TERMS | FIRE_TERMS | RESCUE_TERMS

#: Entidades de respuesta. **Deliberadamente NO disparan solas.**
#:
#: Es la decisión menos obvia del bloque y la que más ruido evita. "Bomberos de
#: Valparaíso celebró su aniversario junto al alcalde", "Carabineros lanza
#: campaña de seguridad escolar" y "SENAPRED capacita a dirigentes vecinales"
#: contienen la entidad y no son emergencias — y son, además, exactamente el
#: tipo de post que estas cuentas publican a diario. Una entidad dice QUIÉN
#: podría estar involucrado, nunca QUÉ pasó.
AGENCY_TERMS: frozenset[str] = frozenset(
    {
        "bomberos",
        "carabineros",
        "samu",
        "senapred",
        "conaf",
        "ambulancia",
    }
)

#: Lo que convierte la mención de una entidad en un hecho. Entidad + contexto
#: pasa el filtro; cualquiera de los dos por separado, no.
OPERATIONAL_TERMS: frozenset[str] = frozenset(
    {
        "emergencia",
        "evacuacion",
        "evacuar",
        "lesionad",  # lesionado, lesionados, lesionada
        "herid",  # herido, heridos, herida
        "fallecid",
        "damnificad",
        "de urgencia",
        "concurr",  # concurre, concurren, concurrió
        "acudi",  # acudió, acudieron
        "trabajan en el lugar",
    }
)

# -- Claves radiales del Sistema Nacional -------------------------------------
#
# El reconocimiento NO se reimplementa acá: se reutiliza `find_codes` del worker
# de Bomberos, que ya resuelve las cinco formas de escribir la misma clave
# (`10-4`, `10-0-4`, `10.4`, `10 – 4`) y rechaza las tres trampas que se le
# parecen (`10-40`, que es otra clave; `10-41`; y `10-4-2026`, que es una
# fecha). Ver el bloque «Reconocimiento de la clave» de
# `app/collectors/traffic/bomberos_10_4_worker.py`.
#
# DEUDA CONOCIDA: esas tres funciones son vocabulario del dominio y hoy viven en
# un worker concreto. El segundo consumidor —este— es la señal de que les
# corresponde un módulo propio (`app/collectors/codes.py`). No se mueve en este
# cambio para no arrastrar al worker de Bomberos y sus tests.

#: Clave normalizada → naturaleza de la señal. La comparación es por PREFIJO de
#: tupla, igual que en `matches_key`: `10-4-1` (rescate con víctima atrapada)
#: responde a `10-4` porque es un subtipo del mismo despacho, no otra
#: emergencia.
#:
#: Ojo con la familia `10-0`: `normalise_code` colapsa el cero intermedio, así
#: que `10-0-4` normaliza a `(10, 4)` y NO a `(10, 0)`. Esta tabla reconoce el
#: `10-0` escrito tal cual. Si el Cuerpo de la zona despacha los estructurales
#: como `10-0-1`, hay que agregar `(10, 1)` acá.
CODE_TYPES: dict[tuple[int, ...], EventType] = {
    (10, 0): EventType.STRUCTURAL_FIRE,  # incendio estructural
    (10, 2): EventType.WILDFIRE,  # pastizales
    (10, 3): EventType.RESCUE,  # rescate de personas
    (10, 4): EventType.ACCIDENT,  # rescate vehicular
}

#: `10-12` (apoyo) va aparte y **necesita compañía** para pasar el filtro. Dos
#: razones, y la primera es de dominio, no un parche:
#:
#: * Un apoyo no describe una emergencia nueva: es un despacho adicional a una
#:   que ya está en curso. Por sí solo no aporta un hecho al mapa.
#: * `10-12` colisiona con una fecha escrita corta ("el 10-12 se realizará…").
#:   Las fechas con año —`10-12-2026`— ya las rechaza `normalise_code`, pero la
#:   forma sin año pasaría.
#:
#: Para que dispare solo, mover esta entrada a `CODE_TYPES`. Es una línea.
SUPPORT_CODES: dict[tuple[int, ...], EventType] = {
    (10, 12): EventType.OTHER,  # apoyo
}

# -- Ruido con forma de emergencia --------------------------------------------

#: Frases que CONTIENEN un término crítico y no son una emergencia. Se **borran
#: del texto** antes de buscar, en vez de vetar el post entero: la excisión es
#: quirúrgica y un veto mal puesto perdería el accidente real que viniera en el
#: mismo caption.
#:
#: `fuegos artificiales` no es un ejemplo de manual: el show de Año Nuevo en el
#: Mar es el post más replicado del año en estas cuentas, y "fuego" lo habría
#: mandado entero al modelo cada 31 de diciembre.
#:
#: **El orden importa**: se excinde de la frase más larga a la más corta. Si
#: "prevencion de incendios" se borrara antes que "prevencion de incendios
#: forestales", quedaría suelto un "forestales" que es término crítico por sí
#: mismo y la campaña de CONAF pasaría igual.
#:
#: La lista se calibra con datos reales; empieza corta a propósito.
_NOISE_PHRASES: tuple[str, ...] = (
    "prevencion de incendios forestales",
    "campana de prevencion de incendios",
    "fuegos artificiales",
    "fuego artificial",
    "show de fuegos",
    "simulacro de incendio",
    "simulacro de emergencia",
    "simulacro de evacuacion",
    "aniversario del incendio",
    "anos del incendio",
    "prevencion de incendios",
    "seguro contra incendios",
    "a fuego lento",
)


def _haystack(caption: str) -> str:
    """Texto listo para buscar: normalizado y con el ruido conocido excindido."""
    text = normalise_text(caption)
    if not text:
        return ""
    for phrase in _NOISE_PHRASES:
        if phrase in text:
            text = text.replace(phrase, " ")
    return text


def _codes_in(haystack: str, table: dict[tuple[int, ...], EventType]) -> EventType | None:
    """Primer tipo cuya clave aparece en el texto. Comparación por prefijo."""
    for code in find_codes(haystack):
        for wanted, event_type in table.items():
            if code[: len(wanted)] == wanted:
                return event_type
    return None


def is_emergency(caption: str) -> bool:
    """¿Este caption habla de una emergencia? Síncrono, en memoria, sin red.

    Es el guardián del gasto: lo que devuelve `False` no llega nunca al
    extractor. Cuatro caminos para pasar, y sólo uno de ellos involucra
    entidades:

    1. Un **término crítico** (tránsito, incendio o rescate).
    2. Una **clave radial** de `CODE_TYPES` — la central diciendo qué despachó.
    3. **Entidad + contexto operativo**: "Bomberos concurre a…", "SAMU trasladó
       a un lesionado". Nunca la entidad sola (ver `AGENCY_TERMS`).
    4. El **`10-12` de apoyo** acompañado de una entidad o de contexto (ver
       `SUPPORT_CODES`).

    El costo es un puñado de búsquedas de subcadena sobre un texto de 1.500
    caracteres como máximo (`clean_caption` lo recorta). No hay I/O, no hay
    `await` y no hay nada que ceda el control: se puede llamar dentro del bucle
    de `fetch()` sin tocar el event loop que comparte con el motor de
    correlación.
    """
    haystack = _haystack(caption)
    if not haystack:
        return False

    if any(term in haystack for term in CRITICAL_TERMS):
        return True

    if _codes_in(haystack, CODE_TYPES) is not None:
        return True

    tiene_entidad = any(term in haystack for term in AGENCY_TERMS)
    tiene_contexto = any(term in haystack for term in OPERATIONAL_TERMS)

    if tiene_entidad and tiene_contexto:
        return True

    if _codes_in(haystack, SUPPORT_CODES) is not None:
        return tiene_entidad or tiene_contexto

    return False


# --- Clasificación determinista ----------------------------------------------
#
# El orden importa: se evalúa de lo más específico a lo más genérico y gana la
# primera coincidencia. "incendio forestal" tiene que mirarse antes que
# "incendio", o todo fuego terminaría siendo estructural.

_WILDFIRE = (
    "incendio forestal",
    "quema de pastizal",
    "pastizales",
    "pastizal",
    "foco de incendio",
)

_STRUCTURAL_FIRE = (
    "incendio estructural",
    "incendio en una vivienda",
    "incendio de vivienda",
    "incendio en local",
    "se quema una casa",
    "casa en llamas",
)

#: Marcador genérico de fuego. Sólo se consulta si ninguno de los específicos
#: coincidió, y produce `OTHER` a propósito — ver `classify_event_type`.
_GENERIC_FIRE = ("incendio", "llamas", "amago", "fuego", "emanacion")

_CLASSIFIERS: tuple[tuple[frozenset[str] | tuple[str, ...], EventType], ...] = (
    (_WILDFIRE, EventType.WILDFIRE),
    (_STRUCTURAL_FIRE, EventType.STRUCTURAL_FIRE),
    (TRAFFIC_TERMS, EventType.ACCIDENT),
    (RESCUE_TERMS, EventType.RESCUE),
)


def classify_event_type(text: str) -> EventType | None:
    """¿Qué describe este caption? None si no describe una emergencia.

    Reglas, no modelo. El contrato de Gemini en este proyecto son tres campos
    geográficos y ningún juicio sobre el hecho (ver
    `app/collectors/traffic/gemini.py`), y esa frontera no se mueve porque la
    fuente sea nueva.

    El fuego sin calificar (`incendio` a secas, que es como lo escribe la mitad
    de estas cuentas) devuelve `OTHER` y **no** `WILDFIRE` ni `SMOKE`. Es
    deliberado y es la decisión más discutible del módulo, así que conviene
    dejarla escrita:

    * `WILDFIRE` afirmaría que hay un incendio forestal, que es lo que CONAF
      confirma yendo al lugar. Un post de Instagram no puede afirmar eso.
    * `SMOKE` es un avistamiento de humo. Tampoco: el post dice fuego.
    * `OTHER` cae en la familia `other`, así que **no se fusiona** con los
      incendios que CONAF o FIRMS reporten a 500 m. Pierde corroboración, y ese
      es exactamente el intercambio buscado: preferimos un punto huérfano en el
      mapa antes que subirle la confianza a un incendio con evidencia que no
      vale lo que parece.

    Si mañana estas cuentas resultan ser buenas prediciendo incendios, el cambio
    es una línea acá y una entrada en `EVENT_TO_INCIDENT_TYPE`. Al revés —haber
    inflado incendios durante seis meses— no se puede deshacer.

    **Invariante con el pre-filtro**: devuelve `None` si y sólo si
    `is_emergency` devolvió `False`. Sin eso, un post podría pasar el filtro
    —pagando su llamada al modelo— y desaparecer después en el `if event_type is
    not None` de `fetch()`, que es la peor combinación posible: se gasta y no se
    guarda. Lo cubre `test_el_prefiltro_y_el_clasificador_no_se_contradicen`.
    """
    haystack = _haystack(text)
    if not haystack:
        return None

    # 1. La clave radial primero: es la central diciendo qué despachó, y eso
    #    vale más que adivinar por vocabulario. Un "10-0 en calle Serrano" es
    #    más específico que cualquier sinónimo de fuego que traiga el caption.
    code_type = _codes_in(haystack, CODE_TYPES)
    if code_type is not None:
        return code_type

    # 2. Vocabulario, de lo más específico a lo más genérico.
    for markers, event_type in _CLASSIFIERS:
        if any(marker in haystack for marker in markers):
            return event_type

    if any(marker in haystack for marker in _GENERIC_FIRE):
        return EventType.OTHER

    # 3. El pre-filtro dijo que sí y no sabemos de qué se trata (un apoyo, una
    #    entidad con contexto). `OTHER` es impreciso pero cierto; `None` sería
    #    tirar algo por lo que ya se pagó.
    if is_emergency(text):
        return EventType.OTHER

    return None


# --- Limpieza del caption ----------------------------------------------------

#: URLs completas. Un "más info en linktr.ee/…" no aporta nada al extractor y
#: sí ocupa tokens.
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

#: Menciones a otras cuentas. Son atribuciones ("vía @otracuenta"), no lugares.
_MENTION = re.compile(r"(?<!\w)@[\w.]+")

#: El bloque de etiquetas del final: una cadena de hashtags, emojis y espacios
#: que llega hasta el final del texto. Es el que hay que cortar entero.
_TRAILING_HASHTAGS = re.compile(r"(?:\s*#[\wÀ-ſ]+)+\s*$")

#: Hashtag suelto en medio de la frase. Acá el `#` se quita y la palabra se
#: conserva: "choque en #Valparaiso" pierde información si se borra completo.
_INLINE_HASHTAG = re.compile(r"#(?=[\wÀ-ſ])")

#: Separadores que estas cuentas usan para pegar el pie de página al cuerpo
#: ("———", "▬▬▬", "•••"). Todo lo que viene después es promoción.
_FOOTER = re.compile(r"[—–\-▬•=_]{3,}.*$", re.DOTALL)

#: Categorías Unicode que se descartan carácter a carácter:
#:   So — símbolos "otros": es donde viven 🚨, 🔥, ⚠ y compañía.
#:   Cs — surrogates sueltos, que aparecen cuando el JSON viene mal codificado.
#:   Cf — formato invisible: joiners de emoji, marcas de dirección de texto.
_DROP_CATEGORIES = frozenset({"So", "Cs", "Cf"})

#: Selectores de variación (U+FE0E/U+FE0F) y el keycap. No son categoría `So`
#: pero acompañan a los emojis y dejan basura si sobreviven.
_DROP_CODEPOINTS = frozenset({0xFE0E, 0xFE0F, 0x20E3})

#: Pistas de que el post cubre varios hechos a la vez. No se descarta —sería
#: perder el primero de los tres— pero se marca, para poder medir después cuánto
#: se está perdiendo por acá.
_MULTI_HINTS = ("resumen del dia", "resumen diario", "ademas, en otro punto")


def clean_caption(text: str) -> str:
    """Caption de Instagram → prosa que el extractor pueda leer.

    Un caption crudo es "🚨🚨 URGENTE 🚨🚨\\n\\nColisión múltiple en Av. España
    con Uno Norte, Viña del Mar.\\n\\n———\\nSíguenos\\n#valparaiso #alerta
    #noticias #viral". De ahí, lo único que le sirve al extractor son once
    palabras.

    Las cuatro operaciones responden a fallos distintos, y ninguna es cosmética:

    * **Emojis fuera.** Ocupan tokens y el modelo a veces los lee como
      contenido. Un caption con veinte 🚨 gasta el presupuesto en decoración.
    * **Pie de página fuera.** Todo lo que va tras una línea de guiones o
      bullets es promoción de la cuenta. Dejarlo dentro mete "Síguenos en
      Facebook" en el texto que se manda a geocodificar.
    * **Hashtags del final fuera, hashtags de en medio conservados sin `#`.**
      El bloque final es SEO; el de en medio suele ser la comuna. Borrar los dos
      pierde la ubicación, conservar los dos llena el texto de ruido.
    * **Menciones y URLs fuera.** Son atribución y promoción.

    Es la misma clase de problema que las ligaduras de Material Symbols en el
    portal del MTT: texto que se ve bien en pantalla y contamina lo que se
    manda al modelo y lo que queda archivado en `raw_data`.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    sin_emoji = "".join(
        char
        for char in raw
        if unicodedata.category(char) not in _DROP_CATEGORIES
        and ord(char) not in _DROP_CODEPOINTS
    )

    # El orden es deliberado: el pie de página se corta ANTES que los hashtags,
    # porque el separador suele venir seguido del bloque de etiquetas y cortar
    # primero por el separador se lleva las dos cosas de una vez.
    sin_footer = _FOOTER.sub(" ", sin_emoji)
    sin_urls = _URL.sub(" ", sin_footer)
    sin_menciones = _MENTION.sub(" ", sin_urls)
    sin_tags_finales = _TRAILING_HASHTAGS.sub(" ", sin_menciones)
    con_tags_planos = _INLINE_HASHTAG.sub("", sin_tags_finales)

    # El tope es el mismo del extractor. Truncar acá y no allá deja el texto
    # recortado también en `raw_events.text`, de modo que lo archivado sea
    # exactamente lo que el modelo vio.
    return " ".join(con_tags_planos.split())[: gemini.MAX_INPUT_CHARS]


def looks_like_digest(text: str) -> bool:
    """¿El caption parece cubrir varios hechos? Sólo marca; no descarta."""
    haystack = normalise_text(text)
    return any(hint in haystack for hint in _MULTI_HINTS)


# --- El post -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstagramPost:
    """Un post del dataset de Apify, ya reducido a lo que este sistema usa."""

    #: `shortCode`: el fragmento estable de la URL (`/p/<shortCode>/`). Es el id
    #: que da la propia fuente y por eso es la clave de la idempotencia. Ver
    #: `external_id_for`.
    short_code: str
    username: str | None
    #: Caption ya limpio (`clean_caption`). El crudo queda en `raw`.
    caption: str
    image_url: str | None
    published_at: datetime | None
    permalink: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


def external_id_for(post: InstagramPost) -> str:
    """`ig:<shortCode>`.

    **Nunca un hash del caption**, aunque sea lo que hace el worker del MTT. Allá
    no queda alternativa porque el portal no expone ids; acá sí los hay, y usar
    un hash del texto significaría que editar el caption —que Instagram
    permite— genera un id nuevo y duplica el accidente en el mapa.
    """
    return f"ig:{post.short_code}"


#: Alias de campo, en orden de preferencia. Cada Actor de Instagram del
#: marketplace nombra las cosas un poco distinto y cambiar de Actor —porque el
#: que usamos subió de precio o dejó de funcionar— no debería tocar el parser.
_ID_KEYS = ("shortCode", "shortcode", "short_code", "code", "id")
_CAPTION_KEYS = ("caption", "text", "description", "title")
_IMAGE_KEYS = ("displayUrl", "display_url", "imageUrl", "thumbnailUrl", "image")
_DATE_KEYS = ("timestamp", "takenAt", "taken_at", "publishedAt", "date")
_USER_KEYS = ("ownerUsername", "owner_username", "username", "ownerFullName")
_URL_KEYS = ("url", "postUrl", "permalink", "link")


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def parse_post(payload: Any) -> InstagramPost | None:
    """Item del dataset → `InstagramPost`. None si no sirve. Función pura.

    Se descarta el item que no traiga id **o** no traiga caption. El segundo
    caso es frecuente y legítimo: estas cuentas publican reels y carruseles sin
    texto, y un post sin caption no tiene nada que geocodificar — sin texto y
    sin coordenadas, `EventCreate` ni siquiera lo aceptaría (ver
    `_validate_geometry`).
    """
    if not isinstance(payload, Mapping):
        return None

    short_code = _first(payload, _ID_KEYS)
    if not short_code:
        return None

    caption = clean_caption(str(_first(payload, _CAPTION_KEYS) or ""))
    if not caption:
        return None

    permalink = _first(payload, _URL_KEYS)
    if not permalink:
        permalink = f"https://www.instagram.com/p/{str(short_code).strip()}/"

    return InstagramPost(
        short_code=str(short_code).strip(),
        username=_text_or_none(_first(payload, _USER_KEYS)),
        caption=caption,
        image_url=_text_or_none(_first(payload, _IMAGE_KEYS)),
        published_at=parse_timestamp(_first(payload, _DATE_KEYS)),
        permalink=str(permalink).strip(),
        raw=dict(payload),
    )


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def is_fresh(post: InstagramPost, *, now: datetime, max_age_minutes: int) -> bool:
    """¿El post es lo bastante reciente para describir el presente?

    Un post sin fecha se considera fresco. Es la decisión menos mala: el costo
    de equivocarse es procesar de más un post viejo —que el filtro por
    `external_id` va a atrapar en la corrida siguiente igual—, mientras que
    descartarlo pierde un accidente por un campo que el Actor no llenó.
    """
    if post.published_at is None:
        return True
    return (now - post.published_at) <= timedelta(minutes=max_age_minutes)


# --- Acople al Geocodificador LLM --------------------------------------------


async def geocode_text(
    text: str, *, geo_client: Any
) -> tuple[dict[str, Any], GeocodeResult | None]:
    """Texto libre → `({street_1, street_2, city}, punto|None)`.

    **Este es el `geocode_text(texto)` del encargo, y no reimplementa nada**: es
    un adaptador de dos líneas sobre el pipeline que ya existe y que hoy usa el
    worker del MTT.

    * `extract_streets_via_llm` (en `traffic/transporteinforma_worker`) llama a
      Gemini y cae a la heurística de reglas si el modelo no está configurado o
      no resuelve.
    * `nominatim.geocode` convierte ese diccionario en un punto, respetando el
      límite de 1 req/s que Nominatim impone por IP (limitador global al
      proceso, ver `app/collectors/nominatim.py`).

    Devuelve las **dos** piezas y no sólo lat/lon a propósito. Cuando mañana un
    punto esté mal, la pregunta va a ser cuál de los dos pasos falló: si el
    modelo leyó "Av. Alemania" donde decía "Av. Argentina", o si Nominatim
    resolvió esa calle al centro de otra comuna. Guardar sólo la coordenada
    borra esa distinción para siempre. Van separadas a `raw_data._extraction` y
    `raw_data._geocoding`.

    No lanza por fallos del modelo —`extract_streets_via_llm` ya los absorbe—
    pero **sí** deja pasar los de Nominatim: quien llama decide si un fallo de
    geocodificación vale una degradación de la corrida.
    """
    streets = await extract_streets_via_llm(text)
    if not streets or not streets.get("street_1"):
        return ({}, None)

    point = await geocode(geo_client, streets)
    return (streets, point)


# --- Collector ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedPost:
    """Lo que `fetch()` entrega a `normalize()`: post, tipo, extracción y punto."""

    post: InstagramPost
    event_type: EventType
    streets: dict[str, Any]
    point: GeocodeResult | None
    apify: dict[str, Any]


class InstagramApifyCollector(BaseCollector):
    """Posts de cuentas hiperlocales, geocodificados con el pipeline existente.

    Comparte con `TransporteInformaCollector` la ruptura de convención que allá
    está explicada: la geocodificación ocurre en `fetch()` y no en
    `normalize()`, para que `normalize()` siga siendo pura y testeable sin red.

    Y comparte con él una restricción operativa que conviene no perder de vista:
    el LLM y Nominatim son **presupuestos compartidos** entre los dos
    collectors. `GEMINI_MAX_CALLS_PER_RUN` acota una corrida, no el día; si
    ambos workers corren cada 5 y 10 minutos con el tope por defecto, el gasto
    diario es la suma de las dos cadencias. Es el número a mirar cuando llegue
    la primera factura.
    """

    name = "instagram_apify"
    #: `SOCIAL_MEDIA` y no un valor nuevo. `EventSource` es un ENUM de
    #: PostgreSQL: agregarle `INSTAGRAM` obliga a una migración con `ALTER TYPE`
    #: y a tocar `SOURCE_BASE_CONFIDENCE`, `RULES` y el frontend, todo para
    #: distinguir algo que ya se distingue por `collector_runs.collector` y por
    #: `raw_data._collector`. Si algún día la política de confianza necesita
    #: separar Instagram de otras redes, ahí sí valdrá la migración.
    source = EventSource.SOCIAL_MEDIA
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.APIFY_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.APIFY_TOKEN.strip():
            # Igual que el resto de la familia: sin credencial el constructor
            # lanza, el runner deja una corrida `failed` en `collector_runs` y
            # el problema se ve. Un collector que arranca y no puede leer nada
            # es la forma de no enterarse en tres semanas.
            raise CollectorError(
                "APIFY_TOKEN no está configurada; el collector de Instagram no "
                "puede autenticarse contra Apify."
            )
        self.actor_id = settings.APIFY_INSTAGRAM_ACTOR_ID.strip()
        if not self.actor_id:
            raise CollectorError(
                "APIFY_INSTAGRAM_ACTOR_ID no está configurada; no hay Actor que "
                "leer."
            )
        self.max_items = settings.APIFY_MAX_ITEMS
        self.max_run_age = settings.APIFY_MAX_RUN_AGE_MINUTES
        self.max_post_age = settings.INSTAGRAM_MAX_AGE_MINUTES
        self.max_geocodes = settings.INSTAGRAM_MAX_GEOCODES
        self.max_llm_calls = settings.GEMINI_MAX_CALLS_PER_RUN
        self.confidence = settings.INSTAGRAM_CONFIDENCE

    def run_params(self) -> dict[str, Any]:
        """Parámetros de la corrida. **El token no aparece acá, y no debe.**

        `collector_runs.params` es una columna JSON consultable desde la API de
        salud: cualquier cosa que se escriba en ella es, a efectos prácticos,
        pública para quien tenga acceso a la base.
        """
        return {
            "actor_id": self.actor_id,
            "cuentas": list(settings.APIFY_INSTAGRAM_ACCOUNTS),
            "max_items": self.max_items,
            "max_run_age_min": self.max_run_age,
            "max_post_age_min": self.max_post_age,
            "max_geocodes": self.max_geocodes,
            "max_llm_calls": self.max_llm_calls,
            "extraction_mode": (
                gemini.MODE_GEMINI if gemini.is_configured() else gemini.MODE_HEURISTIC
            ),
        }

    # -- Pre-filtro de relevancia --------------------------------------------

    @staticmethod
    def _is_emergency(caption: str) -> bool:
        """Guardián del gasto: lo que no pasa acá no llega al modelo.

        Es `staticmethod` por la convención del proyecto: las piezas puras se
        testean sin instanciar el collector, sin sesión y sin configuración. La
        lógica vive en `is_emergency`, a nivel de módulo, para que
        `classify_event_type` pueda consultarla sin construir un collector.
        """
        return is_emergency(caption)

    # -- Delta fetching -------------------------------------------------------

    async def unseen(self, posts: Sequence[InstagramPost]) -> list[InstagramPost]:
        """Descarta los posts que ya están en `raw_events`. **Una** consulta.

        El upsert de `ingest_batch` ya es idempotente, así que esto no evita
        duplicados: evita **pagarlos**. Sin este filtro, cada corrida mandaría
        los mismos cuarenta posts a Gemini y a Nominatim para que la base los
        descarte después, en silencio y con la factura ya emitida.

        La lógica es de tres capas, cada una más cara que la anterior:

        1. `is_fresh` — puro, sin I/O. Descarta lo viejo.
        2. esto — un `SELECT` acotado por el índice único, sobre un puñado de
           ids.
        3. `uq_raw_events_source_external_id` — la red de seguridad. Si las dos
           anteriores fallan, la base sigue sin duplicar nada.

        Se descartó la cuarta capa evidente —guardar en algún sitio el
        `finishedAt` de la última corrida procesada y pedir sólo lo posterior—
        por frágil: si una corrida muere después de mover la marca y antes de
        escribir los eventos, la ventana se pierde y con ella los accidentes de
        esos cinco minutos, sin dejar rastro. El estado ya está en `raw_events`,
        que es donde se puede auditar; no hace falta una segunda copia que se
        pueda desincronizar.
        """
        if not posts:
            return []

        ids = [external_id_for(post) for post in posts]
        known = await self.service.repo.ids_by_external_id(self.source, ids)
        return [post for post in posts if external_id_for(post) not in known]

    # -- Orquestación ---------------------------------------------------------

    async def fetch(self) -> Sequence[ResolvedPost]:
        """Lee el dataset, filtra y geocodifica lo que sobrevive.

        Todo fallo sale de acá como `CollectorError` y de ninguna otra forma.
        `request_json` ya traduce timeouts, 5xx, DNS y TLS (ver
        `geoservices.request_response`), y el `except Exception` cubre lo que no
        anticipamos al cruzar la frontera con un servicio ajeno.
        `BaseCollector.run()` lo registra en `collector_runs` y el resto del
        sistema no se entera: una caída de Apify no puede llevarse por delante la
        corrida de CONAF.
        """
        now = datetime.now(UTC)

        try:
            async with apify_client.build_client() as client:
                run = await apify_client.fetch_last_run(client, self.actor_id)
                items = await apify_client.fetch_items(
                    client, self.actor_id, limit=self.max_items
                )
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                f"instagram_apify: fallo inesperado hablando con Apify: "
                f"{type(exc).__name__}: {exc}",
                detail={"actor": self.actor_id},
            ) from exc

        stale, motivo = apify_client.run_looks_stale(run, self.max_run_age)
        if stale:
            # No es fatal —el dataset viejo puede traer algo que aún no
            # procesamos— pero tiene que verse. Es la diferencia entre "hoy no
            # hubo emergencias" y "el Schedule de Apify lleva dos días muerto",
            # que sin este aviso se ven idénticas desde `collector_runs`.
            self.warn(f"datos rancios: {motivo}")

        useful, problemas = apify_client.describe_items(items)
        for problema in problemas:
            self.warn(problema)

        posts = [post for raw in useful if (post := parse_post(raw)) is not None]
        frescos = [
            post
            for post in posts
            if is_fresh(post, now=now, max_age_minutes=self.max_post_age)
        ]

        # Pre-filtro y clasificación, ANTES del delta y ANTES del modelo: los
        # dos son síncronos, en memoria y sin red, y descartan la mayor parte
        # del dataset (estas cuentas publican política municipal, farándula y
        # atardeceres entre las emergencias).
        candidatos: list[tuple[InstagramPost, EventType]] = []
        ignorados = 0
        for post in frescos:
            if not self._is_emergency(post.caption):
                ignorados += 1
                logger.debug(
                    "Post ignorado: No contiene lenguaje de emergencia",
                    extra={
                        "collector": self.name,
                        "external_id": external_id_for(post),
                        # El caption recortado, no entero: el log de un worker
                        # que corre cada 5 minutos no es el lugar donde archivar
                        # los posts descartados, pero sin una muestra no hay
                        # forma de calibrar el diccionario contra datos reales.
                        "muestra": post.caption[:120],
                    },
                )
                continue

            event_type = classify_event_type(post.caption)
            if event_type is not None:
                candidatos.append((post, event_type))

        nuevos = await self.unseen([post for post, _ in candidatos])
        nuevos_ids = {external_id_for(post) for post in nuevos}
        pendientes = [
            (post, tipo) for post, tipo in candidatos if external_id_for(post) in nuevos_ids
        ]

        resueltos = await self._resolve(pendientes, run)

        logger.info(
            "posts de Instagram procesados",
            extra={
                "collector": self.name,
                "apify_run": run.run_id,
                "items": len(items),
                "posts": len(posts),
                "frescos": len(frescos),
                # `ignorados` es la métrica del pre-filtro. Si se va a cero, el
                # diccionario dejó de filtrar y se está pagando el modelo de más;
                # si se lleva todo, se está perdiendo cobertura.
                "ignorados_prefiltro": ignorados,
                "emergencias": len(candidatos),
                "nuevos": len(pendientes),
                "geocodificados": sum(1 for r in resueltos if r.point is not None),
            },
        )
        return resueltos

    async def _resolve(
        self,
        pendientes: Sequence[tuple[InstagramPost, EventType]],
        run: apify_client.ApifyRun,
    ) -> list[ResolvedPost]:
        """Pasa por el geocodificador LLM lo que sobrevivió a los filtros."""
        if not pendientes:
            return []

        resueltos: list[ResolvedPost] = []
        llm_calls = 0
        geocodes = 0
        apify_meta = run.as_dict()

        # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
        # conexión TLS y mantiene un único User-Agent identificable, que es
        # parte del contrato de uso del servicio.
        async with build_geo_client() as geo_client:
            for post, event_type in pendientes:
                streets: dict[str, Any] = {}
                point: GeocodeResult | None = None

                if llm_calls >= self.max_llm_calls:
                    self.warn(
                        f"se alcanzó el tope de {self.max_llm_calls} llamadas al "
                        f"modelo por corrida; el resto queda sin ubicación"
                    )
                elif geocodes >= self.max_geocodes:
                    self.warn(
                        f"se alcanzó el tope de {self.max_geocodes} "
                        f"geocodificaciones por corrida; el resto queda sin "
                        f"coordenadas"
                    )
                else:
                    llm_calls += 1
                    geocodes += 1  # un intento consume su turno, responda o no
                    try:
                        streets, point = await geocode_text(
                            post.caption, geo_client=geo_client
                        )
                    except Exception as exc:
                        # Un fallo de Nominatim NO pierde el post, y sobre todo
                        # no pierde los otros diecinueve del lote: por eso la
                        # captura es de `Exception` y no sólo de `CollectorError`.
                        self.warn(
                            f"la geocodificación falló para un post "
                            f"({type(exc).__name__}): {exc}"
                        )

                resueltos.append(
                    ResolvedPost(
                        post=post,
                        event_type=event_type,
                        streets=streets,
                        point=point,
                        apify=apify_meta,
                    )
                )

        return resueltos

    def normalize(self, records: Sequence[ResolvedPost]) -> list[EventCreate]:
        """`ResolvedPost` → `EventCreate`. Pura: sin red y sin base."""
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        sin_punto = 0

        for item in records:
            post = item.post
            timestamp = post.published_at or now
            if timestamp > now:
                # Reloj adelantado en la fuente. `EventCreate` rechazaría el
                # evento por futuro (ver `_reject_far_future`) y perderíamos la
                # señal entera por un desfase de segundos.
                timestamp = now
            if item.point is None:
                sin_punto += 1

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=self.source,
                    type=item.event_type,
                    lat=item.point.lat if item.point else None,
                    lon=item.point.lon if item.point else None,
                    text=post.caption[:10_000],
                    external_id=external_id_for(post),
                    confidence=self.confidence,
                    raw_data={
                        "cuenta": post.username,
                        "permalink": post.permalink,
                        # URL firmada del CDN de Instagram: CADUCA. Se guarda
                        # como trazabilidad de dónde salió la señal, no como
                        # algo que el mapa pueda mostrar la semana que viene.
                        "image_url": post.image_url,
                        "image_url_efimera": True,
                        "comuna": item.streets.get("city"),
                        "_collector": self.name,
                        "_apify": item.apify,
                        "_extraction": {
                            **item.streets,
                            "mode": (
                                gemini.MODE_GEMINI
                                if gemini.is_configured()
                                else gemini.MODE_HEURISTIC
                            ),
                            "multi_hint": looks_like_digest(post.caption),
                        },
                        "_geocoding": item.point.as_dict() if item.point else None,
                        # El caption original, sin limpiar. `text` guarda el
                        # limpio —que es lo que vio el modelo— y esto permite
                        # reprocesar sin volver a pagarle a Apify.
                        "caption_original": str(
                            post.raw.get("caption") or post.raw.get("text") or ""
                        )[:10_000],
                    },
                )
            )

        if sin_punto:
            self.warn(
                f"{sin_punto} posts quedaron sin coordenadas; no entran al Paso A "
                f"del motor pero sí quedan registrados"
            )
        return events


__all__ = [
    "AGENCY_TERMS",
    "CODE_TYPES",
    "CRITICAL_TERMS",
    "FIRE_TERMS",
    "OPERATIONAL_TERMS",
    "RESCUE_TERMS",
    "SUPPORT_CODES",
    "TRAFFIC_TERMS",
    "InstagramApifyCollector",
    "InstagramPost",
    "ResolvedPost",
    "classify_event_type",
    "clean_caption",
    "external_id_for",
    "geocode_text",
    "is_emergency",
    "is_fresh",
    "looks_like_digest",
    "parse_post",
]
