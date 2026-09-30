"""Despachos de Bomberos — claves radiales de la central, y su decodificación.

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
De la cuenta pública de la central del Cuerpo de Bomberos. No es una API: es el
mismo texto que un bombero escribe para que lo lean personas.

**La única puerta es `POST /api/v1/apify/webhook`.** Apify raspa la cuenta según
su propio Schedule y avisa a este backend cuando termina; el endpoint encola el
aviso y el proceso de workers lee el dataset que nombra. Ver
`app/api/v1/endpoints/apify.py` y `app/services/apify_webhook_service.py`.

El lector RSS que existió antes (`Bomberos104Collector`, sobre un puente
RSSHub) se borró el 2026-09-23: la ruta de Twitter de RSSHub desapareció cuando
X cerró su API y el espejo de xcancel tampoco responde. Este módulo conserva lo
que el webhook usa: el `Dispatch`, su decodificación, su geocodificación y su
conversión a evento. `revisar_feed` queda porque la usa la prensa local.

* **El texto es prosa, no campos.** No hay `<address>` ni `<code>`: hay una
  frase. Lo que se guarda como dirección es el texto del aviso completo, en
  `raw_data`, sin fingir una precisión que no tiene.
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
El `external_id` sale del identificador del tuit (`guid`); cuando falta, de un
hash determinista de la clave, el texto y la fecha. Releer el mismo dataset
actualiza la fila en vez de duplicar el despacho.
"""

from __future__ import annotations

import hashlib
import html as html_module
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import feedparser

from app.collectors import vocabulary
from app.collectors.cuarteles import cuartel_nombrado, punto_de_cuartel
from app.collectors.nominatim import GeocodeResult, build_client, geocode
from app.collectors.traffic import gemini
from app.collectors.vocabulary import (
    CBV,
    SISTEMAS_CLAVES,
    SistemaClaves,
    find_codes,
    matches_key,
    normalise_code,
    sistema_de_cuenta,
)
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
#  El despacho y el chequeo de feeds
# =============================================================================


@dataclass(frozen=True, slots=True)
class Dispatch:
    """Un despacho ya extraído de un tuit de la central."""

    key: str
    address: str | None
    occurred_at: datetime | None
    commune: str | None
    raw_text: str
    guid: str | None = None
    #: Resumen canónico del despacho, si se pudo decodificar. Lo rellena
    #: `decode_dispatches` y lo consume `build_text`. None significa "no se
    #: decodificó", y entonces el texto del evento cae a la forma de siempre.
    decoded: dict[str, Any] | None = None
    #: Punto resuelto por Nominatim desde las calles que aisló el decodificador.
    #: Lo rellena `geocode_dispatches`, que es la única parte con red de este
    #: camino. None es un resultado frecuente y legítimo: la central nombra
    #: esquinas que OpenStreetMap no conoce.
    point: GeocodeResult | None = None
    #: Cuenta de X que publicó el despacho, con arroba («@CBVM132»). Decide con
    #: qué diccionario de claves se lee (ver `vocabulary.SistemaClaves`). None
    #: en los tuits que no dicen su autor: ahí rige `BOMBEROS_SOURCE_HANDLE`.
    cuenta: str | None = None
    #: Enlace público al tuit, si el Actor lo trae. Sólo para el panel: la
    #: identidad del despacho sigue siendo `guid`.
    url: str | None = None
    #: Unidades que llegaron después en un «SALE … A …» del mismo lote y se
    #: anexaron a este despacho (ver `_colapsar_seguimientos` en el webhook).
    unidades_extra: tuple[str, ...] = ()


def strip_html(fragment: str) -> str:
    """HTML/entidades → texto plano normalizado en espacios.

    Los textos llegan con marcado y entidades (`&amp;`, `&#39;`) según el Actor
    o el feed que los sirva.
    """
    without_tags = _TAG_PATTERN.sub(" ", fragment)
    return _WHITESPACE.sub(" ", html_module.unescape(without_tags)).strip()


@dataclass(frozen=True, slots=True)
class EstadoFeed:
    """Qué llegó de un feed RSS/Atom, en las tres categorías que importan.

    La usa hoy la prensa local (`news/local_news_worker.py`); nació en el lector
    RSS de Bomberos, que ya no existe.

    Las dos primeras ya se distinguían; la tercera se añadió después de que una
    fuente muerta pasara días reportando corridas `success`.

    * `roto` — no es un feed: HTML de error, captcha, un 429 servido con estado
      200. Necesita a una persona.
    * `entradas == 0` con `roto=False` — el feed es válido y **no trae nada**.
      Sospechoso: una fuente viva no pasa días sin publicar.
    * `entradas > 0` — sano. Que ninguna sea una 10-4 es lo normal y no se avisa.
    """

    roto: bool
    motivo: str | None
    entradas: int


