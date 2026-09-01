"""Despachos de Bomberos — clave 10-4, rescate vehicular, vía feed RSS.

Qué es una clave 10-4 dentro de este sistema
---------------------------------------------
La confirmación más fuerte que puede recibir la capa de accidentes. Una 10-4 no
es un aviso de que "puede haber" un choque: es la central despachando carros
porque hay gente atrapada en un vehículo. Alguien con autoridad ya decidió que
el hecho es real y comprometió recursos.

Por eso `EventSource.BOMBEROS` es `confirming=True` con peso 1.00 en
`confidence.py`, y una sola 10-4 lleva el incidente a certeza.

De dónde sale el dato
---------------------
De la cuenta pública de la central del Cuerpo de Bomberos, leída como RSS a
través de un puente tipo RSSHub. No es una API: es el mismo texto que un
bombero escribe para que lo lean personas, servido en XML. Las consecuencias
están asumidas de frente:

* **Es un intermediario, y se cae.** `rsshub.app` es una instancia pública,
  compartida y sin SLA; devuelve 429 y 503 con frecuencia. Peor: la ruta de
  Twitter de RSSHub **dejó de existir** cuando X cerró su API, y hoy el default
  de `BOMBEROS_DISPATCH_URL` redirige a un 404 de Google. Cualquier despliegue
  que dependa de ese puente necesita otro camino.
* **Tres respuestas, no dos.** Este módulo empezó distinguiendo "feed válido sin
  novedades" de "esto no es un feed", y esa distinción dejaba pasar la peor de
  las tres: un feed **válido y vacío**. Es RSS de verdad, el XML está bien, y
  produce cero despachos igual que una madrugada tranquila — así que la corrida
  salía `success` mientras la fuente llevaba días muerta. Un tablero que declara
  sana una fuente caída miente peor que uno que se rompe. Ver `EstadoFeed`.
* **El texto es prosa, no campos.** No hay `<address>` ni `<code>`: hay una
  frase. Lo que se guarda como dirección es el texto del aviso completo, en
  `raw_data`, sin fingir una precisión que no tiene.
* **No se geocodifica.** Ver el apartado siguiente.

Por qué este worker no entrega coordenadas
-------------------------------------------
Geocodificar cada despacho metería una llamada de red por aviso dentro del rate
limit de 1 req/s de Nominatim, y una 10-4 vale por su certeza sobre el hecho, no
por la precisión del punto.

Consecuencia, escrita para que nadie la descubra depurando: una señal sin
`lat`/`lon` **no entra al Paso A** —el motor sólo agrupa lo que tiene geometría—
pero sí queda registrada, consultable y contabilizada. El emparejamiento por
texto con los accidentes de Waze, si se necesita, corresponde al Paso B.

Idempotencia
------------
El feed entrega `<guid>` o `<link>` por ítem; se usa como `external_id`. Cuando
falta —RSSHub no siempre lo emite— se cae a un hash determinista del texto y la
fecha. Releer el feed cada 3 minutos actualiza la fila en vez de duplicar el
despacho.
"""

from __future__ import annotations

import hashlib
import html as html_module
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import parse_timestamp, request_text
from app.collectors.vocabulary import find_codes, matches_key, normalise_code
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Certeza institucional: la central despachó carros. Coincide con
#: SOURCE_BASE_CONFIDENCE[BOMBEROS] y con el peso 1.0 pedido para esta capa.
BOMBEROS_CONFIDENCE = 1.0

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

# =============================================================================
#  Reconocimiento de la clave
# =============================================================================
#
# `normalise_code`, `find_codes` y `matches_key` **vivían acá** y hoy viven en
# `app/collectors/vocabulary.py`. Se importan y se re-exportan: son parte de la
# superficie pública de este worker desde antes de la extracción, y romper esos
# nombres obligaría a tocar los tests de tránsito para nada.
#
# El motivo del traslado está escrito completo en el módulo nuevo, junto con la
# explicación de por qué el reconocimiento son dos pasos triviales y no una sola
# regex ingeniosa. En una frase: una clave radial es vocabulario del Sistema
# Nacional, no un detalle de este feed, y cuando el segundo y el tercer collector
# la necesitaron, este archivo dejó de ser su lugar.
#
# Lo que NO cambió: la comparación sigue siendo por prefijo de tupla, `10-4-1`
# sigue respondiendo a `10-4`, y `10-40` sigue sin hacerlo.


# =============================================================================
#  Lectura del feed
# =============================================================================


