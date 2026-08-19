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
  compartida y sin SLA; devuelve 429 y 503 con frecuencia. El collector trata
  esos fallos como transitorios: reintenta, y si no lo consigue deja la corrida
  en `failed` con el motivo. Lo que **no** hace es devolver cero eventos con
  estado `success`, porque eso sería indistinguible de una noche sin rescates.
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
# El problema
# -----------
# La misma clave aparece escrita de varias formas en un feed de texto libre:
#
#     10-4        forma canónica
#     10-0-4      con el separador de familia que usan varios Cuerpos
#     10-4-1      con sufijo de subtipo (rescate con víctima atrapada)
#     10.4        con punto
#     10 – 4      con guion largo y espacios, cortesía del autocorrector
#
# Y hay cadenas que se le parecen y NO son la clave:
#
#     10-40       otra clave por completo (emanación de gas)
#     10-41       ídem
#     10-0-1      otra clave (incendio estructural)
#     10-4-2026   una fecha, no un código
#
# Por qué no una sola expresión regular
# --------------------------------------
# Se puede escribir una regex que acepte las cinco variantes y rechace las
# cuatro trampas. Queda así:
#
#     (?<!\d)10\s*[-–—.]\s*(?:0\s*[-–—.]\s*)?4(?:\s*[-–—.]\s*\d{1,2})?(?!\d)
#
# Funciona, y es exactamente el tipo de línea que nadie se atreve a tocar en seis
# meses. El fallo que importa —confundir 10-40 con 10-4— depende de un
# `(?!\d)` en el extremo derecho: un carácter fácil de perder en una edición
# apurada, imposible de ver en una revisión, y cuyo síntoma es un despacho por
# fuga de gas apareciendo en el mapa como accidente vehicular.
#
# La decisión
# -----------
# Se separa en dos pasos, cada uno trivialmente verificable:
#
#   1. `_CODE_TOKEN` reconoce *cualquier* cosa con forma de código —grupos de
#      dígitos unidos por separadores— sin decidir cuál es.
#   2. `normalise_code` convierte ese token en una tupla de enteros y aplica las
#      dos reglas del dominio: descartar lo que no es un código (grupos de tres
#      o más dígitos: años, alturas de calle) y colapsar el 0 intermedio del
#      formato 10-0-x.
#
# Después basta comparar tuplas. `10-40` produce `(10, 40)` y `10-4` produce
# `(10, 4)`: distintas por construcción, sin depender de ningún lookahead. La
# comparación por prefijo es la que deja pasar `10-4-1` y rechazar `10-40`, y se
# lee en voz alta sin explicación.

#: Cualquier cosa con forma de código: grupos de dígitos unidos por separadores.
#: Deliberadamente permisivo — filtrar es trabajo de `normalise_code`.
_CODE_TOKEN = re.compile(
    r"(?<!\d)(\d{1,4}(?:\s*[-–—/.]\s*\d{1,4}){1,3})(?!\d)"
)

#: Separadores admitidos, incluidos los guiones tipográficos que introducen los
#: teclados de teléfono.
_CODE_SPLIT = re.compile(r"[\s\-–—/.]+")

#: Un grupo con tres o más dígitos delata que el token no es una clave: es una
#: fecha ("10-4-2026") o una altura de calle. Ninguna clave del Sistema Nacional
#: pasa de dos dígitos por grupo.
_MAX_GROUP_DIGITS = 2


def normalise_code(token: str) -> tuple[int, ...] | None:
    """Token con forma de código → tupla de enteros comparable. None si no lo es.

    Aplica las dos reglas del dominio:

    * **Grupos de 3+ dígitos lo descalifican.** `10-4-2026` es una fecha.
    * **El 0 intermedio del formato `10-0-x` se colapsa.** Varios Cuerpos lo usan
      como separador de familia, no como parte del código, así que `10-0-4` y
      `10-4` son la misma clave y tienen que normalizar a la misma tupla.

    >>> normalise_code("10-4")
    (10, 4)
    >>> normalise_code("10-0-4")
    (10, 4)
    >>> normalise_code("10-4-1")
    (10, 4, 1)
    >>> normalise_code("10-40")
    (10, 40)
    >>> normalise_code("10-4-2026") is None
    True
    """
    pieces = [piece for piece in _CODE_SPLIT.split(token.strip()) if piece]
    if len(pieces) < 2:
        return None
    if any(len(piece) > _MAX_GROUP_DIGITS for piece in pieces):
        return None

    try:
        groups = [int(piece) for piece in pieces]
    except ValueError:  # pragma: no cover — la regex sólo captura dígitos
        return None

    # El 0 en segunda posición es separador de familia, no un valor.
    if len(groups) > 2 and groups[1] == 0:
        groups = [groups[0], *groups[2:]]
    return tuple(groups)


