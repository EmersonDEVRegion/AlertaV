"""Prensa local de la V Región — Sitio del Suceso y Pura Noticia, a costo cero.

Por qué NO hay un Apify en el medio
-----------------------------------
El collector de Instagram paga un intermediario porque el WAF de Meta bloquea
cualquier scraper propio a los dos o tres intentos, y sortearlo es un compromiso
permanente de rotación de proxies. Estos dos portales son lo contrario: publican
para ser leídos, uno de ellos ofrece RSS estándar y ninguno exige credencial. La
única cortesía que piden es un `User-Agent` reconocible y no golpearlos cada
minuto; ambas cosas cuestan una línea de configuración, no una suscripción.

Qué clase de fuente es esto
---------------------------
`EventSource.MEDIA`, y la distinción con `SOCIAL_MEDIA` importa. Una cuenta de
Instagram que se llama "noticias" republica lo que le llega por mensaje directo:
no hay redacción, no hay segunda fuente y no hay corrección. Estos dos tienen
firma, tienen editor y publican rectificaciones. Eso no los vuelve
`confirming` —no van al lugar a constatar, salvo cuando van— pero sí los pone
una banda entera por encima: 0.60 contra 0.35.

Ojo con la misma trampa que documenta el worker de Instagram, que acá aparece en
la otra dirección: `SOURCE_BASE_CONFIDENCE[MEDIA]` vale **0.70** y
`RULES[MEDIA].max_weight` en `confidence.py` vale **0.60**. El motor recorta a
0.60 igual. Se emite 0.60 para que lo archivado en `raw_events` sea exactamente
lo que el motor va a usar, en vez de guardar un número que nadie respeta. La
discrepancia entre esas dos tablas es anterior a este collector y no se toca
desde acá.

Qué se verificó de cada portal (31 de agosto de 2026)
------------------------------------------------------
* **Sitio del Suceso** — WordPress 7.1. `https://www.sitiodelsuceso.cl/feed/`
  devuelve RSS 2.0 (`application/rss+xml`) con `<guid isPermaLink="false">`
  estable, `<pubDate>` con hora y zona, `<description>` con la bajada y
  `<content:encoded>` con el cuerpo completo. Y un regalo que no estaba en el
  encargo: **`<category>` trae la comuna** (Valparaíso, Quilpué, Viña del Mar,
  Nogales, Cabildo…). Ver `comuna_en_categorias`.
* **Pura Noticia** — `www.puranoticia.cl` redirige a `puranoticia.pnt.cl`, que
  **no es WordPress y no publica RSS**: no hay `<link rel="alternate">` en el
  documento y `/rss`, `/rss.xml` y `/feed` devuelven cuerpo vacío. Se lee la
  sección regional por HTML. Por eso su fila de `LOCAL_NEWS_SOURCES` deja el
  campo del feed en blanco: apuntar a un feed inexistente costaría una petición
  fallida y una advertencia por corrida, cada corrida, para siempre.

Es decir: los dos caminos que pedía el encargo no son "el bueno y el parche".
Son un portal cada uno, y por eso los dos tienen que funcionar.

El camino de una noticia
------------------------
1. **Lectura** (`_leer_portal`) — RSS con `feedparser` si el portal lo tiene,
   HTML con BeautifulSoup si no, o si el feed llegó roto o vacío. Cada portal va
   dentro de su propio `try`: uno caído no puede llevarse al otro.
2. **Frescura** (`es_reciente`) — puro, sin red.
3. **Pre-filtro** (`_is_emergency`) — el diccionario heurístico que ya existe,
   reutilizado tal cual. Sin esto, cada corrida mandaría cuarenta noticias de
   política municipal al modelo.
4. **Clasificación** (`classify_event_type`) — reglas, no modelo.
5. **Delta** (`unseen`) — UNA consulta a `raw_events` para los dos portales
   juntos. Va **antes** del LLM, que es lo único caro de la lista.
6. **Geocodificador LLM** (`geocode_noticia`) — `gemini.extract_streets` +
   `nominatim.geocode`, con la comuna del `<category>` como respaldo gratis.
7. **Ingesta** — `EventCreate` con `external_id = prensa:<portal>:<hash>`.

Los pasos 2 a 5 existen para que el 6 se ejecute lo menos posible. Medido contra
el feed real de Sitio del Suceso del 31 de agosto: de 10 noticias, **una** pasó
el pre-filtro (el rescate de dos excursionistas en el Tranque La Luz), y era la
única emergencia del lote. Las otras nueve eran formalizaciones, decomisos de
droga, un macrocentro oncológico y un plan de bacheo.

Lo que este collector NO resuelve
---------------------------------
* **La bajada de Pura Noticia.** Sus tarjetas de portada traen titular, imagen y
  fecha, y **ningún `<p>`**. El encargo suponía titular + bajada; la realidad de
  ese portal es titular a secas. Entrar al artículo por la bajada costaría una
  petición por noticia y no se hace: el titular de un medio ya está escrito para
  contener el hecho y el lugar. Queda anotado en `raw_data._prensa.tiene_bajada`
  para poder medir después cuánta geocodificación se pierde por ahí.
* **La hora en el HTML.** Las tarjetas fechan "Lunes 31 de agosto de 2026", sin
  hora. Ver `parse_fecha_es` y el tratamiento de `resolucion_dia` en
  `normalize`: una noticia con resolución de día NUNCA se estampa como si
  acabara de ocurrir.
* **Las noticias recopilatorias.** Igual que en Instagram: "Balance del temporal:
  tres derrumbes y un corte" produce UNA señal con UNA ubicación.
* **La retrospectiva judicial.** Estos portales cubren tribunales, y una crónica
  de un juicio por el megaincendio de 2024 contiene la palabra "incendio". Ver
  `_RUIDO_PRENSA`: se excinden las formas fechadas, que son las únicas que no
  pueden describir un hecho presente.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup, Tag

from app.collectors.base import BaseCollector
from app.collectors.geoservices import normalise_text, parse_timestamp, request_text
from app.collectors.nominatim import GeocodeResult, geocode
from app.collectors.nominatim import build_client as build_geo_client
from app.collectors.traffic import gemini
from app.collectors.traffic.bomberos_10_4_worker import revisar_feed, strip_html
from app.collectors.traffic.transporteinforma_worker import extract_streets_via_llm
from app.collectors.vocabulary import (
    HEADLINE_VERBS,
    PRESS_NOISE_PHRASES,
    clasificar_noticia,
    es_emergencia,
    haystack_prensa,
    tipo_por_verbo,
)
from app.collectors.weather.comunas import COMUNAS_V_REGION
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)


# =============================================================================
#  Vocabulario de emergencia
# =============================================================================
#
# El diccionario heurístico no se reimplementa acá **y ya no se importa del
# worker de Instagram**: vive en `app/collectors/vocabulary.py`, que es donde le
# corresponde. Este archivo era el tercer acreedor de esa deuda y el que la hizo
# insostenible —un collector de prensa importando de uno de redes sociales es una
# dependencia que no describe ninguna relación real entre las dos fuentes—, así
# que se pagó antes de sumar la fuente que la habría hecho cuádruple.
#
# Lo que se llevó el módulo nuevo, y que antes vivía en este archivo:
#
# * `_RUIDO_PRENSA` → `PRESS_NOISE_PHRASES`
# * `_VERBOS_TITULAR` → `HEADLINE_VERBS`
# * `_haystack_prensa`, `es_emergencia` y `clasificar_noticia`, tal cual
#
# Los nombres viejos siguen disponibles como alias más abajo: son superficie
# pública de este worker desde antes de la extracción y los tests los usan.
#
# El módulo trae además el vocabulario que a esta fuente le faltaba más que a
# ninguna: **inundación y remoción en masa**. Estos portales cubren el invierno
# de Valparaíso calle por calle, y hasta ahora cada anegamiento y cada socavón
# pasaba por el pre-filtro sin que una sola palabra lo reconociera.

#: Alias de compatibilidad. El contenido y su justificación están en
#: `vocabulary`.
_RUIDO_PRENSA: tuple[str, ...] = PRESS_NOISE_PHRASES
_VERBOS_TITULAR: dict[str, EventType] = HEADLINE_VERBS
_haystack_prensa = haystack_prensa
_tipo_por_verbo = tipo_por_verbo


# =============================================================================
#  Portales
# =============================================================================


@dataclass(frozen=True, slots=True)
class NewsPortal:
    """Un portal declarado en `LOCAL_NEWS_SOURCES`.

    Los dos caminos son independientes y ninguno es obligatorio:

    * sólo `feed_url` → se lee RSS y no hay respaldo;
    * sólo `portada_url` → se raspa HTML directamente (el caso de Pura Noticia);
    * los dos → RSS, con la portada como respaldo si el feed llega roto o vacío.
    """

    slug: str
    nombre: str
    feed_url: str | None
    portada_url: str | None

    @property
    def base_url(self) -> str:
        """Raíz para resolver los `href` relativos del HTML.

        Pura Noticia enlaza como `/region-valparaiso/<slug>`: sin esto, el `link`
        —que es la identidad de la noticia y la clave del delta fetching— sería
        una ruta sin dominio y dos portales podrían colisionar en el mismo id.
        """
        referencia = self.portada_url or self.feed_url or ""
        return referencia

    @property
    def label(self) -> str:
        return f"prensa:{self.slug}"


def parse_portals(raw: str | Sequence[str] | None) -> list[NewsPortal]:
    """Declaración textual del `.env` → portales.

    Formato ``slug|nombre|feed_url|portada_url`` separando varios con ``;``, el
    mismo idioma que `FIRMS_SOURCES` y `OPENMETEO_COMUNAS`. Cualquiera de las dos
    URL puede ir vacía; las dos vacías es un error de configuración y se dice.

    Se valida al construir el collector —y no al leer— para que una fila mal
    escrita deje una corrida `failed` con el motivo en `collector_runs`, en vez
    de un portal que silenciosamente deja de consultarse.
    """
    if raw is None:
        return []
    trozos = raw.split(";") if isinstance(raw, str) else list(raw)

    portales: list[NewsPortal] = []
    for trozo in trozos:
        token = trozo.strip()
        if not token:
            continue
        partes = [parte.strip() for parte in token.split("|")]
        if len(partes) < 4:
            raise ValueError(
                f"portal mal declarado: {token!r}. Formato esperado "
                f"'slug|nombre|feed_url|portada_url' (las URL pueden ir vacías)"
            )
        slug, nombre, feed_url, portada_url = partes[:4]
        if not slug:
            raise ValueError(f"portal sin slug en {token!r}")
        if not feed_url and not portada_url:
            raise ValueError(
                f"el portal {slug!r} no declara ni feed ni portada: no hay de "
                f"dónde leer"
            )
        portales.append(
            NewsPortal(
                slug=slug,
                nombre=nombre or slug,
                feed_url=feed_url or None,
                portada_url=portada_url or None,
            )
        )
    return portales


# =============================================================================
#  La noticia
# =============================================================================


@dataclass(frozen=True, slots=True)
class NewsItem:
    """Una noticia ya reducida a lo que este sistema usa."""

    portal: str
    portal_nombre: str
    titular: str
    #: Bajada o resumen. Cadena vacía cuando la fuente no la publica (Pura
    #: Noticia en portada); nunca `None`, para que concatenar no bifurque.
    bajada: str
    link: str
    #: Identificador que da la propia fuente (`<guid>`). `None` en HTML.
    guid: str | None
    published_at: datetime | None
    #: `True` si de la fecha sólo se conoce el día, sin hora. Cambia por completo
    #: cómo se estampa el evento — ver `normalize`.
    resolucion_dia: bool
    #: Comuna declarada por la fuente (`<category>` en RSS). Respaldo gratuito
    #: para el geocodificador; ver `geocode_noticia`.
    comuna_hint: str | None
    #: "rss" o "html". Queda en `raw_data` porque las dos rutas tienen calidad
    #: distinta y hay que poder medirlas por separado.
    origen: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def texto(self) -> str:
        """Titular + bajada, que es lo que se filtra y lo que ve el modelo.

        Se concatenan **el titular y la bajada, no el cuerpo**. El cuerpo de una
        crónica menciona todos los lugares del relato: la nota de "El Diablo" del
        31 de agosto nombra la Estación Puerto, avenida Errázuriz y un tren de
        EFE en tres párrafos distintos. El extractor devuelve un solo par de
        calles, así que darle el cuerpo entero no le da más contexto: le da tres
        candidatos y ninguna forma de elegir. El titular y la bajada contienen el
        hecho y su lugar, que es exactamente lo que se le pide.
        """
        if not self.bajada:
            return self.titular
        return f"{self.titular}. {self.bajada}"


def external_id_for(item: NewsItem) -> str:
    """`prensa:<portal>:<hash>`.

    Se hashea la **identidad** de la noticia —su `<guid>`, o su URL cuando el
    portal no da guid—, jamás su texto. La distinción es la misma que documenta
    el worker de Instagram y acá pesa más todavía: un medio corrige y actualiza
    sus notas varias veces el mismo día, y un id derivado del texto convertiría
    cada corrección en una emergencia nueva sobre el mapa.

    El prefijo lleva el portal porque dos medios cubriendo el mismo choque son
    **dos señales independientes**, y el motor debe poder corroborarlas una con
    otra. Colapsarlas en un id compartido borraría justamente la corroboración
    que hace valioso tener dos fuentes.
    """
    identidad = item.guid or item.link
    digest = hashlib.sha256(identidad.encode("utf-8")).hexdigest()[:24]
    return f"prensa:{item.portal}:{digest}"


# -- Comunas -------------------------------------------------------------------

#: Nombres normalizados de las 36 comunas continentales, de la más larga a la más
#: corta. El orden importa al buscar por subcadena: "La Calera" tiene que
#: probarse antes que "Calera", y "Villa Alemana" antes que "Alemana", o la
#: comuna detectada sería la equivocada.
_COMUNAS_NORMALIZADAS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((normalise_text(comuna.nombre), comuna.nombre) for comuna in COMUNAS_V_REGION),
        key=lambda par: len(par[0]),
        reverse=True,
    )
)


def comuna_en_categorias(categorias: Sequence[str]) -> str | None:
    """Primera comuna de la V Región presente en las categorías del ítem.

    Sitio del Suceso etiqueta cada nota con su comuna (`<category>`), que es la
    propia fuente diciendo dónde ocurrió. Es información gratuita y mejor que
    cualquier heurística sobre el texto, y se usa como respaldo de `city` cuando
    el extractor no lo resuelve — ver `geocode_noticia`.

    Se compara por igualdad y no por subcadena: una categoría es una etiqueta
    corta y controlada, no prosa. Buscar subcadenas ahí haría que la categoría
    "Policial" no aporte nada y que "Deportes Quillota" aporte "Quillota", que
    es exactamente el falso positivo que no queremos.
    """
    for cruda in categorias:
        etiqueta = normalise_text(cruda)
        if not etiqueta:
            continue
        for normalizada, nombre in _COMUNAS_NORMALIZADAS:
            if etiqueta == normalizada:
                return nombre
    return None


def comuna_en_texto(texto: str) -> str | None:
    """Comuna nombrada en el texto. Respaldo del respaldo, para el camino HTML.

    Acá sí se busca por subcadena, porque la entrada es prosa. Es más frágil que
    `comuna_en_categorias` —"vecinos de Valparaíso viajaron a Los Andes" devuelve
    la primera que aparezca— y por eso sólo se consulta cuando no hay categoría,
    y sólo alimenta un campo que el extractor puede sobrescribir.
    """
    haystack = normalise_text(texto)
    if not haystack:
        return None
    for normalizada, nombre in _COMUNAS_NORMALIZADAS:
        if normalizada in haystack:
            return nombre
    return None


# -- Fechas en español ---------------------------------------------------------

_MESES: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,  # variante aceptada por la RAE y usada en Chile
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

#: "Lunes 31 de agosto de 2026". Se busca sobre el texto ya normalizado (sin
#: tildes, en minúscula), así que el día de la semana se ignora por construcción.
_FECHA_LARGA = re.compile(r"(?<!\d)(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})(?!\d)")

#: Hora suelta al principio de una tarjeta ("23:10Bodyboardista…"). Se acota a
#: horas válidas para no confundirla con un marcador ("1:0", "45:12" de un
#: cronómetro) y se exige que no venga pegada a más dígitos.
_HORA_SUELTA = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


def parse_fecha_es(texto: str, *, ahora: datetime) -> tuple[datetime | None, bool]:
    """Fecha de una tarjeta de portada → `(instante, resolucion_dia)`.

    Devuelve la pareja y no sólo el instante porque la diferencia decide cómo se
    estampa el evento. Tres casos, en orden de preferencia:

    1. **Fecha larga + hora** — "31 de agosto de 2026 23:10". Instante completo.
    2. **Sólo fecha larga** — "Lunes 31 de agosto de 2026". Se devuelve a
       medianoche UTC con `resolucion_dia=True`: el día es cierto, la hora no
       existe y no se inventa.
    3. **Sólo hora** — "23:10", que es como fecha Pura Noticia su río de últimas
       noticias. Se combina con la fecha de la corrida. Es una suposición
       razonable —una tarjeta que muestra sólo la hora está diciendo "hoy"— pero
       se protege contra el borde: si la hora resultante cae en el futuro (son
       las 00:20 UTC y la tarjeta dice 23:10 hora de Chile), se le resta un día
       en vez de emitir un evento futuro que `EventCreate` rechazaría.

    `parse_timestamp` de `geoservices` no cubre nada de esto: entiende epoch,
    ISO 8601 y los formatos numéricos chilenos, y ninguno de los tres aparece en
    una portada escrita para personas.
    """
    haystack = normalise_text(texto)
    if not haystack:
        return (None, False)

    hora_match = _HORA_SUELTA.search(haystack)
    fecha_match = _FECHA_LARGA.search(haystack)

    if fecha_match:
        dia, mes_txt, anio = fecha_match.groups()
        mes = _MESES.get(mes_txt)
        if mes is not None:
            try:
                base = datetime(int(anio), mes, int(dia), tzinfo=UTC)
            except ValueError:
                return (None, False)
            if hora_match:
                horas, minutos = (int(parte) for parte in hora_match.groups())
                return (base + timedelta(hours=horas, minutes=minutos), False)
            return (base, True)

    if hora_match:
        horas, minutos = (int(parte) for parte in hora_match.groups())
        candidato = ahora.replace(
            hour=horas, minute=minutos, second=0, microsecond=0
        )
        if candidato > ahora:
            candidato -= timedelta(days=1)
        return (candidato, False)

    return (None, False)


def es_reciente(item: NewsItem, *, ahora: datetime, max_age_minutes: int) -> bool:
    """¿La noticia describe el presente?

    Tres reglas, una por cada calidad de fecha:

    * **Sin fecha** → pasa. Es la decisión menos mala, la misma que toma el
      worker de Instagram: procesar de más una nota vieja cuesta una llamada al
      modelo y el filtro por `external_id` la atrapa en la corrida siguiente,
      mientras que descartarla pierde un siniestro por un campo que el portal no
      llenó.
    * **Con hora** → la ventana normal, en minutos.
    * **Sólo con día** → se acepta hoy y ayer **en UTC**. La holgura de un día no
      es generosidad: Chile va cuatro horas por detrás de UTC, así que una nota
      publicada el martes por la tarde en Valparaíso puede estar fechada el
      martes mientras acá ya es miércoles. Comparar con `max_age_minutes` un dato
      cuya resolución es de 24 horas sería fingir una precisión que no existe.
    """
    if item.published_at is None:
        return True
    if item.resolucion_dia:
        dias = (ahora.date() - item.published_at.date()).days
        return -1 <= dias <= 1
    return (ahora - item.published_at) <= timedelta(minutes=max_age_minutes)


# =============================================================================
#  Camino 1 — RSS
# =============================================================================


def _entry_value(entry: Any, *nombres: str) -> str | None:
    """Primer campo no vacío de una entrada de feedparser.

    Las entradas de feedparser son `FeedParserDict`: se comportan como objeto y
    como diccionario, y qué camino funciona depende de la clave. Se prueban los
    dos en vez de elegir uno, porque `entry.summary` existe y `entry.description`
    a veces no, aunque el XML traiga `<description>`.
    """
    for nombre in nombres:
        valor = getattr(entry, nombre, None)
        if valor is None and isinstance(entry, dict):
            valor = entry.get(nombre)
        if valor and str(valor).strip():
            return str(valor)
    return None


def _entry_categorias(entry: Any) -> list[str]:
    """Etiquetas `<category>` de una entrada, en el orden en que vienen."""
    crudas = getattr(entry, "tags", None) or []
    etiquetas: list[str] = []
    for tag in crudas:
        termino = None
        if isinstance(tag, dict):
            termino = tag.get("term") or tag.get("label")
        else:
            termino = getattr(tag, "term", None) or getattr(tag, "label", None)
        if termino and str(termino).strip():
            etiquetas.append(str(termino).strip())
    return etiquetas


def _entry_timestamp(entry: Any) -> datetime | None:
    """Fecha de publicación de una entrada. Instante completo o None.

    Es deliberadamente una copia corta de `bomberos_10_4_worker.entry_timestamp`
    y no una importación: allá el desempaquetado explícito de `struct_time` está
    justificado en su propio comentario, y acá hace falta además el respaldo
    sobre `dc:date`, que es lo que emite un WordPress cuando le falta `pubDate`.
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is not None:
        try:
            year, month, day, hour, minute, second = parsed[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except (TypeError, ValueError):
            pass

    crudo = _entry_value(entry, "published", "updated", "created", "date")
    return parse_timestamp(crudo) if crudo else None


def parse_feed(cuerpo: str, portal: NewsPortal) -> list[NewsItem]:
    """Cuerpo de un feed RSS/Atom → noticias. Función pura, sin red.

    Se lee `<description>` y **no** `<content:encoded>`, aunque Sitio del Suceso
    publique los dos. El motivo está en el docstring de `NewsItem.texto`: el
    cuerpo entero le da al extractor tres direcciones y ninguna forma de elegir.

    Los dos campos vienen con HTML y entidades (`&#8230;` por los puntos
    suspensivos, `<p class="wp-block-paragraph">` por el editor de bloques), así
    que pasan por `strip_html` antes de tocar nada más. Sin eso, el texto que ve
    el modelo —y el que queda archivado en `raw_events.text`— llevaría marcado
    dentro.
    """
    parsed = feedparser.parse(cuerpo)
    noticias: list[NewsItem] = []

    for entry in parsed.entries:
        titular = strip_html(_entry_value(entry, "title") or "")
        link = (_entry_value(entry, "link") or "").strip()
        if not titular or not link:
            # Sin titular no hay nada que filtrar; sin enlace no hay identidad
            # estable y el delta fetching dejaría de funcionar para ese ítem.
            continue

        bajada = strip_html(_entry_value(entry, "summary", "description") or "")
        categorias = _entry_categorias(entry)

        noticias.append(
            NewsItem(
                portal=portal.slug,
                portal_nombre=portal.nombre,
                titular=titular,
                bajada=bajada,
                link=urljoin(portal.base_url or link, link),
                guid=_entry_value(entry, "id", "guid"),
                published_at=_entry_timestamp(entry),
                resolucion_dia=False,
                comuna_hint=comuna_en_categorias(categorias),
                origen="rss",
                raw={
                    "categorias": categorias,
                    "autor": _entry_value(entry, "author"),
                    "origen": "rss",
                },
            )
        )
    return noticias


# =============================================================================
#  Camino 2 — HTML
# =============================================================================
#
# La misma estrategia de dos etapas que el worker del MTT, por la misma razón:
# los selectores CSS de un CMS se renumeran en cada rediseño y el vocabulario de
# una emergencia no.
#
#   1. **Estructura** — se recogen bloques candidatos por etiqueta y clase. Red
#      amplia, trae basura.
#   2. **Contenido** — sobrevive el bloque que tiene un titular con un enlace.
#
# Lo que hace distinto a este caso, y que costó descubrir mirando el DOM real de
# Pura Noticia: **un `<article>` puede ser un anuncio**. La página sirve sus
# slots de Google Ads envueltos en `<article class="relative content">`, la misma
# clase que usan las tarjetas de noticias. Un barrido ingenuo por `article`
# devuelve veintidós bloques de los cuales varios son iframes de publicidad, sin
# titular y sin enlace propio. De ahí que la etapa 2 exija titular Y enlace, y no
# sólo longitud de texto.

#: Contenedores donde estos portales publican sus tarjetas, de lo específico a lo
#: genérico. `article` es el que funciona hoy en Pura Noticia (verificado: 22
#: elementos en la sección regional); los demás son el respaldo para un rediseño
#: y para el WordPress de Sitio del Suceso si alguna vez hay que raspar su
#: portada.
_SELECTORES_BLOQUE: tuple[str, ...] = (
    "article",
    "div.post",
    "div.entry",
    "li.post",
    "div.card",
)

#: Etiquetas cuyo texto no es contenido. `iframe` y `noscript` están por los
#: anuncios; `script` y `style` porque BeautifulSoup los devuelve en `get_text`.
_ETIQUETAS_MUDAS = frozenset({"script", "style", "noscript", "iframe", "svg"})

#: Un titular más corto que esto es una etiqueta de sección ("Policial"), no un
#: titular. Más largo que el tope, es la página entera capturada por un
#: contenedor padre.
_MIN_TITULAR_CHARS = 25
_MAX_TITULAR_CHARS = 300


def _texto_visible(nodo: Tag) -> str:
    """Texto de un nodo saltándose lo que no es contenido. Ver `_ETIQUETAS_MUDAS`.

    Se recorren las cadenas en vez de arrancar los nodos del árbol porque el
    árbol es compartido —el mismo `soup` alimenta varias pasadas— y mutarlo a
    mitad de camino haría que el resultado dependiera del orden de las llamadas.
    Es la misma decisión, y por el mismo motivo, que `_block_text` en el worker
    del MTT.
    """
    partes = [
        str(cadena)
        for cadena in nodo.find_all(string=True)
        if cadena.parent is None or cadena.parent.name not in _ETIQUETAS_MUDAS
    ]
    return " ".join(" ".join(partes).split())


def _titular_de(bloque: Tag) -> str:
    """Titular de una tarjeta: el encabezado, o el `title=` del enlace.

    El respaldo no es cosmético. Varias maquetas truncan el encabezado por CSS
    pero dejan el titular completo en el atributo `title` del `<a>` —Pura Noticia
    lo hace— y ese texto completo es el que contiene el lugar del hecho, que es
    justamente lo que el geocodificador necesita.
    """
    for etiqueta in ("h1", "h2", "h3", "h4"):
        encabezado = bloque.find(etiqueta)
        if encabezado is not None:
            texto = _texto_visible(encabezado)
            if len(texto) >= _MIN_TITULAR_CHARS:
                return texto[:_MAX_TITULAR_CHARS]

    enlace = bloque.find("a", href=True)
    if enlace is not None:
        titulo = str(enlace.get("title") or "").strip()
        if len(titulo) >= _MIN_TITULAR_CHARS:
            return " ".join(titulo.split())[:_MAX_TITULAR_CHARS]
    return ""


def _bajada_de(bloque: Tag) -> str:
    """Primer `<p>` con contenido de la tarjeta. Cadena vacía si no hay.

    Que devuelva vacío es el caso **normal**, no el excepcional: las tarjetas de
    Pura Noticia son enlace + imagen + `<h3>` + fecha, sin un solo párrafo. Ver
    el apartado de limitaciones en el encabezado del módulo.
    """
    for parrafo in bloque.find_all("p"):
        texto = _texto_visible(parrafo)
        if len(texto) >= _MIN_TITULAR_CHARS:
            return texto[:1000]
    return ""


def _bloques_candidatos(soup: BeautifulSoup) -> Iterator[Tag]:
    for selector in _SELECTORES_BLOQUE:
        yield from soup.select(selector)


def parse_portada(
    html: str, portal: NewsPortal, *, ahora: datetime
) -> list[NewsItem]:
    """HTML de una portada → noticias. Función pura, sin red.

    Devuelve lista vacía si la página no trae ningún bloque reconocible. Esa
    ambigüedad —¿no hay noticias o cambió el DOM?— la resuelve
    `portada_parece_rota`, que se consulta aparte y por separado.

    La deduplicación es **por enlace** y no por contención de texto como en el
    worker del MTT. Acá el problema es otro: Pura Noticia repite la misma nota en
    varios carruseles de la misma página (destacados, últimas, región), así que
    los bloques son hermanos con el mismo `href`, no padres e hijos. El enlace es
    la identidad exacta y compararlo no depende de ninguna heurística.

    Y no es un detalle de limpieza: sin esto, una misma noticia entraría tres
    veces al sistema con tres `external_id` iguales —la base las colapsa— pero
    pagando tres extracciones y tres geocodificaciones antes de que la base las
    colapse.
    """
    soup = BeautifulSoup(html, _html_parser())
    vistos: set[str] = set()
    noticias: list[NewsItem] = []

    for bloque in _bloques_candidatos(soup):
        enlace = bloque.find("a", href=True)
        if enlace is None:
            continue
        href = str(enlace.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        titular = _titular_de(bloque)
        if not titular:
            # Sin titular no hay hecho que filtrar. Es el caso de los slots de
            # publicidad envueltos en `<article>`: ver el bloque de arriba.
            continue

        link = urljoin(portal.base_url or href, href)
        if link in vistos:
            continue
        vistos.add(link)

        bajada = _bajada_de(bloque)
        publicado, resolucion_dia = parse_fecha_es(
            _texto_visible(bloque), ahora=ahora
        )

        noticias.append(
            NewsItem(
                portal=portal.slug,
                portal_nombre=portal.nombre,
                titular=titular,
                bajada=bajada,
                link=link,
                guid=None,
                published_at=publicado,
                resolucion_dia=resolucion_dia,
                comuna_hint=comuna_en_texto(f"{titular} {bajada}"),
                origen="html",
                raw={"origen": "html", "tiene_bajada": bool(bajada)},
            )
        )
    return noticias


def portada_parece_rota(html: str) -> tuple[bool, str | None]:
    """¿La portada cambió de estructura? Devuelve `(rota, motivo)`.

    Distingue lo que un `len(noticias) == 0` confunde: un portal que hoy no
    publicó nada de la V Región —raro pero posible— de un rediseño que dejó el
    scraper ciego. Se mira si **existen** los contenedores; que no contengan
    titulares es información legítima, que no existan es una alarma.
    """
    soup = BeautifulSoup(html, _html_parser())
    if not soup.find("body"):
        return (True, "la respuesta no parece HTML")
    if sum(1 for _ in _bloques_candidatos(soup)) == 0:
        return (
            True,
            "no se encontró ningún bloque <article> ni contenedor de tarjeta",
        )
    return (False, None)


def _html_parser() -> str:
    """`lxml` si está disponible; si no, el parser de la stdlib.

    Mismo criterio que en el worker del MTT: lxml es mucho más indulgente con el
    HTML roto que produce un CMS con plugins, pero degradar a `html.parser` es
    preferible a que el collector no arranque.
    """
    try:
        import lxml  # noqa: F401
    except ImportError:  # pragma: no cover — lxml está en requirements-prod
        return "html.parser"
    return "lxml"


# =============================================================================
#  Acople al geocodificador LLM
# =============================================================================


async def geocode_noticia(
    texto: str, *, comuna_hint: str | None, geo_client: Any
) -> tuple[dict[str, Any], GeocodeResult | None]:
    """Texto libre → `({street_1, street_2, city}, punto|None)`.

    Es el mismo pipeline de dos pasos que usan el worker del MTT y el de
    Instagram —`extract_streets_via_llm` y después `nominatim.geocode`— con una
    diferencia que justifica no reutilizar `geocode_text` tal cual: **la comuna
    entra entre los dos pasos**.

    Por qué ahí y no antes ni después. La comuna que trae `<category>` es la
    fuente diciendo dónde ocurrió, y es gratis; pero el modelo leyó el texto y la
    fuente sólo etiquetó la nota, así que cuando los dos opinan gana el modelo.
    Sólo se rellena el hueco. Ponerla antes contaminaría el prompt con un dato
    que el modelo podría repetir en vez de leer; ponerla después obligaría a una
    segunda llamada a Nominatim para reintentar, que es justo lo que el límite de
    1 req/s hace caro.

    Lo que gana: "Colisión frontal entre dos vehículos" sin comuna geocodifica a
    cualquier parte de Chile; con `city="San Felipe"` cae donde debe. La mejora
    no cuesta un token.

    Devuelve las **dos** piezas —extracción y punto— y no sólo lat/lon, por la
    misma razón que allá: cuando mañana un punto esté mal, hay que poder
    distinguir si el modelo leyó mal la calle o si Nominatim resolvió esa calle a
    otra comuna. Van separadas a `raw_data._extraction` y `raw_data._geocoding`.
    """
    payload = " ".join(str(texto or "").split())[: gemini.MAX_INPUT_CHARS]
    if not payload:
        return ({}, None)

    streets = await extract_streets_via_llm(payload)
    if not streets or not streets.get("street_1"):
        return ({}, None)

    if comuna_hint and not (streets.get("city") or "").strip():
        streets = {**streets, "city": comuna_hint, "city_origen": "categoria"}

    point = await geocode(geo_client, streets)
    return (streets, point)


# =============================================================================
#  Collector
# =============================================================================


@dataclass(frozen=True, slots=True)
class ResolvedNews:
    """Lo que `fetch()` entrega a `normalize()`: noticia, tipo, extracción, punto."""

    item: NewsItem
    event_type: EventType
    streets: dict[str, Any]
    point: GeocodeResult | None


@dataclass(slots=True)
class _LecturaPortal:
    """Resultado de leer un portal. Alimenta la traza y las advertencias."""

    portal: NewsPortal
    noticias: list[NewsItem]
    via: str | None = None
    error: str | None = None
    aviso: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class LocalNewsCollector(BaseCollector):
    """Prensa local de la V Región, raspada sin intermediarios.

    Comparte con `TransporteInformaCollector` y con el de Instagram la ruptura de
    convención que allá está explicada: la geocodificación ocurre en `fetch()` y
    no en `normalize()`, para que `normalize()` siga siendo pura y testeable sin
    red.

    Y comparte con ellos una restricción operativa que ya son **tres** en
    compartirla, y conviene decirlo antes de que llegue la factura: el LLM y
    Nominatim son presupuestos comunes. `GEMINI_MAX_CALLS_PER_RUN` acota una
    corrida, no el día, y el limitador de Nominatim es global al proceso. Con el
    MTT cada 10 minutos, Instagram cada 5 y este cada 15, el gasto diario es la
    suma de las tres cadencias por sus topes respectivos. Es el número a mirar,
    y por eso `LOCAL_NEWS_MAX_GEOCODES` es el más bajo de los tres: una noticia
    llega minutos u horas después que el aviso del MTT o el post de la cuenta
    hiperlocal, así que si hay que recortar algo en una corrida saturada, que sea
    esto.
    """

    name = "prensa_local"
    #: `MEDIA` y no un valor nuevo por portal. `EventSource` es un ENUM de
    #: PostgreSQL: agregar `SITIO_DEL_SUCESO` obliga a una migración con
    #: `ALTER TYPE`, a tocar `SOURCE_BASE_CONFIDENCE`, `RULES` y el frontend, y
    #: todo para distinguir algo que ya se distingue por `raw_data._prensa.portal`
    #: y por el propio `external_id`. Lo que sí importa es que los dos portales
    #: son señales **independientes** entre sí, y eso se conserva: ver
    #: `external_id_for`.
    source = EventSource.MEDIA
    default_interval_seconds = 900

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.LOCAL_NEWS_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        try:
            self.portales = parse_portals(settings.LOCAL_NEWS_SOURCES)
        except ValueError as exc:
            # Configuración mal escrita: el constructor lanza, el runner deja una
            # corrida `failed` en `collector_runs` y el problema se ve. Un portal
            # que deja de consultarse en silencio no se descubre nunca.
            raise CollectorError(
                f"LOCAL_NEWS_SOURCES está mal declarada: {exc}"
            ) from exc

        if not self.portales:
            raise CollectorError(
                "LOCAL_NEWS_SOURCES no declara ningún portal; el collector no "
                "tiene de dónde leer."
            )

        self.max_items = settings.LOCAL_NEWS_MAX_ITEMS_PER_PORTAL
        self.max_age = settings.LOCAL_NEWS_MAX_AGE_MINUTES
        self.max_geocodes = settings.LOCAL_NEWS_MAX_GEOCODES
        self.max_llm_calls = settings.GEMINI_MAX_CALLS_PER_RUN
        self.confidence = settings.LOCAL_NEWS_CONFIDENCE
        self.timeout = settings.LOCAL_NEWS_TIMEOUT_SECONDS

        if not gemini.is_configured():
            logger.warning(
                "GEMINI_API_KEY no está configurada: la extracción de calles "
                "usará la heurística de reglas",
                extra={"collector": self.name},
            )

    def run_params(self) -> dict[str, Any]:
        return {
            "portales": [
                {
                    "slug": portal.slug,
                    "feed": portal.feed_url,
                    "portada": portal.portada_url,
                }
                for portal in self.portales
            ],
            "max_items_por_portal": self.max_items,
            "max_age_min": self.max_age,
            "max_geocodes": self.max_geocodes,
            "max_llm_calls": self.max_llm_calls,
            "extraction_mode": (
                gemini.MODE_GEMINI if gemini.is_configured() else gemini.MODE_HEURISTIC
            ),
        }

    # -- Pre-filtro de relevancia --------------------------------------------

    @staticmethod
    def _is_emergency(texto: str) -> bool:
        """Guardián del gasto: lo que no pasa acá no llega al modelo.

        `staticmethod` por la convención del proyecto: las piezas puras se
        testean sin instanciar el collector, sin sesión y sin configuración. La
        lógica vive en `es_emergencia`, a nivel de módulo.
        """
        return es_emergencia(texto)

    # -- Transporte -----------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        """Cliente HTTP con cabeceras de navegador.

        El `User-Agent` por defecto de httpx (`python-httpx/0.28.1`) es lo primero
        que mira una regla básica de Cloudflare o de un plugin de seguridad de
        WordPress, y la respuesta es un 403 o un desafío servido con HTTP 200.
        Con cabeceras de navegador el problema desaparece para el nivel de
        protección que tienen estos dos portales.

        Tres precisiones, porque esto merece decirse en voz alta y no esconderse
        en una constante:

        * **No es evasión.** Un desafío JavaScript de verdad seguiría fallando y
          este collector no intenta resolverlo. Si algún día lo activan, lo
          correcto es dejar de raspar el portal, no perseguirlo.
        * **Es distinto del `User-Agent` de Nominatim**, que exige identificarse
          con un contacto real y rechaza las peticiones anónimas. Dos servicios
          con contratos opuestos, dos clientes, dos cabeceras. Mezclarlos rompía
          uno de los dos.
        * **`Accept-Language: es-CL`** pesa tanto como el `User-Agent`: varios
          CMS sirven contenido distinto —o un muro— a un cliente que no declara
          idioma.
        """
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.LOCAL_NEWS_USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/rss+xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "es-CL,es;q=0.9",
            },
        )

    async def _leer_portal(
        self, client: httpx.AsyncClient, portal: NewsPortal, *, ahora: datetime
    ) -> _LecturaPortal:
        """Lee un portal por RSS, por HTML, o por RSS con HTML de respaldo.

        **Nada escapa de este método.** Es la pieza que cumple el requisito de
        que un portal caído no detenga la corrida, y la forma de cumplirlo no es
        tragarse el error: es capturarlo, atribuirlo al portal que lo produjo y
        devolverlo para que `fetch()` decida. La diferencia importa — un
        `except: pass` acá haría que dos portales muertos se vieran igual que dos
        portales sin noticias.
        """
        lectura = _LecturaPortal(portal=portal, noticias=[])
        errores: list[str] = []

        if portal.feed_url:
            try:
                cuerpo = await request_text(
                    client, portal.feed_url, origin=portal.label
                )
                estado = revisar_feed(cuerpo)
                if estado.roto:
                    errores.append(f"feed ilegible ({estado.motivo})")
                elif not estado.entradas:
                    # Feed válido y vacío. Es el fallo silencioso que documenta el
                    # worker de Bomberos: pasa las dos comprobaciones anteriores y
                    # produce cero noticias, igual que una madrugada tranquila. Un
                    # diario regional no pasa horas sin publicar.
                    errores.append("el feed es válido pero no trae ninguna entrada")
                else:
                    lectura.noticias = parse_feed(cuerpo, portal)
                    lectura.via = "rss"
            except CollectorError as exc:
                errores.append(exc.message)
            except Exception as exc:
                errores.append(f"{type(exc).__name__}: {exc}")

        necesita_respaldo = not lectura.noticias and portal.portada_url is not None
        if necesita_respaldo:
            try:
                html = await request_text(
                    client, portal.portada_url or "", origin=portal.label
                )
                rota, motivo = portada_parece_rota(html)
                if rota:
                    errores.append(f"la portada cambió de estructura: {motivo}")
                lectura.noticias = parse_portada(html, portal, ahora=ahora)
                lectura.via = "html"
            except CollectorError as exc:
                errores.append(exc.message)
            except Exception as exc:
                errores.append(f"{type(exc).__name__}: {exc}")

        if lectura.noticias:
            # Se leyó algo. Si hubo tropiezos por el camino —el feed falló y
            # salvó la portada— eso es una degradación, no un fallo: la corrida
            # sigue y queda `partial` con el motivo a la vista.
            if errores:
                lectura.aviso = (
                    f"{portal.nombre}: se leyó por {lectura.via} tras fallar el "
                    f"camino preferido ({'; '.join(errores)})"
                )
            return lectura

        lectura.error = (
            "; ".join(errores) if errores else "no se obtuvo ninguna noticia"
        )
        return lectura

    # -- Delta fetching -------------------------------------------------------

    async def unseen(self, items: Sequence[NewsItem]) -> list[NewsItem]:
        """Descarta las noticias ya procesadas. **Una** consulta para todo el lote.

        Es el requisito central de esta fuente y el que más dinero ahorra. Una
        noticia queda publicada en la portada **durante días**: sin este filtro,
        cada corrida —cuatro por hora— mandaría las mismas cuarenta notas a Gemini
        y a Nominatim para que la base las descarte después, en silencio y con la
        factura ya emitida. El upsert por `uq_raw_events_source_external_id` ya
        evita el duplicado; esto evita **pagarlo**.

        Tres capas, cada una más cara que la anterior:

        1. `es_reciente` — puro, sin I/O.
        2. esto — un `SELECT` acotado por el índice único, sobre un puñado de ids.
        3. el índice único — la red de seguridad, si las dos anteriores fallan.

        Se descartó guardar el estado **en memoria**, que era la otra opción del
        encargo, y por un motivo concreto y no estilístico: el runner construye
        un collector nuevo en cada corrida (`get_collector` dentro de
        `run_collector`), así que un `set` de instancia se vaciaría entre
        corridas y no filtraría nada. Un caché a nivel de módulo sí sobreviviría,
        pero se perdería en cada despliegue y en cada reinicio del contenedor —y
        justo después de un reinicio es cuando toda la portada parece nueva y el
        gasto se dispara. `raw_events` ya tiene ese estado, es auditable y no se
        puede desincronizar; una segunda copia sólo agrega una forma de mentir.
        """
        if not items:
            return []

        ids = [external_id_for(item) for item in items]
        conocidos = await self.service.repo.ids_by_external_id(self.source, ids)
        return [item for item in items if external_id_for(item) not in conocidos]

    # -- Orquestación ---------------------------------------------------------

    async def fetch(self) -> Sequence[ResolvedNews]:
        """Lee los portales, filtra y geocodifica lo que sobrevive.

        Sólo se lanza `CollectorError` si **todos** los portales fallaron. Que
        uno se caiga es una degradación con nombre y apellido en
        `collector_runs`; que se caigan los dos es una fuente muerta y tiene que
        verse en rojo, porque una corrida `success` con cero noticias y una
        fuente caída se ven idénticas desde el tablero.
        """
        ahora = datetime.now(UTC)
        lecturas: list[_LecturaPortal] = []

        async with self._client() as client:
            for portal in self.portales:
                lecturas.append(
                    await self._leer_portal(client, portal, ahora=ahora)
                )

        for lectura in lecturas:
            if lectura.aviso:
                self.warn(lectura.aviso)
            elif lectura.error:
                self.warn(f"{lectura.portal.nombre}: {lectura.error}")

        if not any(lectura.ok for lectura in lecturas):
            raise CollectorError(
                "ningún portal de prensa respondió",
                detail={
                    "portales": [
                        {"portal": lectura.portal.slug, "error": lectura.error}
                        for lectura in lecturas
                    ]
                },
            )

        noticias: list[NewsItem] = []
        for lectura in lecturas:
            noticias.extend(lectura.noticias[: self.max_items])

        frescas = [
            item
            for item in noticias
            if es_reciente(item, ahora=ahora, max_age_minutes=self.max_age)
        ]

        # Pre-filtro y clasificación ANTES del delta y ANTES del modelo: los dos
        # son síncronos, en memoria y sin red, y descartan la mayor parte del
        # lote. Un diario publica política municipal, deportes y tribunales entre
        # las emergencias, y en mucha mayor proporción que una cuenta de alertas.
        candidatas: list[tuple[NewsItem, EventType]] = []
        ignoradas = 0
        for item in frescas:
            if not self._is_emergency(item.texto):
                ignoradas += 1
                logger.debug(
                    "Noticia ignorada: no contiene lenguaje de emergencia",
                    extra={
                        "collector": self.name,
                        "external_id": external_id_for(item),
                        "muestra": item.titular[:120],
                    },
                )
                continue

            event_type = clasificar_noticia(item.texto)
            if event_type is not None:
                candidatas.append((item, event_type))

        # UNA consulta para los dos portales juntos, no una por portal: el delta
        # fetching deja de ser una optimización si se paga con N+1 consultas.
        nuevas = await self.unseen([item for item, _ in candidatas])
        nuevos_ids = {external_id_for(item) for item in nuevas}
        pendientes = [
            (item, tipo)
            for item, tipo in candidatas
            if external_id_for(item) in nuevos_ids
        ]

        resueltas = await self._resolve(pendientes)

        logger.info(
            "noticias de prensa local procesadas",
            extra={
                "collector": self.name,
                "portales_ok": sum(1 for lectura in lecturas if lectura.ok),
                "noticias": len(noticias),
                "frescas": len(frescas),
                # `ignoradas` es la métrica del pre-filtro. Si se va a cero, el
                # diccionario dejó de filtrar y se está pagando el modelo de más;
                # si se lleva todo, se perdió cobertura.
                "ignoradas_prefiltro": ignoradas,
                "emergencias": len(candidatas),
                "nuevas": len(pendientes),
                "geocodificadas": sum(1 for r in resueltas if r.point is not None),
            },
        )
        return resueltas

    async def _resolve(
        self, pendientes: Sequence[tuple[NewsItem, EventType]]
    ) -> list[ResolvedNews]:
        """Pasa por el geocodificador LLM lo que sobrevivió a los filtros."""
        if not pendientes:
            return []

        resueltas: list[ResolvedNews] = []
        llm_calls = 0
        geocodes = 0

        # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
        # conexión TLS y mantiene un único User-Agent identificable, que es parte
        # del contrato de uso del servicio.
        async with build_geo_client() as geo_client:
            for item, event_type in pendientes:
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
                        streets, point = await geocode_noticia(
                            item.texto,
                            comuna_hint=item.comuna_hint,
                            geo_client=geo_client,
                        )
                    except Exception as exc:
                        # Un fallo de Nominatim NO pierde la noticia, y sobre todo
                        # no pierde las otras del lote: por eso la captura es de
                        # `Exception` y no sólo de `CollectorError`.
                        self.warn(
                            f"la geocodificación falló para una noticia "
                            f"({type(exc).__name__}): {exc}"
                        )

                resueltas.append(
                    ResolvedNews(
                        item=item,
                        event_type=event_type,
                        streets=streets,
                        point=point,
                    )
                )

        return resueltas

    def normalize(self, records: Sequence[ResolvedNews]) -> list[EventCreate]:
        """`ResolvedNews` → `EventCreate`. Pura: sin red y sin base."""
        ahora = datetime.now(UTC)
        eventos: list[EventCreate] = []
        sin_punto = 0
        sin_hora = 0

        for registro in records:
            item = registro.item
            timestamp = self._timestamp_de(item, ahora=ahora)
            if item.published_at is None or item.resolucion_dia:
                sin_hora += 1
            if registro.point is None:
                sin_punto += 1

            eventos.append(
                EventCreate(
                    timestamp=timestamp,
                    source=self.source,
                    type=registro.event_type,
                    lat=registro.point.lat if registro.point else None,
                    lon=registro.point.lon if registro.point else None,
                    text=item.texto[:10_000],
                    external_id=external_id_for(item),
                    confidence=self.confidence,
                    raw_data={
                        "titular": item.titular,
                        "bajada": item.bajada or None,
                        "url": item.link,
                        "comuna": (
                            registro.streets.get("city") or item.comuna_hint
                        ),
                        "_collector": self.name,
                        "_prensa": {
                            "portal": item.portal,
                            "medio": item.portal_nombre,
                            "origen": item.origen,
                            "guid": item.guid,
                            "tiene_bajada": bool(item.bajada),
                            "comuna_declarada": item.comuna_hint,
                            "fecha_declarada": (
                                item.published_at.isoformat()
                                if item.published_at
                                else None
                            ),
                            "resolucion_dia": item.resolucion_dia,
                            **{
                                clave: valor
                                for clave, valor in item.raw.items()
                                if clave != "origen"
                            },
                        },
                        "_extraction": {
                            **registro.streets,
                            "mode": (
                                gemini.MODE_GEMINI
                                if gemini.is_configured()
                                else gemini.MODE_HEURISTIC
                            ),
                        },
                        "_geocoding": (
                            registro.point.as_dict() if registro.point else None
                        ),
                    },
                )
            )

        if sin_punto:
            self.warn(
                f"{sin_punto} noticias quedaron sin coordenadas; no entran al "
                f"Paso A del motor pero sí quedan registradas"
            )
        if sin_hora:
            self.warn(
                f"{sin_hora} noticias sin hora de publicación; se estampó la "
                f"cota superior conocida (ver `_timestamp_de`)"
            )
        return eventos

    @staticmethod
    def _timestamp_de(item: NewsItem, *, ahora: datetime) -> datetime:
        """Cuándo ocurrió, sin fingir una precisión que la fuente no dio.

        Tres casos, y el tercero es el que importa:

        * **Con hora** → esa hora, recortada si el reloj de la fuente va
          adelantado (`EventCreate` rechaza el futuro y perderíamos la señal
          entera por un desfase de segundos).
        * **Sin fecha** → la hora de la corrida. El delta fetching garantiza que
          una noticia se procesa **una sola vez, la primera que se ve**, así que
          la hora de primera lectura es una cota superior de la publicación con
          error acotado por la cadencia: quince minutos.
        * **Sólo con día** → el final de ese día, recortado a `ahora`. Para una
          nota de hoy eso da la hora de la corrida, que es la misma cota superior
          del caso anterior. Para una de ayer da las 23:59 de ayer, que la deja
          fuera de la ventana de correlación de 4 h: queda registrada y
          consultable, pero no se funde con lo que está pasando ahora.

        Ese último caso es el que evita el peor error posible de esta fuente.
        Estampar `ahora` en una nota fechada ayer pondría una emergencia de hace
        veinte horas sobre el mapa como si acabara de ocurrir, y le daría además
        el 0.60 de un medio para corroborar un incidente con el que no tiene
        nada que ver. Nadie lo notaría: el punto se vería perfectamente normal.
        """
        if item.published_at is None:
            return ahora
        if item.resolucion_dia:
            fin_del_dia = item.published_at + timedelta(hours=23, minutes=59)
            return min(fin_del_dia, ahora)
        return min(item.published_at, ahora)


__all__ = [
    "HEADLINE_VERBS",
    "PRESS_NOISE_PHRASES",
    "LocalNewsCollector",
    "NewsItem",
    "NewsPortal",
    "ResolvedNews",
    "clasificar_noticia",
    "comuna_en_categorias",
    "comuna_en_texto",
    "es_emergencia",
    "es_reciente",
    "external_id_for",
    "geocode_noticia",
    "parse_fecha_es",
    "parse_feed",
    "parse_portada",
    "parse_portals",
    "portada_parece_rota",
]
