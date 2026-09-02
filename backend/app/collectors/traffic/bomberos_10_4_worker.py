"""Despachos de Bomberos — claves radiales de la central, y su decodificación.

Qué es una clave 10-4 dentro de este sistema
---------------------------------------------
La confirmación más fuerte que puede recibir la capa de accidentes. Una 10-4 no
es un aviso de que "puede haber" un choque: es la central despachando carros
porque hay gente atrapada en un vehículo. Alguien con autoridad ya decidió que
el hecho es real y comprometió recursos.

Por eso `EventSource.BOMBEROS` es `confirming=True` con peso 1.00 en
`confidence.py`, y una sola 10-4 lleva el incidente a certeza.

De dónde sale el dato — y por qué cambió la puerta
---------------------------------------------------
De la cuenta pública de la central del Cuerpo de Bomberos. No es una API: es el
mismo texto que un bombero escribe para que lo lean personas.

**El camino vivo es `POST /api/v1/apify/webhook`.** Apify raspa la cuenta según
su propio Schedule y llama a este backend cuando termina; nosotros leemos el
dataset que nos nombra. Ver `app/api/v1/endpoints/apify.py`.

El camino anterior —un puente tipo RSSHub que servía la cuenta como XML— está
**muerto, no degradado**: la ruta de Twitter de RSSHub desapareció cuando X
cerró su API, y el espejo de xcancel que la reemplazó tampoco responde. El
ajuste `BOMBEROS_DISPATCH_URL` fue eliminado por eso: un ajuste que no admite
ningún valor que funcione no es configuración, es una trampa para el próximo que
despliegue. `Bomberos104Collector` sigue acá, **fuera de `COLLECTORS`**, por si
alguna vez se levanta un puente propio sobre otra central; hoy no recolecta
nada y su URL hay que pasársela a mano.

Lo que NO cambió al cambiar de puerta, y es lo que importa:

* **El texto es prosa, no campos.** No hay `<address>` ni `<code>`: hay una
  frase. Lo que se guarda como dirección es el texto del aviso completo, en
  `raw_data`, sin fingir una precisión que no tiene.
* **El evento resultante es idéntico por las dos puertas.** `decode_dispatches`
  y `dispatches_to_events` son funciones libres justamente para eso: el mismo
  despacho produce el mismo `external_id`, el mismo texto y la misma confianza
  venga del webhook o del feed. Si cada puerta armara el suyo, la misma 10-4
  aparecería dos veces en el mapa.
* **Tres respuestas, no dos** (sólo en el camino RSS). Este módulo empezó
  distinguiendo "feed válido sin novedades" de "esto no es un feed", y esa
  distinción dejaba pasar la peor de las tres: un feed **válido y vacío**. Ver
  `EstadoFeed`. El equivalente en el webhook es `run_looks_stale` del cliente de
  Apify: un dataset servido por una corrida de anteayer.
* **La clave decide el tipo.** Ver el apartado siguiente.

Qué tipo de señal produce un despacho
--------------------------------------
El que diga su clave, resuelto por `vocabulary.dispatch_event_type`:
`10-0`/`10-1` estructural, `10-2` pastizales, `10-3` rescate, `10-4` accidente,
y `OTHER` para todo lo demás.

Acá hubo durante un tiempo un `EventType.ACCIDENT` **fijo**, y era correcto
mientras `BOMBEROS_ACCIDENT_KEYS` sólo aceptaba `10-4`. Cuando la ingesta se
abrió a la familia 10 entera, ese literal se quedó y pasó a mentir: un incendio
estructural entraba al sistema declarándose choque. El daño no era cosmético —el
motor particiona por familia antes de agrupar, así que ese incendio quedaba en
`traffic` y no podía corroborar ninguna señal de fuego del mismo lugar y minuto.

Geocodificación: presupuestada, y por qué existe
-------------------------------------------------
`geocode_dispatches` resuelve a punto las calles que aisló el decodificador,
con un tope por entrega (`BOMBEROS_MAX_GEOCODES`) y sin poder tumbar el lote.

Este paso no existía, con un argumento razonable: una 10-4 vale por su certeza
sobre el hecho, no por la precisión del punto, y Nominatim admite 1 req/s. Lo
que faltaba medir era la consecuencia: una señal sin `lat`/`lon` **no entra al
Paso A** —el motor sólo agrupa lo que tiene geometría— y el Paso B únicamente
adosa alertas de SENAPRED a incidentes ya abiertos. O sea que la fuente de
confianza 1.00 del catálogo no producía **ningún** incidente: quedaba
consultable en `/events` y ausente del mapa.

Lo que no se resuelve entra igual, sin coordenadas, como antes.

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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app.collectors import vocabulary
from app.collectors.base import BaseCollector
from app.collectors.geoservices import parse_timestamp, request_text
from app.collectors.nominatim import GeocodeResult, build_client, geocode
from app.collectors.traffic import gemini
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
    #: Resumen canónico del despacho, si se pudo decodificar. Lo rellena
    #: `fetch()` —no `parse_dispatches`, que es pura y sin red— y lo consume
    #: `build_text`. None significa "no se decodificó", y entonces el texto del
    #: evento cae a la forma de siempre.
    decoded: dict[str, Any] | None = None
    #: Punto resuelto por Nominatim desde las calles que aisló el decodificador.
    #: Lo rellena `geocode_dispatches`, que es la única parte con red de este
    #: camino. None es un resultado frecuente y legítimo: la central nombra
    #: esquinas que OpenStreetMap no conoce.
    point: GeocodeResult | None = None


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
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
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
        return EstadoFeed(roto=True, motivo=f"la respuesta no es un feed ({detalle})", entradas=0)

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
    """Texto del evento. El resumen decodificado si lo hay; si no, el de antes.

    El formato canónico —`(Clave) (Significado) en (Ubicación) (Fuente: @cuenta)`—
    lo fija `gemini.format_dispatch_summary`, no este módulo. Acá sólo se elige
    entre él y el respaldo.

    El respaldo dice "rescate vehicular" con la clave interpolada, y esa frase
    es correcta **sólo** para la familia 10-4, que es la única que el collector
    pedía cuando se escribió. Con `BOMBEROS_ACCIDENT_KEYS` configurable, un
    despacho 10-2 saldría rotulado como rescate vehicular. Se conserva porque
    perder el texto entero sería peor, pero el camino bueno es el decodificado.
    """
    if dispatch.decoded and dispatch.decoded.get("resumen"):
        return str(dispatch.decoded["resumen"])
    cuerpo = dispatch.address or ""
    return f"Clave {dispatch.key} (rescate vehicular): {cuerpo}".strip()


# =============================================================================
#  Núcleo del dominio: decodificar un despacho y convertirlo en evento
# =============================================================================
#
# Estas dos funciones son libres y no métodos, y esa forma es el punto. Hay dos
# caminos de entrada para el mismo hecho —el webhook de Apify, que es el vivo, y
# el lector de RSS de más abajo, que ya no está en rotación— y un despacho tiene
# que producir exactamente el mismo evento por los dos. Cuando esto vivía dentro
# de la clase, el webhook sólo podía reusarlo instanciando un collector que no
# iba a recolectar nada, o copiando la lógica: la segunda opción es cómo se
# consigue que la misma 10-4 entre con `external_id` distinto según la puerta y
# aparezca dos veces en el mapa.


async def decode_dispatches(
    dispatches: Sequence[Dispatch],
    *,
    source_handle: str,
    max_llm_calls: int,
) -> tuple[list[Dispatch], int]:
    """Adjunta a cada despacho su resumen canónico. Devuelve `(despachos, por_reglas)`.

    Secuencial y no `asyncio.gather`, a propósito: son como mucho un puñado de
    avisos por lote y el paralelismo sólo serviría para chocar antes con la
    cuota del modelo. Lo que sí hay es un tope duro: pasado ese número la
    decodificación sigue, pero por reglas, que no cuestan nada.

    Nada de esto puede tumbar al llamador. `gemini.extract_dispatch` ya absorbe
    todo fallo del modelo, y el `except` de acá cubre lo que quede: un despacho
    sin resumen entra igual, con el texto de siempre.

    **No se avisa cuando se cae a las reglas**, y eso es una decisión, no un
    olvido. Caer a las reglas es el camino NORMAL de cualquier despliegue sin
    `GEMINI_API_KEY` —la capa funciona igual, sólo que sin modelo—. Marcar
    `partial` una corrida sana es la misma trampa que este módulo ya documenta
    para el feed sin 10-4: un aviso que se repite en cada corrida entrena a todo
    el mundo a ignorar los avisos, y ahí se pierde el que sí importa. El modo
    queda en `raw_data._extraction.mode`, que es donde se puede medir.
    """
    presupuesto = max_llm_calls
    handle = source_handle.strip() or "Bomberos"
    decodificados: list[Dispatch] = []
    por_reglas = 0

    for dispatch in dispatches:
        decoded: dict[str, Any] | None = None
        modo = gemini.MODE_HEURISTIC
        try:
            if presupuesto > 0 and gemini.is_configured():
                presupuesto -= 1
                decoded = await gemini.extract_dispatch(dispatch.raw_text, source_handle=handle)
                if decoded is not None:
                    modo = gemini.MODE_GEMINI
            if decoded is None:
                por_reglas += 1
                decoded = gemini.dispatch_summary_heuristic(dispatch.raw_text, source_handle=handle)
        except Exception as exc:  # pragma: no cover — extract_dispatch no lanza
            logger.warning(
                "no se pudo decodificar un despacho; entra con el texto crudo",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )

        if decoded is not None:
            # El modo se anota POR DESPACHO y no por lote: con el presupuesto
            # agotado a mitad de camino, los primeros avisos pasaron por el
            # modelo y los últimos no. Un solo valor para todos mentiría sobre
            # la mitad.
            decoded = {**decoded, "mode": modo}

        decodificados.append(replace(dispatch, decoded=decoded))

    if por_reglas:
        logger.info(
            "despachos decodificados por reglas",
            extra={
                "cantidad": por_reglas,
                "tope": max_llm_calls,
                "modelo_configurado": gemini.is_configured(),
            },
        )
    return (decodificados, por_reglas)


async def geocode_dispatches(
    dispatches: Sequence[Dispatch], *, max_geocodes: int
) -> tuple[list[Dispatch], int]:
    """Resuelve a punto las calles que aisló el decodificador.

    Devuelve `(despachos, resueltos)`. **Nunca lanza**: un fallo de Nominatim
    deja el despacho sin coordenadas, que es el estado en el que estaban todos
    antes de que este paso existiera.

    # Por qué ahora sí se geocodifica

    El módulo declaraba que no hacía falta, con dos argumentos que eran ciertos
    cuando se escribieron: una 10-4 vale por su certeza sobre el hecho y no por
    la precisión del punto, y geocodificar metía una llamada por aviso dentro
    del límite de 1 req/s de Nominatim.

    Lo que cambió es la consecuencia, que estaba escrita en el propio docstring
    y resultó ser más cara de lo que parecía: **una señal sin `lat`/`lon` no
    entra al Paso A** —`cluster_unassigned_events` filtra por `geom IS NOT
    NULL`— y el Paso B sólo adosa alertas de SENAPRED a incidentes que ya
    existen. O sea que la fuente de confianza 1.00 del catálogo, la única que
    lleva un incidente a certeza por sí sola, **no podía producir ni un solo
    incidente**: quedaba consultable en `/events` y ausente del mapa. Los
    contadores de Incendios, Accidentes y Otras emergencias no la veían nunca.

    Los dos argumentos originales siguen valiendo y por eso el paso es
    presupuestado igual que en el MTT (`max_geocodes`) y falla hacia el silencio:
    lo que no se resuelve entra sin punto, exactamente como antes.

    # Por qué no cuesta una llamada por despacho

    Porque el decodificador ya corrió. `decode_dispatches` produce
    `{street_1, street_2, city}` con el mismo vocabulario que consume
    `nominatim.build_query`, así que acá no hay extracción: sólo la consulta. Y
    los despachos sin vía reconocible —los que `build_query` resuelve a None— ni
    siquiera la gastan.
    """
    if not dispatches or max_geocodes <= 0:
        return (list(dispatches), 0)

    salida: list[Dispatch] = []
    resueltos = 0
    gastados = 0

    async with build_client() as client:
        for dispatch in dispatches:
            calles = dispatch.decoded or {}
            # Sin vía principal no hay nada que buscar. Preguntar sólo por la
            # comuna devolvería el centroide comunal, que como ubicación de una
            # emergencia es peor que no tener ninguna: parece un dato y no lo es.
            if not calles.get("street_1") or gastados >= max_geocodes:
                salida.append(dispatch)
                continue

            punto: GeocodeResult | None = None
            try:
                punto = await geocode(client, dict(calles))
            except Exception as exc:
                # Se atrapa `Exception` y no `CollectorError` a propósito: una
                # esquina que hace reventar a Nominatim no puede costarle el
                # punto a los demás despachos del lote, y menos aún el lote.
                logger.warning(
                    "Nominatim falló para un despacho; entra sin coordenadas",
                    extra={"error": f"{type(exc).__name__}: {exc}"},
                )
            # El presupuesto avanza igual: un servicio que falla consumió su
            # segundo de rate limit lo mismo que uno que responde.
            gastados += 1

            if punto is not None:
                resueltos += 1
            salida.append(replace(dispatch, point=punto))

    logger.info(
        "despachos geocodificados",
        extra={"resueltos": resueltos, "intentos": gastados, "tope": max_geocodes},
    )
    return (salida, resueltos)


def dispatch_type(dispatch: Dispatch) -> EventType:
    """Naturaleza de la señal de UN despacho, según su clave.

    Se prueba primero la clave que aisló el decodificador (`decoded["clave"]`) y
    después el aviso completo. El orden importa: el campo aislado es la clave
    que motivó el despacho, mientras que el texto entero puede traer además una
    petición de recursos (`3-2`, ambulancia) que no describe el siniestro.

    Ver `vocabulary.dispatch_event_type` para el porqué de todo esto — en corto,
    acá había un `EventType.ACCIDENT` fijo desde que la fuente sólo entregaba
    `10-4`, y desde que la ingesta se abrió a la familia 10 entera ese literal
    estaba metiendo incendios estructurales en la familia `traffic`.
    """
    clave = (dispatch.decoded or {}).get("clave")
    if isinstance(clave, str) and clave.strip():
        tipo = vocabulary.dispatch_event_type(clave)
        if tipo is not vocabulary.DISPATCH_DEFAULT_TYPE:
            return tipo

    # `dispatch.key` es la clave configurada que hizo pasar el filtro de
    # ingesta: es el respaldo natural cuando el decodificador no aisló ninguna.
    for texto in (dispatch.key, dispatch.raw_text):
        if texto:
            tipo = vocabulary.dispatch_event_type(texto)
            if tipo is not vocabulary.DISPATCH_DEFAULT_TYPE:
                return tipo

    return vocabulary.DISPATCH_DEFAULT_TYPE


def dispatches_to_events(
    dispatches: Sequence[Dispatch], *, collector: str
) -> tuple[list[EventCreate], int]:
    """Despachos decodificados → señales. Devuelve `(eventos, sin_fecha)`.

    Pura y sin red. `collector` sólo viaja a `raw_data._collector` para que una
    fila diga por qué puerta entró —webhook o feed— sin cambiar nada más del
    evento: el `external_id`, el texto y la confianza son idénticos por los dos
    caminos, que es lo que hace que la idempotencia funcione entre ellos.

    Las coordenadas, si las hay, las puso `geocode_dispatches` antes. Un
    despacho sin punto entra igual —perder una 10-4 por una esquina que
    OpenStreetMap no conoce sería el peor intercambio posible—, sabiendo que no
    entra al Paso A del motor.
    """
    now = datetime.now(UTC)
    events: list[EventCreate] = []
    undated = 0

    for dispatch in dispatches:
        if dispatch.occurred_at is None:
            undated += 1
        timestamp = dispatch.occurred_at or now
        # Un feed con el reloj adelantado haría fallar la validación de
        # `EventCreate` y perdería el lote entero por un ítem.
        if timestamp > now:
            timestamp = now

        punto = dispatch.point

        events.append(
            EventCreate(
                timestamp=timestamp,
                source=EventSource.BOMBEROS,
                # Derivado de la clave, no fijo. Ver `dispatch_type`.
                type=dispatch_type(dispatch),
                lat=punto.lat if punto else None,
                lon=punto.lon if punto else None,
                text=build_text(dispatch)[:10_000],
                external_id=build_external_id(dispatch),
                confidence=BOMBEROS_CONFIDENCE,
                raw_data={
                    "_collector": collector,
                    "_bomberos": {
                        "clave": dispatch.key,
                        "direccion": dispatch.address,
                        "aviso": dispatch.raw_text,
                        "guid": dispatch.guid,
                        "fecha_declarada": (
                            dispatch.occurred_at.isoformat() if dispatch.occurred_at else None
                        ),
                    },
                    # Las partes decodificadas van SEPARADAS del aviso, no
                    # sobrescribiéndolo: el crudo es lo que dijo la central y lo
                    # demás es lo que este sistema entendió. Fundir las dos
                    # cosas borra la distinción para siempre, que es el mismo
                    # criterio de `_extraction` en las otras capas.
                    "_extraction": {
                        # El modo real de ESTE despacho, puesto por
                        # `decode_dispatches`. No se deriva de `is_configured()`:
                        # con el presupuesto agotado, o con el modelo devolviendo
                        # None, hay avisos que pasaron por las reglas aunque la
                        # clave esté configurada.
                        "mode": (dispatch.decoded or {}).get("mode", gemini.MODE_HEURISTIC),
                        "clave": (dispatch.decoded or {}).get("clave"),
                        "significado": (dispatch.decoded or {}).get("significado"),
                        "street_1": (dispatch.decoded or {}).get("street_1"),
                        "street_2": (dispatch.decoded or {}).get("street_2"),
                        "city": (dispatch.decoded or {}).get("city"),
                        "resumen": (dispatch.decoded or {}).get("resumen"),
                    },
                    # Separado de `_extraction`, igual que en el MTT: qué leyó
                    # el decodificador y qué resolvió el geocodificador son dos
                    # pasos distintos, y si mañana un punto está mal esto dice
                    # cuál de los dos falló. `importance` baja suele significar
                    # que Nominatim resolvió la comuna entera y no la esquina.
                    "_geocoding": punto.as_dict() if punto else None,
                },
            )
        )

    return (events, undated)


class Bomberos104Collector(BaseCollector):
    """Lector del feed RSS de despachos con clave de rescate vehicular.

    **Fuera de `COLLECTORS`.** Ver el docstring de `__init__`: el puente que
    leía está muerto y los despachos entran por el webhook de Apify.
    """

    name = "bomberos_10_4"
    source = EventSource.BOMBEROS
    default_interval_seconds = 180

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.BOMBEROS_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any, *, feed_url: str = "") -> None:
        """La URL del feed llega por argumento, **no desde `settings`**.

        Antes salía de `BOMBEROS_DISPATCH_URL`, que ya no existe. Ese ajuste
        apuntaba por defecto a un puente RSSHub sobre la cuenta de la central, y
        ese camino está muerto sin reemplazo: la ruta de Twitter de RSSHub
        desapareció con la API de X, y el espejo de xcancel tampoco responde. Un
        ajuste que no admite ningún valor que funcione no es configuración, es
        una trampa: el próximo que despliegue lo rellena, ve arrancar el
        collector y tarda días en descubrir que no hay fuente detrás.

        Los despachos entran hoy por `POST /api/v1/apify/webhook`. Esta clase
        queda fuera de `COLLECTORS` (ver `app/collectors/registry.py`) y sólo
        sirve si algún día se levanta un puente RSS **propio** —una instancia de
        RSSHub sobre otra central, por ejemplo—, en cuyo caso quien la registre
        le pasa la URL explícitamente y sabe lo que está haciendo.
        """
        super().__init__(session)
        self.url = feed_url.strip()
        if not self.url:
            raise CollectorError(
                "Bomberos104Collector necesita una URL de feed explícita. El "
                "camino vivo de los despachos es el webhook de Apify "
                "(POST /api/v1/apify/webhook), no un feed RSS."
            )
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

        return await self._decode(dispatches)

    async def _decode(self, dispatches: Sequence[Dispatch]) -> list[Dispatch]:
        """Delegación pura a `decode_dispatches`. Ver allá el porqué de todo.

        Se conserva el método —y no se llama a la función libre desde `fetch`—
        porque `_decode` es superficie usada por los tests de este worker desde
        antes de que existiera el webhook, y romper ese nombre para ahorrar tres
        líneas no compra nada.
        """
        decodificados, _por_reglas = await decode_dispatches(
            dispatches,
            source_handle=settings.BOMBEROS_SOURCE_HANDLE,
            max_llm_calls=settings.BOMBEROS_MAX_LLM_CALLS,
        )
        return decodificados

    def normalize(self, records: Sequence[Dispatch]) -> list[EventCreate]:
        """Delegación a `dispatches_to_events`, más el aviso de la corrida.

        El aviso se queda acá y no baja al núcleo compartido a propósito:
        `self.warn` deja la corrida en `partial`, y "partial" es un concepto de
        `BaseCollector` —de una corrida de CRON— que el webhook no tiene. El
        conteo de avisos sin fecha sí es compartido, y por eso lo devuelve la
        función libre en vez de contarlo dos veces.
        """
        events, undated = dispatches_to_events(records, collector=self.name)
        if undated:
            self.warn(f"{undated} avisos sin fecha reconocible; se usó la hora de la corrida")
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
    "decode_dispatches",
    "dispatch_type",
    "dispatches_to_events",
    "entry_text",
    "entry_timestamp",
    "feed_is_broken",
    "find_codes",
    "geocode_dispatches",
    "matches_key",
    "normalise_code",
    "parse_dispatches",
    "revisar_feed",
    "strip_html",
]