@dataclass(frozen=True, slots=True)
class Dispatch:
    """Un despacho ya extraído de un `<item>` del feed."""

    key: str
    address: str | None
    occurred_at: datetime | None
    commune: str | None
    raw_text: str
    guid: str | None = None


def strip_html(fragment: str) -> str:
    """HTML/entidades → texto plano normalizado en espacios.

    Los feeds de RSSHub traen la descripción con marcado y entidades
    (`&amp;`, `&#39;`) porque el puente reempaqueta HTML dentro del XML.
    """
    without_tags = _TAG_PATTERN.sub(" ", fragment)
    return _WHITESPACE.sub(" ", html_module.unescape(without_tags)).strip()


def entry_text(entry: Any) -> str:
    """Título y descripción de un ítem, concatenados y limpios.

    Se miran los dos campos porque el puente no es consistente: a veces el texto
    completo está en `<title>` y `<description>` lo repite, y a veces el título
    va truncado con puntos suspensivos. Quedarse con uno solo perdería avisos.
    """
    parts: list[str] = []
    for field_name in ("title", "summary", "description"):
        value = getattr(entry, field_name, None) or (
            entry.get(field_name) if isinstance(entry, dict) else None
        )
        if value:
            cleaned = strip_html(str(value))
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
    return " ".join(parts)


