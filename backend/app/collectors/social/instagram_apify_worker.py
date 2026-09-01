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
from app.collectors.traffic.transporteinforma_worker import extract_streets_via_llm
from app.collectors.vocabulary import (
    AGENCY_TERMS,
    CODE_TYPES,
    CRITICAL_TERMS,
    FIRE_TERMS,
    FLOOD_TERMS,
    LANDSLIDE_TERMS,
    NOISE_PHRASES,
    OPERATIONAL_TERMS,
    RESCUE_TERMS,
    SUPPORT_CODES,
    TRAFFIC_TERMS,
    classify_event_type,
    haystack,
    is_emergency,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)


# =============================================================================
#  Pre-filtro de relevancia y clasificación
# =============================================================================
#
# El diccionario de emergencia **vivía acá** —fue el primer collector que tuvo
# que leer texto libre— y hoy vive en `app/collectors/vocabulary.py`. Este
# archivo lo importa y lo re-exporta: los nombres son superficie pública desde
# antes de la extracción, y los tests de esta fuente los usan tal cual.
#
# Por qué se movió, en corto: cuando el worker de prensa lo importó, quedó un
# collector de diarios dependiendo de uno de redes sociales, que es una flecha
# que no describe ninguna relación real entre las dos fuentes — sólo el orden en
# que se escribieron. La deuda estaba anotada acá mismo desde que `find_codes`
# tuvo su segundo consumidor. El módulo nuevo trae además el vocabulario de
# inundación y remoción en masa, que a esta fuente le faltaba tanto como a la
# otra: estas cuentas publican calles anegadas todo el invierno y el sistema no
# tenía cómo nombrarlas.
#
# Lo que NO cambió para este worker: `is_emergency` sigue siendo síncrono y sin
# red —se llama dentro del bucle de `fetch()`—, sigue siendo un filtro de recall
# y no de precisión, y `classify_event_type` sigue devolviendo `None` si y sólo
# si `is_emergency` devolvió `False`.

#: Alias de compatibilidad. El contenido y su justificación están en
#: `vocabulary`; acá sólo se conserva el nombre por el que los tests y el resto
#: del paquete ya los conocen.
_NOISE_PHRASES: tuple[str, ...] = NOISE_PHRASES
_haystack = haystack


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
    """¿El caption parece cubrir varios hechos? Sólo marca; no descarta.

    Busca sobre el texto normalizado **sin excindir ruido**: acá interesa la
    forma del caption, no si describe una emergencia, así que se usa
    `normalise_text` y no `haystack`.
    """
    normalizado = normalise_text(text)
    return any(hint in normalizado for hint in _MULTI_HINTS)


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
    "FLOOD_TERMS",
    "LANDSLIDE_TERMS",
    "NOISE_PHRASES",
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