def find_codes(text: str) -> list[tuple[int, ...]]:
    """Todos los códigos normalizados presentes en un texto."""
    found: list[tuple[int, ...]] = []
    for match in _CODE_TOKEN.finditer(text):
        code = normalise_code(match.group(1))
        if code is not None:
            found.append(code)
    return found


def matches_key(text: str, keys: Sequence[str]) -> str | None:
    """Devuelve la clave buscada que aparece en el texto, o None.

    La comparación es **por prefijo de tupla**: un aviso con `10-4-1` responde a
    la clave configurada `10-4` porque `(10, 4)` es prefijo de `(10, 4, 1)`. Ese
    sufijo es un subtipo del mismo despacho —rescate con víctima atrapada—, no
    otra emergencia, y descartarlo perdería justo los casos más graves.

    `10-40` produce `(10, 40)`, que no tiene a `(10, 4)` por prefijo, así que no
    coincide. Sin lookaheads y sin ambigüedad.
    """
    present = find_codes(text)
    if not present:
        return None

    for key in keys:
        wanted = normalise_code(key)
        if wanted is None:
            continue
        for code in present:
            if code[: len(wanted)] == wanted:
                return key
    return None


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


def feed_is_broken(feed_body: str) -> tuple[bool, str | None]:
    """¿El feed llegó ilegible? Devuelve `(roto, motivo)`.

    Distingue dos cosas que un `len(entries) == 0` confunde: un feed válido sin
    novedades (normal, silencioso) y algo que no es un feed (la página de error
    de RSSHub, un captcha, un 429 servido como HTML con estado 200). Sólo lo
    segundo merece alarma.

    El discriminador es `parsed.version`, y esa elección tiene una historia
    corta: `bozo` no sirve para esto. feedparser es tan indulgente que digiere
    HTML sin quejarse —devuelve `bozo=False` y hasta rellena `feed.summary` con
    el texto de la página de error—, así que confiar en `bozo` dejaba pasar
    justo el caso que este chequeo existe para atrapar. `version` sólo trae
    valor (`rss20`, `atom10`…) cuando reconoció un formato de sindicación de
    verdad; ante HTML queda en cadena vacía.
    """
    parsed = feedparser.parse(feed_body)
    if parsed.entries:
        return (False, None)

    if not getattr(parsed, "version", ""):
        reason = getattr(parsed, "bozo_exception", None)
        detalle = f"{type(reason).__name__}: {reason}" if reason else "no es RSS ni Atom"
        return (True, f"la respuesta no es un feed ({detalle})")

    if getattr(parsed, "bozo", 0):
        reason = getattr(parsed, "bozo_exception", None)
        return (True, f"XML inválido ({reason})" if reason else "XML inválido")

    return (False, None)


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
            broken, reason = feed_is_broken(body)
            dispatches = parse_dispatches(body, self.keys)
        except Exception as exc:
            raise CollectorError(
                f"bomberos: el feed no se pudo interpretar: {type(exc).__name__}: {exc}",
                detail={"url": self.url, "muestra": body[:200]},
            ) from exc

        if broken:
            # Se avisa y se sigue: la corrida queda `partial`, con el motivo
            # visible en `collector_runs`. Un feed ilegible es un problema del
            # puente —RSSHub sirviendo una página de error con HTTP 200— y no
            # justifica perder lo poco que se haya podido leer.
            self.warn(f"el feed llegó sin ítems interpretables ({reason})")

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
    "build_external_id",
    "build_text",
    "entry_text",
    "entry_timestamp",
    "feed_is_broken",
    "find_codes",
    "matches_key",
    "normalise_code",
    "parse_dispatches",
    "strip_html",
]