def entry_timestamp(entry: Any) -> datetime | None:
    """Fecha de publicación del ítem.

    `feedparser` ya resuelve `pubDate` a un `struct_time` en UTC. Se prefiere ese
    camino y se cae al texto crudo sólo si el parser no pudo.
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed is not None:
        try:
            # Desempaquetado explícito y no `datetime(*parsed[:6], tzinfo=UTC)`:
            # el segundo es más corto pero mypy no puede probar que la tupla
            # tenga exactamente seis elementos, y con razón — `struct_time`
            # también puede llegar truncada desde un feed raro, y ahí el error
            # sería un `TypeError` en producción en vez de un aviso al compilar.
            year, month, day, hour, minute, second = parsed[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except (TypeError, ValueError):
            pass

    for field_name in ("published", "updated"):
        raw = getattr(entry, field_name, None)
        if raw:
            resolved = parse_timestamp(raw)
            if resolved is not None:
                return resolved
    return None


def entry_guid(entry: Any) -> str | None:
    for field_name in ("id", "guid", "link"):
        value = getattr(entry, field_name, None)
        if value and str(value).strip():
            return str(value).strip()
    return None


def parse_dispatches(feed_body: str, keys: Sequence[str]) -> list[Dispatch]:
    """Extrae de un feed RSS/Atom los despachos cuya clave coincide. Función pura.

    Separada de la clase para poder testearla contra un feed real guardado, que
    es la única forma honesta de verificar un parser de fuente ajena.

    `feedparser` es tolerante por diseño: ante XML mal formado devuelve lo que
    pudo leer y levanta `bozo`. Se aprovecha esa tolerancia —un feed a medias es
    mejor que ninguno— pero el llamador puede inspeccionar `bozo` para avisar.
    """
    parsed = feedparser.parse(feed_body)
    dispatches: list[Dispatch] = []

    for entry in parsed.entries:
        text = entry_text(entry)
        if not text:
            continue

        key = matches_key(text, keys)
        if key is None:
            continue

        dispatches.append(
            Dispatch(
                key=key,
                # El texto del aviso ES la dirección disponible. No se recorta ni
                # se intenta aislar la calle: cualquier heurística que lo hiciera
                # descartaría contexto que un operador sí sabe leer.
                address=text,
                occurred_at=entry_timestamp(entry),
                commune=None,
                raw_text=text[:2000],
                guid=entry_guid(entry),
            )
        )

    return dispatches


@dataclass(frozen=True, slots=True)
class EstadoFeed:
    """Qué llegó del puente, en las tres categorías que importan.

    Las dos primeras ya se distinguían; la tercera se añadió después de que una
    fuente muerta pasara días reportando corridas `success`.

    * `roto` — no es un feed: HTML de error, captcha, un 429 servido con estado
      200. Necesita a una persona.
    * `entradas == 0` con `roto=False` — el feed es válido y **no trae nada**.
      Sospechoso: la central de una región de dos millones de habitantes no pasa
      días sin publicar. Ver `Bomberos104Collector.fetch`.
    * `entradas > 0` — sano. Que ninguna sea una 10-4 es lo normal y no se avisa.
    """

    roto: bool
    motivo: str | None
    entradas: int


def revisar_feed(feed_body: str) -> EstadoFeed:
    """Clasifica la respuesta del puente. Ver `EstadoFeed`.

    El discriminador de "roto" es `parsed.version`, y esa elección tiene una
    historia corta: `bozo` no sirve para esto. feedparser es tan indulgente que
    digiere HTML sin quejarse —devuelve `bozo=False` y hasta rellena
    `feed.summary` con el texto de la página de error—, así que confiar en
    `bozo` dejaba pasar justo el caso que este chequeo existe para atrapar.
    `version` sólo trae valor (`rss20`, `atom10`…) cuando reconoció un formato
    de sindicación de verdad; ante HTML queda en cadena vacía.
    """
    parsed = feedparser.parse(feed_body)
    entradas = len(parsed.entries)

    if entradas:
        return EstadoFeed(roto=False, motivo=None, entradas=entradas)

    if not getattr(parsed, "version", ""):
        reason = getattr(parsed, "bozo_exception", None)
        detalle = f"{type(reason).__name__}: {reason}" if reason else "no es RSS ni Atom"
        return EstadoFeed(
            roto=True, motivo=f"la respuesta no es un feed ({detalle})", entradas=0
        )

    if getattr(parsed, "bozo", 0):
        reason = getattr(parsed, "bozo_exception", None)
        motivo = f"XML inválido ({reason})" if reason else "XML inválido"
        return EstadoFeed(roto=True, motivo=motivo, entradas=0)

    return EstadoFeed(roto=False, motivo=None, entradas=0)


def feed_is_broken(feed_body: str) -> tuple[bool, str | None]:
    """`(roto, motivo)` — la pregunta estrecha, sobre `revisar_feed`.

    Se conserva porque "¿es esto un feed?" es una pregunta legítima por sí sola
    y hay tests que la hacen. Lo que **no** responde es si el feed trae algo, y
    confundir las dos cosas es lo que dejó una fuente muerta reportando
    corridas exitosas durante días.
    """
    estado = revisar_feed(feed_body)
    return (estado.roto, estado.motivo)


def build_external_id(dispatch: Dispatch) -> str:
    """ID estable. Se prefiere el `guid` del feed; si falta, un hash del aviso.

    Cuando hay que hashear entran la clave, el texto y el instante: la terna que
    identifica un despacho. Sin la fecha, dos rescates distintos en la misma
    esquina el mismo día colapsarían en una sola fila.
    """
    if dispatch.guid:
        digest = hashlib.sha256(dispatch.guid.encode("utf-8")).hexdigest()[:24]
        return f"bomberos:{digest}"

    stamp = dispatch.occurred_at.isoformat() if dispatch.occurred_at else "sin-fecha"
    payload = f"{dispatch.key}|{dispatch.raw_text}|{stamp}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"bomberos:{digest}"


def build_text(dispatch: Dispatch) -> str:
    cuerpo = dispatch.address or ""
    return f"Clave {dispatch.key} (rescate vehicular): {cuerpo}".strip()


class Bomberos104Collector(BaseCollector):
    """Lector del feed RSS de despachos con clave de rescate vehicular."""

    name = "bomberos_10_4"
    source = EventSource.BOMBEROS
    default_interval_seconds = 180

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.BOMBEROS_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.BOMBEROS_DISPATCH_URL.strip():
            raise CollectorError(
                "BOMBEROS_DISPATCH_URL no está configurada; el collector no "
                "tiene de dónde leer."
            )
        self.url = settings.BOMBEROS_DISPATCH_URL.strip()
        self.keys = [key.strip() for key in settings.BOMBEROS_ACCIDENT_KEYS if key.strip()]
        if not self.keys:
            raise CollectorError("BOMBEROS_ACCIDENT_KEYS quedó vacía")

    def run_params(self) -> dict[str, Any]:
        return {"keys": self.keys, "url": self.url}

    async def fetch(self) -> Sequence[Dispatch]:
        """Lee el feed. Todo fallo de red sale como `CollectorError`.

        Nada escapa de acá sin convertirse: `request_text` ya traduce timeouts,
        5xx, DNS y TLS, y el `except Exception` final cubre lo que no anticipamos
        —un feedparser que reviente con un XML patológico, por ejemplo—. El
        contrato con el orquestador es que este método falla de UNA sola forma.
        """
        try:
            async with httpx.AsyncClient(
                timeout=settings.BOMBEROS_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            ) as client:
                body = await request_text(client, self.url, origin="bomberos")
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                f"bomberos: fallo inesperado al leer el feed: {type(exc).__name__}: {exc}",
                detail={"url": self.url},
            ) from exc

        try:
            estado = revisar_feed(body)
            dispatches = parse_dispatches(body, self.keys)
        except Exception as exc:
            raise CollectorError(
                f"bomberos: el feed no se pudo interpretar: {type(exc).__name__}: {exc}",
                detail={"url": self.url, "muestra": body[:200]},
            ) from exc

        if estado.roto:
            # Se avisa y se sigue: la corrida queda `partial`, con el motivo
            # visible en `collector_runs`. Un feed ilegible es un problema del
            # puente —RSSHub sirviendo una página de error con HTTP 200— y no
            # justifica perder lo poco que se haya podido leer.
            self.warn(f"el feed llegó sin ítems interpretables ({estado.motivo})")
        elif not estado.entradas:
            # El caso que costó días de silencio: un feed **válido** y **vacío**.
            #
            # Pasa las dos comprobaciones de arriba —es RSS de verdad, el XML
            # está bien— y produce cero despachos, exactamente igual que una
            # madrugada sin rescates. Sin este aviso, la corrida sale `success`
            # y el tablero declara sana una fuente que lleva días muerta. Es el
            # mismo modo de fallo silencioso que este proyecto persigue en todas
            # las capas, sólo que disfrazado de buena noticia.
            #
            # No se distingue "vacío una vez" de "vacío desde hace días": el
            # collector no tiene memoria entre corridas. Un aviso por corrida es
            # suficiente para que se vea en `collector_runs`, y quien mire dos
            # filas seguidas saca la conclusión.
            #
            # Ojo con la asimetría deliberada: que el feed traiga entradas y
            # ninguna sea una 10-4 **no** se avisa. Eso sí es una noche
            # tranquila, y avisarlo entrenaría a todo el mundo a ignorar el
            # aviso — que es como se pierde la señal que sí importa.
            self.warn(
                "el feed es válido pero no trae ninguna entrada; una central de "
                "despacho no pasa días en silencio, así que probablemente la "
                "fuente esté caída aunque responda"
            )

        return dispatches

    def normalize(self, records: Sequence[Dispatch]) -> list[EventCreate]:
        now = datetime.now(UTC)
        events: list[EventCreate] = []
        undated = 0

        for dispatch in records:
            if dispatch.occurred_at is None:
                undated += 1
            timestamp = dispatch.occurred_at or now
            # Un feed con el reloj adelantado haría fallar la validación de
            # `EventCreate` y perdería el lote entero por un ítem.
            if timestamp > now:
                timestamp = now

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.BOMBEROS,
                    type=EventType.ACCIDENT,
                    # Sin lat/lon: ver el docstring del módulo.
                    text=build_text(dispatch)[:10_000],
                    external_id=build_external_id(dispatch),
                    confidence=BOMBEROS_CONFIDENCE,
                    raw_data={
                        "_collector": self.name,
                        "_bomberos": {
                            "clave": dispatch.key,
                            "direccion": dispatch.address,
                            "aviso": dispatch.raw_text,
                            "guid": dispatch.guid,
                            "fecha_declarada": (
                                dispatch.occurred_at.isoformat()
                                if dispatch.occurred_at
                                else None
                            ),
                        },
                    },
                )
            )

        if undated:
            self.warn(
                f"{undated} avisos sin fecha reconocible; se usó la hora de la corrida"
            )
        return events


def _iter_codes(texts: Iterable[str]) -> list[tuple[int, ...]]:
    """Utilidad de diagnóstico: los códigos vistos en un conjunto de avisos.

    Sirve para calibrar `BOMBEROS_ACCIDENT_KEYS` contra un feed real sin escribir
    un script aparte: revela qué claves publica de verdad esa central.
    """
    seen: list[tuple[int, ...]] = []
    for text in texts:
        for code in find_codes(text):
            if code not in seen:
                seen.append(code)
    return sorted(seen)


__all__ = [
    "BOMBEROS_CONFIDENCE",
    "Bomberos104Collector",
    "Dispatch",
    "EstadoFeed",
    "build_external_id",
    "build_text",
    "entry_text",
    "entry_timestamp",
    "feed_is_broken",
    "find_codes",
    "matches_key",
    "normalise_code",
    "parse_dispatches",
    "revisar_feed",
    "strip_html",
]