def revisar_feed(feed_body: str) -> EstadoFeed:
    """Clasifica la respuesta de un feed. Ver `EstadoFeed`.

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
# Estas funciones son libres y no métodos: el webhook las usa sin instanciar
# ningún collector, y los tests las ejercitan sin red ni base.


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
    respaldo = source_handle.strip() or "Bomberos"
    decodificados: list[Dispatch] = []
    por_reglas = 0

    for dispatch in dispatches:
        decoded: dict[str, Any] | None = None
        modo = gemini.MODE_HEURISTIC
        # La cuenta del propio despacho manda: con dos centrales en el mismo
        # lote, un solo `source_handle` firmaría los despachos de Viña como de
        # Valparaíso y los leería con el diccionario equivocado.
        handle = dispatch.cuenta or respaldo
        sistema = sistema_de_despacho(dispatch, respaldo=respaldo)
        try:
            if presupuesto > 0 and gemini.is_configured():
                presupuesto -= 1
                decoded = await gemini.extract_dispatch(
                    dispatch.raw_text, source_handle=handle, sistema=sistema
                )
                if decoded is not None:
                    modo = gemini.MODE_GEMINI
            if decoded is None:
                por_reglas += 1
                decoded = gemini.dispatch_summary_heuristic(
                    dispatch.raw_text, source_handle=handle, sistema=sistema
                )
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


#: Jurisdicción de cada central, por su cuenta de X. Es la comuna que la caja
#: de `nominatim.COMUNA_VIEWBOX` va a acotar.
#:
#: Es información **gratis y fiable** que el sistema estaba tirando: quién
#: publica el despacho dice en qué comuna ocurrió, mucho mejor de lo que ninguna
#: heurística puede sacar del texto. El decodificador no la produce —la central
#: no escribe la comuna, porque para ella es obvia— así que `city` llegaba
#: siempre en nulo y la consulta salía sin ninguna guarda geográfica.
#:
#: Lo que costó: el 2026-09-03, «PRIMERO DE MAYO / 12 DE OCTUBRE» de @CGI_CBV
#: resolvía en Quillota, a 40 km. Ver `nominatim.COMUNA_VIEWBOX`.
#:
#: Se deriva de `vocabulary.SISTEMAS_CLAVES` —la primera comuna de cada
#: Cuerpo— para que declarar una central nueva sea una sola entrada allá.
HANDLE_COMUNA: dict[str, str] = {
    cuenta: sistema.comunas[0]
    for sistema in SISTEMAS_CLAVES
    for cuenta in sistema.cuentas
    if sistema.comunas
}


def comuna_de_handle(handle: str | None) -> str | None:
    """Comuna de la central, o None si la cuenta no está declarada.

    None deja la búsqueda sin acotar, que es el comportamiento anterior: una
    central nueva no se rompe por no estar en la tabla, sólo pierde la guarda.
    """
    if not handle:
        return None
    return HANDLE_COMUNA.get(handle.strip().lstrip("@").lower())


def comunas_alternativas(handle: str | None) -> tuple[str, ...]:
    """Las OTRAS comunas de la jurisdicción, para reintentar la geocodificación.

    El CBVM cubre Viña del Mar **y Concón**, y la central casi nunca escribe la
    comuna. Con la guarda puesta en Viña, una esquina de Concón se descarta por
    caer en la comuna equivocada; el reintento con Concón la recupera, y sólo
    cuesta peticiones cuando la primera falló.
    """
    sistema = sistema_de_cuenta(handle)
    if sistema is None:
        return ()
    return tuple(sistema.comunas[1:])


def unidades_del_aviso(texto: str | None, sistema: SistemaClaves) -> list[str]:
    """Reexporta `gemini.unidades_del_aviso` para el webhook."""
    return gemini.unidades_del_aviso(texto, sistema)


def sistema_de_despacho(dispatch: Dispatch, *, respaldo: str | None = None) -> SistemaClaves:
    """Con qué diccionario se lee un despacho.

    El de su cuenta si la trae; si no, el de `respaldo` (la cuenta configurada);
    y si tampoco, el del CBV, que es lo que regía antes de que hubiera dos
    centrales. Quien filtra las cuentas sin tabla es el webhook, antes de llegar
    acá: este respaldo sólo cubre el camino RSS y los tuits sin autor.
    """
    return sistema_de_cuenta(dispatch.cuenta) or sistema_de_cuenta(respaldo) or CBV


async def geocode_dispatches(
    dispatches: Sequence[Dispatch], *, max_geocodes: int, comuna: str | None = None
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
            # Un destino que es un cuartel («Quinta Compañía de Bomberos
            # Quilpue», «CUARTEL GENERAL») se ubica con la instantánea del SIG,
            # sin gastar Nominatim, que no conoce esos nombres. Ver
            # `collectors/cuarteles.py`.
            destino = " ".join(
                str(calles.get(campo) or "") for campo in ("street_1", "street_2")
            ).strip() or dispatch.raw_text
            cuartel = cuartel_nombrado(destino, sistema_de_despacho(dispatch))
            if cuartel is not None:
                resueltos += 1
                salida.append(replace(dispatch, point=punto_de_cuartel(cuartel, destino)))
                continue
            # Sin vía principal no hay nada que buscar. Preguntar sólo por la
            # comuna devolvería el centroide comunal, que como ubicación de una
            # emergencia es peor que no tener ninguna: parece un dato y no lo es.
            if not calles.get("street_1") or gastados >= max_geocodes:
                salida.append(dispatch)
                continue

            punto: GeocodeResult | None = None
            # La comuna del despacho si el decodificador la sacó; si no —el
            # caso normal, la central no la escribe— la de SU central, y recién
            # después la que pasó el llamador. Ver `HANDLE_COMUNA`.
            principal = calles.get("city") or comuna_de_handle(dispatch.cuenta) or comuna
            intentos = [principal]
            if not calles.get("city"):
                intentos += [
                    alternativa
                    for alternativa in comunas_alternativas(dispatch.cuenta)
                    if alternativa != principal
                ]

            for intento in intentos:
                if gastados >= max_geocodes:
                    break
                try:
                    punto = await geocode(client, dict(calles), comuna=intento)
                except Exception as exc:
                    # Se atrapa `Exception` y no `CollectorError` a propósito:
                    # una esquina que hace reventar a Nominatim no puede
                    # costarle el punto a los demás despachos del lote.
                    logger.warning(
                        "Nominatim falló para un despacho; entra sin coordenadas",
                        extra={"error": f"{type(exc).__name__}: {exc}"},
                    )
                # El presupuesto avanza igual: un servicio que falla consumió
                # su segundo de rate limit lo mismo que uno que responde.
                gastados += 1
                if punto is not None:
                    break

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
    # El diccionario del Cuerpo que despachó: `Clave 3` es un incendio
    # vehicular en Viña y no existe en Valparaíso.
    sistema = sistema_de_despacho(dispatch)
    clave = (dispatch.decoded or {}).get("clave")
    if isinstance(clave, str) and clave.strip():
        tipo = vocabulary.dispatch_event_type(clave, sistema)
        if tipo is not vocabulary.DISPATCH_DEFAULT_TYPE:
            return tipo

    # `dispatch.key` es la clave configurada que hizo pasar el filtro de
    # ingesta: es el respaldo natural cuando el decodificador no aisló ninguna.
    for texto in (dispatch.key, dispatch.raw_text):
        if texto:
            tipo = vocabulary.dispatch_event_type(texto, sistema)
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
                        # Quién despachó y con qué diccionario se leyó. Con dos
                        # centrales en el mismo webhook, sin esto no hay forma
                        # de saber desde la base si un `Clave 10` se interpretó
                        # como agua (CBV) o como servicio (CBVM).
                        "cuenta": dispatch.cuenta,
                        "cuerpo": sistema_de_despacho(dispatch).slug,
                        # Los carros despachados, más los de los seguimientos
                        # que se anexaron. La ficha del incidente los muestra.
                        "unidades": list(
                            dict.fromkeys(
                                (
                                    *gemini.unidades_del_aviso(
                                        dispatch.raw_text, sistema_de_despacho(dispatch)
                                    ),
                                    *dispatch.unidades_extra,
                                )
                            )
                        ),
                        "url": dispatch.url,
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


__all__ = [
    "BOMBEROS_CONFIDENCE",
    "Dispatch",
    "EstadoFeed",
    "build_external_id",
    "build_text",
    "decode_dispatches",
    "dispatch_type",
    "dispatches_to_events",
    "find_codes",
    "geocode_dispatches",
    "matches_key",
    "normalise_code",
    "revisar_feed",
    "strip_html",
    "unidades_del_aviso",
]
