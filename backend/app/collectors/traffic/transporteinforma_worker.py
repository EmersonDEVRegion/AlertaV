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
El MTT publica dos clases de aviso y esta tubería emite las dos, con tipos
distintos:

* **`ACCIDENT`** — el siniestro. Correlacionable, familia `traffic`, se une con
  los despachos de Bomberos.
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

De dónde llegan los avisos
--------------------------
De la cuenta de X @TTIValparaiso, por el webhook de Apify
(`app/collectors/traffic/transito_x.py`). Este módulo es la tubería que esa
cuenta usa: clasificar, extraer, geocodificar y emitir.

El portal `valparaiso.transporteinforma.cl` también se leyó, cada 10 minutos,
hasta octubre de 2026. Se retiró: en 3 481 corridas (19-09 al 17-10-2026) no
leyó ningún aviso, y el portal publicaba unos 4 en dos meses y medio. La cuenta
de X es el canal que el MTT mantiene al día.

El cuello de botella de una corrida no es el MTT sino **Nominatim**, el
servicio genuinamente limitado (1 req/s global, compartido con la prensa
local). Por eso el presupuesto de geocodificación se gasta con los accidentes
primero.

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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.collectors.geoservices import normalise_text
from app.collectors.nominatim import GeocodeResult, build_client, geocode
from app.collectors.traffic import gemini
from app.collectors.vocabulary import (
    es_accidente_vial,
    es_operacion_vial,
)
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
#:
#: La barra va **sin exigir espacios alrededor**, y no es cosmética. El MTT
#: escribe «Av. España / Uno Norte» con espacios; las cuentas de redes
#: escriben «Argentina/Colón» pegado, que es como se rotula un cruce en un
#: titular. Con `\s+/\s+` la segunda forma no separaba nada y `street_1` quedaba
#: valiendo «Argentina/Colón» — un nombre que no existe en OSM, así que la
#: consulta no devolvía punto y el evento entraba mudo.
_INTERSECTION_SPLIT = re.compile(
    r"(?:\s*/\s*|\s+(?:con|esquina(?:\s+de)?|y)\s+)", re.IGNORECASE
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
#: El «a la» es OPCIONAL. La prensa chilena escribe las dos formas —«a la altura
#: del terminal» y «altura del terminal», esta última sobre todo en titulares y
#: en captions, donde se ahorra la preposición— y sólo la larga estaba
#: contemplada. Con la corta, la referencia entera se quedaba dentro de
#: `street_1` («Av. Argentina, altura del terminal de buses») y la consulta
#: pedía una calle con ese nombre, que no existe.
_REFERENCE_SPLIT = re.compile(
    r",?\s+(?:(?:a la\s+)?altura del?|frente a[l]?|cerca de[l]?|"
    r"en el sector del?|sector del?)\s+",
    re.IGNORECASE,
)

#: Preposición que introduce el lugar dentro de la oración. Se prueban todas, en
#: orden, hasta que una dé una calle — no sólo la primera.
#:
#: Tomar la primera y quedarse con lo que sigue bastaba para un aviso del MTT.
#: En un titular de prensa, no: "Incendio **en** Viña del Mar: … consumió una
#: casa **en** el sector de Miraflores Alto" tiene la comuna detrás de la
#: primera y el lugar detrás de la segunda. Quedarse con la primera producía
#: `street_1 = "Viña del Mar:"`, y Nominatim devolvía la ciudad entera: un pin
#: en la plaza para una casa que ardía a 2 km.
_PLACE_LEADS = re.compile(
    r"\b(?:en|sobre|a la altura del?|frente a[l]?|cerca de[l]?)\s+",
    re.IGNORECASE,
)

#: "Bomberos concurre **hasta** calle Once": la forma en que la prensa escribe
#: el despacho. Sólo delante de una palabra de vía, porque "hasta las 20 horas"
#: y "hasta Santiago" también existen; sólo si ninguna preposición de
#: `_PLACE_LEADS` dio una calle; y nunca detrás de un "desde", porque "desde Av.
#: España hasta Av. Argentina" es un tramo y su extremo no es el lugar del hecho.
_PLACE_LEAD_HASTA = re.compile(
    r"\bhasta\s+(?=(?:la\s+)?(?:calle|avenida|avda|av|pasaje|psje|pje|camino|ruta)\b)",
    re.IGNORECASE,
)
_TRAMO_DESDE = re.compile(r"\bdesde\b", re.IGNORECASE)

#: Lo que el extractor llegó a devolver como `street_1` y no nombra ninguna vía:
#: un artículo suelto ("en **el** sector de Miraflores Alto" deja "el" delante
#: de la referencia) o una palabra de lugar genérica. Una consulta con eso
#: resuelve a cualquier punto de la región, y ese punto parece un dato.
_NOT_A_STREET = frozenset(
    {
        "el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "de", "del",
        "este", "esta", "dicho", "dicha", "su", "sus", "comuna", "ciudad",
        "sector", "zona", "lugar", "area", "region", "calle", "via", "domicilio",
    }
)

#: Corta el lugar cuando empieza la parte narrativa del aviso.
#:
#: `sentido` entra porque «Ruta 68, sentido a Santiago» es una dirección de
#: circulación, no un lugar: dejarla dentro ensucia la consulta a Nominatim con
#: un nombre de ciudad que está a 100 km del hecho.
#:
#: Los dos puntos cortan igual que el punto: "Incendio en Viña del Mar: equipos
#: de emergencia…" es la forma de un titular, y lo que sigue a los dos puntos es
#: la bajada, no más lugar.
_PLACE_STOP = re.compile(
    r"\s*[.;:]|\s+(?:transito|tránsito|se recomienda|precaucion|precaución|"
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

#: Marcas que se escriben en `raw_data._extraction` cuando un aviso NO se
#: extrajo o NO se geocodificó por falta de cupo en ESA corrida. El delta no
#: reutiliza un aviso marcado: la corrida siguiente vuelve a intentarlo.
PENDIENTE_EXTRACCION = "extraccion_pendiente"
PENDIENTE_GEOCODIFICACION = "geocodificacion_pendiente"


def external_id_de(notice: TrafficNotice, tipo: EventType) -> str:
    """El `external_id` de un aviso. Lo usan el delta y `normalize()`.

    **El prefijo de los accidentes NO cambia, y eso no es pereza.** Es la clave
    de idempotencia: uniformar a `mtt:<tipo>:<hash>` habría reinsertado en la
    primera corrida tras el despliegue **todos** los accidentes que el sistema ya
    conocía, con identificador nuevo — duplicados que además se corroboran entre
    sí en el motor. La capa táctica es nueva y lleva su prefijo propio.
    """
    if tipo is EventType.ACCIDENT:
        return f"mtt:{notice.notice_id}"
    return f"mtt:closure:{notice.notice_id}"


def texto_md5(texto: str) -> str:
    """md5 del texto tal como queda guardado (`EventCreate` le hace `strip`)."""
    return hashlib.md5(texto[:10_000].strip().encode("utf-8")).hexdigest()


def reutilizable(previo: Any, texto: str) -> bool:
    """¿El aviso ya está guardado con el mismo texto y su extracción completa?

    El mismo tuit llega en varias entregas seguidas del webhook, y cada una
    volvería a pagar Gemini y Nominatim. Se reutiliza lo guardado salvo que el
    texto haya cambiado o que la corrida que lo guardó se haya quedado sin cupo
    para él (ver `PENDIENTE_*`).
    """
    if previo is None or previo.texto_md5 != texto_md5(texto):
        return False
    extraccion = previo.raw_data.get("_extraction")
    if not isinstance(extraccion, Mapping):
        return False
    return not (extraccion.get(PENDIENTE_EXTRACCION) or extraccion.get(PENDIENTE_GEOCODIFICACION))


def calles_guardadas(raw_data: Mapping[str, Any]) -> dict[str, Any]:
    """La extracción guardada, tal cual (incluye el `mode` con que se hizo)."""
    extraccion = raw_data.get("_extraction")
    return dict(extraccion) if isinstance(extraccion, Mapping) else {}


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

    Prueba cada preposición de `_PLACE_LEADS` en orden y se queda con la primera
    que nombre una vía; recién si ninguna lo hace, prueba `_PLACE_LEAD_HASTA`.
    Una preposición seguida sólo de una comuna ("Incendio en Viña del Mar: …")
    no da calle, pero sí da la ciudad, y se guarda para la vía que aparezca
    después.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return None

    # A partir de acá se trabaja con los puntos de las abreviaturas neutralizados;
    # se restauran al construir el resultado.
    protected = _protect_abbreviations(cleaned)

    hastas = [
        lead
        for lead in _PLACE_LEAD_HASTA.finditer(protected)
        if not _TRAMO_DESDE.search(protected[: lead.start()])
    ]
    ciudad_vista: str | None = None
    for orden, lead in enumerate((*_PLACE_LEADS.finditer(protected), *hastas)):
        streets, ciudad = _streets_from_place(protected[lead.end():])
        # La primera preposición se acepta como siempre —es lo que el MTT ya
        # resolvía y no se toca—. Las siguientes tienen que PARECER una vía:
        # detrás de un "en" cualquiera hay de todo ("envuelta en llamas,
        # existiendo además riesgo…"), y una calle inventada es peor que
        # ninguna.
        if streets is not None and (orden == 0 or _starts_like_street(streets["street_1"])):
            if not streets["city"] and ciudad_vista:
                streets["city"] = ciudad_vista
            return streets
        ciudad_vista = ciudad_vista or ciudad
    return None


#: Palabras con que empieza una vía escrita en minúscula ("hasta calle once").
_STREET_WORDS = frozenset(
    {
        "calle", "avenida", "av", "avda", "pasaje", "psje", "pje", "ptje",
        "camino", "ruta", "autopista", "carretera", "diagonal", "paseo",
        "subida", "bajada", "callejon", "escala", "escalera", "troncal",
    }
)


def _starts_like_street(value: str | None) -> bool:
    """¿Empieza como una vía? Nombre propio, número o palabra de `_STREET_WORDS`."""
    texto = (value or "").strip()
    if not texto:
        return False
    if texto[:1].isupper() or texto[:1].isdigit():
        return True
    primera = normalise_text(texto).split()[0].strip(".")
    return primera in _STREET_WORDS


def _is_street_name(value: str | None) -> bool:
    """¿`value` puede ser el nombre de una vía? Ver `_NOT_A_STREET`."""
    palabras = normalise_text(value or "").strip(" ,.:;").split()
    if not palabras:
        return False
    if " ".join(palabras) in _KNOWN_CITIES:
        return False
    return not set(palabras) <= _NOT_A_STREET


def _streets_from_place(place: str) -> tuple[dict[str, Any] | None, str | None]:
    """Lo que sigue a una preposición → `(calles|None, ciudad|None)`.

    La ciudad se devuelve aparte y aunque no haya calle: "en Viña del Mar" no
    nombra una vía, pero dice dónde buscar la que venga después.
    """
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
        return (None, _restore_abbreviations(city))
    if normalise_text(street_part) in _KNOWN_CITIES:
        # "en Viña del Mar": una comuna, no una calle.
        return (None, _restore_abbreviations(city or street_part))

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
    if not street_1 or not _is_street_name(street_1):
        return (None, _restore_abbreviations(city))

    if city is None and reference:
        reference, city = _split_trailing_city(reference)

    streets: dict[str, Any] = {
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
    return (streets, streets["city"])


def _split_trailing_city(value: str) -> tuple[str | None, str | None]:
    """Separa una comuna pegada al final de un nombre de calle."""
    normalised = normalise_text(value)
    for city in sorted(_KNOWN_CITIES, key=len, reverse=True):
        if normalised.endswith(city) and normalised != city:
            cut = len(value) - len(city)
            head = value[:cut].strip(" ,.")
            return (head or None, value[cut:].strip(" ,."))
    return (value, None)


@dataclass(slots=True)
class ResumenResolucion:
    """Lo que `resolver_avisos` dejó: los avisos resueltos y lo que gastó."""

    resueltos: list[ResolvedNotice]
    llamadas_llm: int = 0
    geocodificados: int = 0
    reutilizados: int = 0


async def resolver_avisos(
    clasificados: Sequence[tuple[TrafficNotice, EventType]],
    *,
    claves: Sequence[str],
    conocidos: Mapping[str, Any],
    max_llm_calls: int,
    max_geocodes: int,
    avisar: Callable[[str], None],
) -> ResumenResolucion:
    """Avisos ya clasificados → `(aviso, tipo, extracción, punto)`.

    Delta contra lo guardado, Gemini con su tope, Nominatim con el suyo y las
    marcas de pendiente.

    `claves` va en el mismo orden que `clasificados` (es su `external_id`).
    `avisar` recibe las degradaciones (`BaseCollector.warn` o equivalente).
    """
    resolved: list[ResolvedNotice] = []
    geocoded = 0
    llm_calls = 0
    reutilizados = 0

    # Un solo cliente para todas las llamadas a Nominatim: reutiliza la
    # conexión TLS y, sobre todo, mantiene un único User-Agent identificable,
    # que es parte del contrato de uso del servicio.
    async with build_client() as geo_client:
        for (notice, tipo), clave in zip(clasificados, claves, strict=True):
            previo = conocidos.get(clave)
            if previo is not None and reutilizable(previo, notice.text):
                resolved.append(
                    (
                        notice,
                        tipo,
                        calles_guardadas(previo.raw_data),
                        GeocodeResult.desde_dict(previo.raw_data.get("_geocoding")),
                    )
                )
                reutilizados += 1
                continue

            if llm_calls >= max_llm_calls:
                avisar(
                    f"se alcanzó el tope de {max_llm_calls} llamadas al "
                    f"modelo por corrida; el resto queda sin ubicación"
                )
                # Marcado para que la corrida siguiente lo intente: sin la
                # marca, el delta lo daría por resuelto para siempre.
                resolved.append((notice, tipo, {PENDIENTE_EXTRACCION: True}, None))
                continue

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
            if geocoded < max_geocodes and streets.get("street_1"):
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
                    avisar(
                        f"Nominatim falló para un aviso "
                        f"({type(exc).__name__}): {exc}"
                    )
                    # El contador igual avanza: un servicio que falla consumió
                    # su segundo de rate limit lo mismo que uno que responde.
                    geocoded += 1
            elif geocoded >= max_geocodes:
                avisar(
                    f"se alcanzó el tope de {max_geocodes} geocodificaciones "
                    f"por corrida; el resto queda sin coordenadas"
                )
                streets = {**streets, PENDIENTE_GEOCODIFICACION: True}

            resolved.append((notice, tipo, streets, point))

    return ResumenResolucion(
        resueltos=resolved,
        llamadas_llm=llm_calls,
        geocodificados=geocoded,
        reutilizados=reutilizados,
    )


def eventos_de_avisos(
    records: Sequence[ResolvedNotice],
    *,
    collector: str,
    avisar: Callable[[str], None],
    ahora: datetime | None = None,
) -> list[EventCreate]:
    """Avisos resueltos → `EventCreate`. Pura: sin red y sin base.

    `collector` queda en `raw_data._collector` para saber de dónde vino.
    """
    now = ahora or datetime.now(UTC)
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
                external_id=external_id_de(notice, tipo),
                confidence=(
                    TRANSPORTE_INFORMA_CONFIDENCE
                    if tipo is EventType.ACCIDENT
                    else ROAD_CLOSURE_CONFIDENCE
                ),
                raw_data={
                    **dict(notice.raw),
                    "comuna": streets.get("city"),
                    "_collector": collector,
                    # Los dos pasos quedan separados y auditables: qué leyó el
                    # extractor y qué resolvió el geocodificador. Si mañana un
                    # punto está mal, esto dice cuál de los dos falló.
                    "_extraction": {
                        **streets,
                        # Una extracción reutilizada conserva el modo con
                        # que se hizo; una nueva lleva el de ahora.
                        "mode": streets.get("mode")
                        or (
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
        avisar(
            f"{sin_punto} accidentes quedaron sin coordenadas; no entran al "
            f"Paso A del motor pero sí quedan registrados"
        )
    return events


__all__ = [
    "PENDIENTE_EXTRACCION",
    "PENDIENTE_GEOCODIFICACION",
    "ROAD_CLOSURE_CONFIDENCE",
    "TRANSPORTE_INFORMA_CONFIDENCE",
    "ResolvedNotice",
    "ResumenResolucion",
    "TrafficNotice",
    "calles_guardadas",
    "eventos_de_avisos",
    "external_id_de",
    "extract_streets_heuristic",
    "extract_streets_via_llm",
    "looks_like_accident",
    "looks_like_road_ops",
    "resolver_avisos",
    "reutilizable",
    "texto_md5",
]
