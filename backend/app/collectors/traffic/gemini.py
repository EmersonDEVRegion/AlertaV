"""Extracción de entidades con Gemini, aislada del collector que la usa.

Qué hace y qué NO hace
----------------------
Recibe la prosa de un aviso de tránsito y devuelve las calles y la ciudad. Eso
es todo. El modelo **no** decide si hubo un accidente, no juzga gravedad y no
infiere coordenadas: esas tres cosas las resuelve código determinista y
auditable, aguas arriba y aguas abajo.

Esa frontera no es purismo. Un LLM que se equivoca extrayendo una calle produce
una geocodificación fallida o un punto discutible —visible, marcado en
`raw_data._geocoding`—. Un LLM al que se le permitiera decidir "esto es un
accidente" podría inventar un siniestro que nadie reportó, y eso llegaría al
mapa como un hecho. Se le da la tarea donde sus errores son baratos.

Por qué `google-genai` y no `google-generativeai`
-------------------------------------------------
`google-generativeai` está archivado. Su repositorio se llama hoy
`deprecated-generative-ai-python`, no recibe modelos nuevos, y los módulos
generativos de Vertex que lo acompañaban se eliminaron el 24 de junio de 2026.
El SDK unificado (`google-genai`) es el que sigue vivo, y además trae lo que
este módulo necesita: `client.aio`, un cliente **async nativo**.

Cómo se evita bloquear el event loop
------------------------------------
Con `client.aio.models.generate_content(...)`, que es una corrutina de verdad:
por debajo usa httpx asíncrono y cede el control mientras espera la respuesta de
Google. No hay `asyncio.to_thread` ni hilos de por medio, y esa diferencia
importa acá: los collectors comparten event loop con el motor de correlación
dentro de un único intérprete (ver `app/workers.py`), así que una llamada
bloqueante de 2 segundos congelaría también la correlación. Un pool de hilos lo
evitaría, pero a cambio de un hilo por llamada en una instancia de 512 MB.

La única parte bloqueante es construir el `Client`, que se hace una vez y se
cachea.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache
from typing import Any

from app.collectors.vocabulary import (
    CLAVE_MEANINGS,
    NON_INCIDENT_CODES,
    clave_label,
    clave_meaning,
    find_claves,
    normalise_code,
    resolve_clave,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Modos de extracción. Quedan escritos en `raw_data._extraction.mode` de cada
#: señal para poder separar en la base lo extraído por el modelo de lo extraído
#: por reglas — sin esa marca, comparar ambos enfoques sería imposible
#: retroactivamente.
MODE_GEMINI = "gemini"
MODE_HEURISTIC = "heuristic"

#: Instrucción de sistema. Cada línea responde a una forma concreta de fallar.
#:
#: * "Responde SOLO con JSON" y la prohibición explícita de ``` cubren el
#:   reflejo más común del modelo: envolver la respuesta en un bloque de código
#:   markdown. Se refuerza con `response_mime_type` y se limpia igual al parsear:
#:   tres capas para el mismo fallo, porque es el que más veces ocurre.
#: * "No inventes" es la línea que más importa. Una calle alucinada geocodifica
#:   a un punto plausible y falso, que es peor que no tener ubicación: lo
#:   segundo se ve en el mapa como un aviso sin marcador, lo primero no se ve
#:   nunca.
#: * "No traduzcas ni corrijas" evita que "Av. Espana" se convierta en
#:   "Avenida España" — Nominatim resuelve mejor el texto tal como lo escribió
#:   la fuente, y además reescribir el dato rompe la trazabilidad.
#: * "street_2 sólo si hay intersección explícita" evita el relleno: ante un
#:   tramo de ruta sin cruce, el modelo tiende a inventar una transversal
#:   plausible para no dejar el campo vacío.
SYSTEM_INSTRUCTION = """\
Eres un extractor de entidades geográficas para un sistema de emergencias de la \
Región de Valparaíso, Chile. Recibes el texto de un aviso de tránsito y devuelves \
únicamente las vías mencionadas.

Responde SOLO con un objeto JSON válido. Sin markdown, sin ```json, sin \
explicaciones, sin texto antes ni después.

Estructura exacta:
{"street_1": "...", "street_2": "..." | null, "city": "..." | null}

Reglas:
- street_1: la vía principal donde ocurre el hecho. Si el aviso no nombra \
ninguna vía, devuelve null.
- street_2: la vía transversal SOLO si el aviso menciona explícitamente un \
cruce, esquina o intersección. Si no lo menciona, null. No la inventes.
- city: la comuna o ciudad si el aviso la nombra. Si no, null.
- No inventes datos. Ante la duda, null.
- No traduzcas, no corrijas ortografía y no expandas abreviaturas: copia los \
nombres tal como aparecen en el texto.
- No devuelvas coordenadas, ni latitud, ni longitud, bajo ninguna circunstancia.\
"""

#: Esquema que se le pasa al modelo. Con esto la API restringe la generación al
#: formato pedido en vez de confiar en que obedezca el prompt: es la diferencia
#: entre pedir JSON y garantizarlo.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "street_1": {"type": "STRING", "nullable": True},
        "street_2": {"type": "STRING", "nullable": True},
        "city": {"type": "STRING", "nullable": True},
    },
    "required": ["street_1", "street_2", "city"],
}

#: Última línea de defensa por si el modelo igual envuelve la respuesta.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)

#: Un aviso de tránsito son dos o tres frases. Un texto mucho más largo es un
#: bloque de la página mal recortado, y mandarlo entero sería pagar tokens por
#: ruido y darle al modelo más superficie para alucinar.
MAX_INPUT_CHARS = 1500


class GeminiUnavailableError(RuntimeError):
    """No hay cliente: falta la clave o el SDK. No es un error de la llamada."""


@lru_cache(maxsize=1)
def _client() -> Any:
    """Cliente cacheado. Construirlo es lo único bloqueante del módulo.

    Se hace una vez por proceso y fuera del camino caliente. `lru_cache` sobre
    una función sin argumentos es la forma más corta de un singleton perezoso
    que además se puede limpiar en los tests con `_client.cache_clear()`.
    """
    if not settings.GEMINI_API_KEY.strip():
        raise GeminiUnavailableError("GEMINI_API_KEY no está configurada")

    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover — está en requirements-prod
        raise GeminiUnavailableError(
            "google-genai no está instalado. Ojo: el paquete es `google-genai`, "
            "no `google-generativeai`, que está archivado."
        ) from exc

    return genai.Client(api_key=settings.GEMINI_API_KEY.strip())


def is_configured() -> bool:
    return bool(settings.GEMINI_API_KEY.strip())


def _strip_fence(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group("body") if match else raw.strip()


def response_text(response: Any) -> str:
    """Texto de una respuesta del SDK, ignorando las partes que no son texto.

    Reemplaza a `response.text`, que es el accesor de conveniencia del SDK y el
    origen de este aviso en cada corrida:

        Warning: there are non-text parts in the response: ['thought_signature']

    Qué está pasando, porque el aviso no lo dice: los modelos con razonamiento
    —la familia Flash/Pro actual— devuelven la respuesta partida en varias
    `Part`. Una lleva el JSON que pedimos; las otras llevan el rastro del
    pensamiento (`thought_signature`, y a veces un resumen marcado con
    `thought=True`). `response.text` está definido para el caso simple de una
    sola parte de texto, así que ante esa mezcla concatena lo que puede y avisa
    por `warnings` de todo lo que descartó.

    **El aviso es correcto y la respuesta también**: nunca perdimos un despacho
    por esto. Pero un `warning` que se repite en cada llamada es exactamente lo
    que entrena a un equipo a ignorar los avisos del log, y el día que el modelo
    devuelva algo raro de verdad, nadie lo va a ver entre estos.

    Lo que se hace acá es recorrer las partes a mano y quedarse sólo con las de
    texto real:

      * `part.text` que no sea una cadena → no es texto (una firma de
        pensamiento es `bytes`, una llamada a función es un objeto). Fuera.
      * `part.thought` en `True` → es el resumen del razonamiento, no la
        respuesta. Fuera: concatenarlo al JSON lo volvería imparseable.

    El respaldo sigue siendo `response.text`, para una respuesta sin
    `candidates` o de un SDK cuya forma cambie. Se lee dentro de
    `catch_warnings` porque el punto de esta función es que el aviso deje de
    aparecer, y el respaldo es justamente el camino que lo emite.
    """
    partes: list[str] = []

    for candidate in getattr(response, "candidates", None) or ():
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or ():
            # `thought=True` marca el resumen del razonamiento. Es texto de
            # verdad, y por eso hay que descartarlo explícitamente: si se colara,
            # el `json.loads` de aguas abajo recibiría prosa antes del objeto.
            if getattr(part, "thought", False):
                continue
            texto = getattr(part, "text", None)
            if isinstance(texto, str) and texto:
                partes.append(texto)

    if partes:
        return "".join(partes)

    # Sin candidatos utilizables: puede ser un bloqueo del filtro de seguridad
    # (respuesta sin `content`) o una forma que este código no previó. El
    # accesor del SDK es la mejor conjetura que queda.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        texto = getattr(response, "text", None)
    return texto if isinstance(texto, str) else ""


def parse_response(raw: str) -> dict[str, Any] | None:
    """Texto crudo del modelo → dict validado. None si no sirve.

    Devolver None es un resultado legítimo y esperado, no una excepción: el aviso
    sigue su curso sin coordenadas. Se rechaza en tres casos, y los tres son
    formas de alucinación observadas en modelos pequeños:

    1. **No es JSON.** Prosa, disculpas, JSON a medias.
    2. **No es un objeto.** Una lista, un número, `null`.
    3. **Falta la vía principal.** Sin `street_1` no hay nada que geocodificar,
       y quedarse con la ciudad sola devolvería el centroide comunal — una
       ubicación que parece un dato y no lo es.
    """
    body = _strip_fence(raw)
    if not body:
        return None

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "Gemini devolvió algo que no es JSON; el aviso queda sin ubicación",
            extra={"muestra": body[:200]},
        )
        return None

    if not isinstance(payload, dict):
        logger.warning(
            "Gemini devolvió un JSON que no es un objeto",
            extra={"tipo": type(payload).__name__, "muestra": body[:200]},
        )
        return None

    street_1 = _clean(payload.get("street_1"))
    if not street_1:
        # No es un fallo del modelo: hay avisos que legítimamente no nombran una
        # vía ("Restricción vehicular en la región"). Se registra en debug para
        # no llenar los logs de ruido esperable.
        logger.debug("el aviso no nombra una vía principal", extra={"muestra": body[:200]})
        return None

    return {
        "street_1": street_1,
        "street_2": _clean(payload.get("street_2")),
        "city": _clean(payload.get("city")),
    }


def _clean(value: Any) -> str | None:
    """Normaliza un campo del modelo a `str` no vacío o None.

    Los modelos devuelven el vacío de varias formas y todas significan lo mismo:
    `null`, `""`, `"null"`, `"N/A"`, `"desconocido"`.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() in {"null", "none", "n/a", "na", "desconocido", "-"}:
        return None
    return text


async def extract_streets(text: str) -> dict[str, Any] | None:
    """Llama a Gemini y devuelve `{street_1, street_2, city}` o None.

    **Nunca lanza.** Cualquier fallo —clave ausente, cuota agotada, timeout,
    caída de Google, respuesta alucinada— se registra y devuelve None. El aviso
    entra igual al sistema, sin coordenadas: perder un accidente informado por
    el MTT porque un servicio de terceros tuvo un mal minuto sería el peor
    intercambio posible.
    """
    payload = " ".join(str(text or "").split())[:MAX_INPUT_CHARS]
    if not payload:
        return None

    try:
        client = _client()
    except GeminiUnavailableError as exc:
        logger.warning("extracción con Gemini no disponible: %s", exc)
        return None

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            # `client.aio` es la cara asíncrona del SDK: una corrutina real, no
            # un wrapper sobre hilos. Ver el docstring del módulo.
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    # Fuerza el formato en el decodificador, no en el prompt.
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=settings.GEMINI_TEMPERATURE,
                    # La respuesta son tres campos cortos. Un tope bajo acota el
                    # costo y corta en seco cualquier intento de explicarse.
                    max_output_tokens=256,
                ),
            ),
            # Cinturón sobre el timeout del SDK: si algún día cambia su valor por
            # defecto, la corrida no se queda colgada indefinidamente.
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Gemini no respondió dentro del plazo; el aviso queda sin ubicación",
            extra={"timeout_s": settings.GEMINI_TIMEOUT_SECONDS},
        )
        return None
    except Exception as exc:
        logger.warning(
            "la llamada a Gemini falló; el aviso queda sin ubicación",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return None

    # `response_text` y no `response.text`: los modelos con razonamiento
    # devuelven además la firma del pensamiento, y el accesor del SDK avisa por
    # `warnings` en cada llamada. Ver el docstring de la función.
    raw = response_text(response)
    if not raw:
        # Pasa cuando el filtro de seguridad bloquea la respuesta. Un aviso de
        # accidente con heridos puede activarlo, así que no es un caso teórico.
        logger.warning(
            "Gemini respondió sin texto (¿filtro de seguridad?)",
            extra={"feedback": str(getattr(response, "prompt_feedback", ""))[:200]},
        )
        return None

    return parse_response(raw)


# =============================================================================
#  Segundo contrato: despachos con clave radial
# =============================================================================
#
# `extract_streets` recibe prosa del MTT o de un diario. Un despacho de la
# central no es prosa: es un formato telegráfico con clave, y el ejemplo real
# de @CGI_CBV es todo lo que hay —
#
#     81 * DIEGO COOK / GUACOLDA * CLAVE 12
#
# Ni verbo, ni preposición, ni comuna. `extract_streets` lo procesa igual, pero
# devuelve tres campos sueltos y pierde lo único que la central sí informó con
# precisión: qué despachó. De ahí este segundo camino.
#
# Qué se le pide al modelo y qué NO
# ---------------------------------
# Se le pide **parsear**: separar el número de carro del par de calles y de la
# clave, en un formato que cambia de un Cuerpo a otro y que ningún separador
# fijo cubre (`*`, `/`, `-`, `x`, `c/`, "esq.", a veces nada).
#
# NO se le pide **interpretar la clave**. Aunque el diccionario viaja en el
# prompt —el modelo necesita saber qué es una clave para reconocerla— el
# significado que llega al resumen se busca en `CLAVE_MEANINGS`, en código. Es
# la misma frontera que gobierna el módulo entero: si el modelo alucina una
# calle, se ve un punto discutible; si alucinara que 10-2 es "rescate
# vehicular", el resumen afirmaría con la autoridad de la central algo que la
# central no dijo. Un despacho de Bomberos entra al sistema con confianza 1.00
# (ver `bomberos_10_4_worker`), así que ahí no hay margen para una invención.
#
# La consecuencia práctica: el modelo puede devolver `clave` y `significado`, y
# `significado` se descarta siempre que la clave esté en el diccionario.

#: Diccionario renderizado para el prompt. Se construye desde `CLAVE_MEANINGS`
#: en tiempo de importación en vez de escribirse a mano, para que el prompt no
#: pueda divergir de la tabla que valida su salida. Ver el comentario de esa
#: tabla en `app/collectors/vocabulary.py`.
#:
#: Esa decisión se pagó sola el 2026-09-02: cuando se descubrió que las tablas
#: describían otro sistema de claves y se reemplazaron por la del CBV, el
#: prompt se actualizó sin que nadie lo tocara. Un glosario escrito a mano
#: habría seguido enseñándole al modelo que `10-4` es rescate vehicular.
#:
#: Va **ordenado por familia** y con las no-emergencias marcadas. Lo primero
#: porque el orden de inserción del diccionario deja "Clave 15" antes que
#: "Clave 7" y eso no ayuda a nadie a leer. Lo segundo importa más: sin la
#: marca, el modelo ve "Clave 12: Academia de Cuerpo" en la misma lista que un
#: incendio estructural y no tiene cómo saber que una es un hecho del mundo y la
#: otra una actividad interna del Cuerpo. Que igual se filtren en la ingesta no
#: quita que el modelo trabaje mejor sabiéndolo.
CLAVE_GLOSSARY: str = "\n".join(
    f"- {clave_label(code)}: {meaning}"
    + (" [NO es una emergencia: actividad interna del Cuerpo]"
       if code in NON_INCIDENT_CODES else "")
    for code, meaning in sorted(CLAVE_MEANINGS.items())
)

#: Plantilla del resumen. Una sola definición, usada por el prompt y por el
#: formateador determinista, para que el ejemplo que ve el modelo y la cadena
#: que se guarda no puedan describir formatos distintos.
SUMMARY_TEMPLATE = "({clave}) ({significado}) en ({ubicacion}) (Fuente: {fuente})"

#: Qué se escribe cuando el despacho no nombra ninguna vía. Explícito y no una
#: cadena vacía: un resumen con un paréntesis vacío parece un error de código,
#: y esto es un dato que la central no entregó.
UBICACION_DESCONOCIDA = "ubicación no informada"

#: Instrucción de sistema del camino de despachos.
#:
#: Hereda las prohibiciones del extractor de calles —nada de markdown, nada de
#: inventar, nada de corregir nombres— y agrega las tres que impone el formato
#: telegráfico:
#:
#: * **El número de carro no es una dirección.** "81" abre casi todos los avisos
#:   y es la unidad despachada. Sin decirlo, el modelo lo lee como altura de
#:   calle y produce "Diego Cook 81".
#: * **`/`, `*`, `x`, `c/` y "esq." son el separador de intersección**, no parte
#:   del nombre de la calle.
#: * **La comuna sale del texto o queda nula.** @CGI_CBV a veces la escribe en
#:   la imagen y no en el tuit; deducirla del nombre de la calle es exactamente
#:   la alucinación cara: "Guacolda" existe en cuatro comunas de la región.
DISPATCH_SYSTEM_INSTRUCTION = f"""\
Eres un decodificador de despachos radiales del Cuerpo de Bomberos de la Región \
de Valparaíso, Chile. Recibes el texto crudo de un despacho y devuelves sus \
partes.

Responde SOLO con un objeto JSON válido. Sin markdown, sin ```json, sin \
explicaciones, sin texto antes ni después.

Estructura exacta:
{{"clave": "..." | null, "significado": "..." | null, "street_1": "..." | null, \
"street_2": "..." | null, "city": "..." | null, "resumen": "..."}}

DICCIONARIO DE CLAVES (estándar de Bomberos de Chile). Es el único válido:
{CLAVE_GLOSSARY}

Notas del diccionario:
- Las claves admiten sufijo de subtipo: 10-4-1 es un 10-4.
- El cero intermedio es separador de familia: 10-0-4 es 10-4.
- "CLAVE 12" y "10-12" son la misma clave.
- Las claves de la familia 3 piden un recurso (Carabineros, ambulancia, \
empresa eléctrica); no describen el siniestro. Si el despacho trae una clave de \
familia 10 y además una de familia 3, la clave del despacho es la de familia 10.

Reglas de extracción:
- clave: la clave del despacho, copiada tal como aparece. Si no hay ninguna, null.
- significado: el texto que este diccionario asigna a esa clave. Si la clave no \
está en el diccionario, null. No inventes significados ni traduzcas los de arriba.
- street_1 y street_2: las vías del despacho. Los separadores "*", "/", "x", \
"c/", "con" y "esq." indican intersección: lo que va a cada lado es una vía \
distinta. street_2 sólo si hay intersección explícita; si no, null.
- El número suelto al inicio del despacho es la unidad o carro despachado \
(ej. "81"), NO una altura de calle y NO parte del nombre de la vía. Descártalo.
- city: la comuna sólo si el texto la nombra. NUNCA la deduzcas del nombre de \
la calle: el mismo nombre de calle existe en varias comunas de la región.
- No inventes datos. Ante la duda, null.
- No traduzcas, no corrijas ortografía y no expandas abreviaturas en los nombres \
de vías: cópialos tal como aparecen.
- No devuelvas coordenadas, ni latitud, ni longitud, bajo ninguna circunstancia.

Formato de resumen (campo "resumen"), exacto y sin variantes:
{
    SUMMARY_TEMPLATE.format(
        clave="Clave",
        significado="Significado de la clave",
        ubicacion="Intersección o dirección, Comuna",
        fuente="cuenta de origen",
    )
}

Ejemplo completo:
Entrada: 81 * DIEGO COOK / GUACOLDA * CLAVE 12
Salida: {{"clave": "Clave 12", "significado": "Llamado a servicio especial", \
"street_1": "DIEGO COOK", "street_2": "GUACOLDA", "city": null, "resumen": \
"(Clave 12) (Llamado a servicio especial) en (Diego Cook con Guacolda) \
(Fuente: @CGI_CBV)"}}\
"""

#: Esquema del camino de despachos. Igual que el de calles: restringe la
#: generación en el decodificador en vez de confiar en que el prompt se cumpla.
DISPATCH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "clave": {"type": "STRING", "nullable": True},
        "significado": {"type": "STRING", "nullable": True},
        "street_1": {"type": "STRING", "nullable": True},
        "street_2": {"type": "STRING", "nullable": True},
        "city": {"type": "STRING", "nullable": True},
        "resumen": {"type": "STRING", "nullable": True},
    },
    "required": ["clave", "significado", "street_1", "street_2", "city", "resumen"],
}

#: Partículas que quedan en minúscula al componer un nombre de vía.
#:
#: La lista es corta a propósito, y el recorte costó una pasada: los artículos
#: —"la", "los", "las", "el"— parecen pertenecer acá y **no** pertenecen. En el
#: callejero chileno son parte del nombre propio y van con mayúscula: Camino La
#: Pólvora, Los Placeres, Las Zorras, El Salto. Bajarlos producía "Camino la
#: Pólvora", que está mal escrito y además le da a Nominatim una cadena que no
#: coincide con la del catastro.
#:
#: Quedan sólo las preposiciones y conjunciones, que sí van en minúscula dentro
#: de un nombre: "Doce de Febrero", "Cinco de Abril".
_PARTICULAS = frozenset({"de", "del", "y", "e"})


def _titlecase_via(nombre: str) -> str:
    """MAYÚSCULAS de radio → nombre de calle legible.

    La central escribe todo en mayúsculas porque el despacho se lee en pantalla
    de consola. Un resumen destinado a una persona no puede gritar.

    Se aplica **sólo al resumen**. Los campos `street_1`/`street_2` que van al
    geocodificador conservan el texto original: Nominatim resuelve mejor lo que
    escribió la fuente, y reescribir el dato de origen rompe la trazabilidad.

    Sólo toca los tokens que están enteramente en mayúsculas. Un nombre que ya
    viene en capitalización mixta ("Av. Argentina") lo escribió alguien con
    cuidado, y corregirlo sólo puede empeorarlo.
    """
    palabras = nombre.split()
    salida: list[str] = []
    for indice, palabra in enumerate(palabras):
        if not palabra.isupper():
            salida.append(palabra)
            continue
        minuscula = palabra.lower()
        # La partícula inicial sí se capitaliza: "Los Placeres", no "los
        # Placeres". Sólo pierde la mayúscula la que va dentro del nombre.
        if indice > 0 and minuscula in _PARTICULAS:
            salida.append(minuscula)
        else:
            salida.append(minuscula[:1].upper() + minuscula[1:])
    return " ".join(salida)


def build_location(street_1: str | None, street_2: str | None, city: str | None) -> str:
    """Las tres piezas geográficas → el paréntesis de ubicación del resumen.

    "Diego Cook con Guacolda, Valparaíso". La conjunción es "con" y no "/" ni
    "esq." porque es como se dice una intersección en Chile, y el resumen lo lee
    una persona, no un parser.

    Sin vía principal no hay ubicación: devolver sólo la comuna diría "en
    (Valparaíso)", que suena a un dato y no lo es —la central informó una
    esquina, no una comuna entera—.
    """
    if not street_1:
        return UBICACION_DESCONOCIDA

    vias = _titlecase_via(street_1)
    if street_2:
        vias = f"{vias} con {_titlecase_via(street_2)}"
    if city:
        return f"{vias}, {_titlecase_via(city)}"
    return vias


def format_dispatch_summary(
    *,
    clave: str | None,
    street_1: str | None,
    street_2: str | None,
    city: str | None,
    source_handle: str,
    significado: str | None = None,
) -> str:
    """Campos validados → el resumen canónico. Determinista, sin red.

    **Esta función fija el formato de salida, no el prompt.** El prompt le pide
    el resumen al modelo y el modelo suele acertar, pero "suele" no es un
    contrato: la cadena que se guarda se arma acá, con `SUMMARY_TEMPLATE`, a
    partir de campos ya validados. Así el formato es testeable sin llamar a la
    API y no puede derivar con un cambio de modelo.

    El `significado` se resuelve contra `CLAVE_MEANINGS` y el que haya devuelto
    el modelo sólo se usa cuando la clave **no** está en el diccionario —el caso
    de una clave nueva que la central empezó a despachar y nadie registró—.
    Incluso ahí queda marcado por la vía de siempre: `raw_data._extraction`.

    El formato exacto, sobre el despacho real que motivó este camino:

        (Clave 12) (Llamado a servicio especial)
        en (Diego Cook con Guacolda, Valparaíso) (Fuente: @CGI_CBV)

    —en una sola línea. Lo fija `test_el_despacho_real_produce_el_resumen_pedido`
    carácter por carácter; acá va partido sólo para no pasar los 100 caracteres.
    """
    etiqueta = "Sin clave"
    texto_clave = " ".join(str(clave or "").split())
    resuelto = resolve_clave(texto_clave) if texto_clave else None

    # Anotada explícitamente y no inferida de la primera asignación. Sin la
    # anotación, mypy fija `canonico: str` en la rama de `resuelto` —que es un
    # `tuple[str, str]`— y después marca la rama de abajo, donde
    # `clave_meaning` devuelve `str | None` por diseño: una clave que no está
    # en `CLAVE_MEANINGS` no tiene significado que dar, y ese None es justo el
    # caso que el bloque siguiente existe para cubrir. El tipo verdadero de la
    # variable es la unión; lo que estaba mal era dejarlo implícito.
    canonico: str | None
    if resuelto is not None:
        etiqueta, canonico = resuelto
    else:
        # El modelo puede devolver la clave sin la palabra "clave" delante
        # ("12"), que `resolve_clave` no reconoce porque un número suelto en un
        # despacho es casi siempre el carro. Acá el campo YA está aislado, así
        # que el número suelto sí es la clave.
        codigo = normalise_code(texto_clave) or (
            (int(texto_clave),) if texto_clave.isdigit() and len(texto_clave) <= 2 else None
        )
        canonico = clave_meaning(codigo) if codigo is not None else None
        if codigo is not None:
            etiqueta = clave_label(codigo)
        elif texto_clave:
            etiqueta = texto_clave

    if canonico is None:
        # Clave fuera del diccionario. Se acepta lo del modelo porque la
        # alternativa —"Clave desconocida"— borraría información que la central
        # sí dio, pero se avisa: es la señal de que hay que agregar una entrada
        # a `CLAVE_MEANINGS`.
        canonico = " ".join(str(significado or "").split()) or "Clave no reconocida"
        if texto_clave:
            logger.info(
                "clave fuera de CLAVE_MEANINGS; el significado viene del modelo",
                extra={"clave": texto_clave, "significado": canonico},
            )

    return SUMMARY_TEMPLATE.format(
        clave=etiqueta,
        significado=canonico,
        ubicacion=build_location(street_1, street_2, city),
        fuente=source_handle,
    )


def parse_dispatch_response(raw: str, *, source_handle: str) -> dict[str, Any] | None:
    """Texto crudo del modelo → dict validado con `resumen`. None si no sirve.

    Mismo criterio que `parse_response` —ante la duda, None— con una diferencia
    que importa: acá **la clave sola basta**. Un despacho sin calle reconocible
    sigue siendo una emergencia informada por la central con confianza 1.00, y
    tirarlo por no poder ubicarlo perdería la señal más fuerte del sistema. Lo
    que se pierde sin `street_1` es el marcador, no el evento.

    Se rechaza sólo lo que no aporta nada: respuesta que no es JSON, que no es
    un objeto, o que no trae ni clave ni vía.
    """
    body = _strip_fence(raw)
    if not body:
        return None

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "Gemini devolvió algo que no es JSON al decodificar un despacho",
            extra={"muestra": body[:200]},
        )
        return None

    if not isinstance(payload, dict):
        logger.warning(
            "Gemini devolvió un JSON que no es un objeto al decodificar un despacho",
            extra={"tipo": type(payload).__name__, "muestra": body[:200]},
        )
        return None

    clave = _clean(payload.get("clave"))
    street_1 = _clean(payload.get("street_1"))
    if not clave and not street_1:
        logger.debug("el despacho no trae ni clave ni vía", extra={"muestra": body[:200]})
        return None

    street_2 = _clean(payload.get("street_2"))
    city = _clean(payload.get("city"))
    significado = _clean(payload.get("significado"))

    resumen = format_dispatch_summary(
        clave=clave,
        significado=significado,
        street_1=street_1,
        street_2=street_2,
        city=city,
        source_handle=source_handle,
    )

    # El resumen del modelo no se guarda, pero sí se compara: una divergencia
    # sostenida delata que el prompt dejó de describir lo que el formateador
    # hace, y ese desfase no tiene otro síntoma.
    propuesto = _clean(payload.get("resumen"))
    if propuesto and propuesto != resumen:
        logger.debug(
            "el resumen del modelo difiere del canónico; se guarda el canónico",
            extra={"modelo": propuesto[:200], "canonico": resumen[:200]},
        )

    return {
        "clave": clave,
        "significado": significado,
        "street_1": street_1,
        "street_2": street_2,
        "city": city,
        "resumen": resumen,
    }


async def extract_dispatch(text: str, *, source_handle: str) -> dict[str, Any] | None:
    """Despacho crudo → `{clave, significado, street_1, street_2, city, resumen}`.

    **Nunca lanza**, por el mismo motivo que `extract_streets`: perder un
    despacho de la central porque un servicio de terceros tuvo un mal minuto
    sería el peor intercambio posible. Ante cualquier fallo devuelve None y el
    llamador cae a `dispatch_summary_heuristic`, que resuelve el formato
    telegráfico con reglas y sin red.
    """
    payload = " ".join(str(text or "").split())[:MAX_INPUT_CHARS]
    if not payload:
        return None

    try:
        client = _client()
    except GeminiUnavailableError as exc:
        logger.warning("decodificación de despachos no disponible: %s", exc)
        return None

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=DISPATCH_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=DISPATCH_RESPONSE_SCHEMA,
                    temperature=settings.GEMINI_TEMPERATURE,
                    # Seis campos cortos en vez de tres: el tope sube, pero
                    # sigue cortando en seco cualquier intento de explicarse.
                    max_output_tokens=512,
                ),
            ),
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Gemini no respondió al decodificar un despacho",
            extra={"timeout_s": settings.GEMINI_TIMEOUT_SECONDS},
        )
        return None
    except Exception as exc:
        logger.warning(
            "la llamada a Gemini falló al decodificar un despacho",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return None

    raw = response_text(response)
    if not raw:
        logger.warning(
            "Gemini respondió sin texto al decodificar un despacho (¿filtro de seguridad?)",
            extra={"feedback": str(getattr(response, "prompt_feedback", ""))[:200]},
        )
        return None

    return parse_dispatch_response(raw, source_handle=source_handle)


#: Separadores de intersección del formato telegráfico. El `*` de @CGI_CBV
#: delimita campos y la `/` separa las dos vías, pero no todos los Cuerpos usan
#: los mismos, así que la heurística acepta el conjunto completo.
_CAMPO_SPLIT = re.compile(r"\s*\*\s*")
_VIA_SPLIT = re.compile(r"\s*(?:/|\bcon\b|\besq\.?\b|\bc/\b|\bx\b)\s*", re.IGNORECASE)
#: Unidad despachada: el campo suelto que abre el aviso. "81", pero también
#: "B1", "BX 3", "M 5" y "R 12" — la nomenclatura de carros mezcla letra y
#: número según el tipo de máquina, y una versión que sólo reconociera dígitos
#: deja pasar "B1" como si fuera el nombre de la calle.
#:
#: Sólo aplica al campo COMPLETO. "GUACOLDA 81" es una altura de calle y sí es
#: una dirección; lo que se descarta es el campo que no contiene nada más.
#:
#: **Acepta varias unidades separadas por coma.** A una emergencia grande la
#: central despacha más de un carro y las lista juntas: el despacho real que
#: destapó las tablas de claves abría con «91, 71». Con la versión anterior —una
#: sola unidad— ese campo no se reconocía como tal y «91, 71» terminaba siendo
#: `street_1`, o sea que el rescate vehicular de Avenida España se iba a
#: geocodificar buscando la calle «91, 71». El fallo aparece justamente en los
#: despachos más grandes, que son los que más importan.
_UNIDAD = re.compile(r"^[A-Za-z]{0,2}\s*\d{1,3}(?:\s*,\s*[A-Za-z]{0,2}\s*\d{1,3})*$")


def dispatch_summary_heuristic(text: str, *, source_handle: str) -> dict[str, Any] | None:
    """Decodificación por reglas. Respaldo y línea base contra la que medir.

    Existe por la misma razón que `extract_streets_heuristic`: una clave sin
    provisionar no puede apagar en silencio la fuente de mayor confianza del
    sistema. Resuelve el formato de @CGI_CBV —campos separados por `*`, vías por
    `/`, clave al final— que es la forma en que llega la mayoría, y devuelve
    None cuando no reconoce ni clave ni vía.

    Qué camino produjo cada resumen queda en `raw_data._extraction.mode`, igual
    que en la capa de calles, para poder medir uno contra otro sobre datos
    reales en vez de discutirlo.
    """
    limpio = " ".join(str(text or "").split())
    if not limpio:
        return None

    # Mismo motivo que en `format_dispatch_summary`: `resolve_clave` devuelve
    # `tuple[str, str]` y desempaquetarlo primero haría que mypy fijara ambas
    # variables en `str`, cuando la rama de abajo las deja en None a propósito
    # —un aviso sin clave reconocible existe y tiene que poder representarse—.
    clave: str | None
    significado: str | None
    resuelto = resolve_clave(limpio)
    if resuelto is not None:
        clave, significado = resuelto
    else:
        # La clave está en el aviso pero no en `CLAVE_MEANINGS`: la central
        # empezó a despachar algo que nadie registró. Se conserva el número —es
        # el dato— y el significado queda para que `format_dispatch_summary`
        # lo marque. Perder también el número dejaría un resumen que dice "Sin
        # clave" sobre un despacho que sí traía una, y eso oculta justo el caso
        # que hay que ir a corregir al diccionario.
        presentes = find_claves(limpio)
        clave = clave_label(presentes[0]) if presentes else None
        significado = None

    campos = [campo for campo in _CAMPO_SPLIT.split(limpio) if campo]
    street_1: str | None = None
    street_2: str | None = None

    for campo in campos:
        if _UNIDAD.match(campo):
            continue
        # El campo de la clave no es una dirección.
        if find_claves(campo) and not _VIA_SPLIT.search(campo):
            continue
        partes = [parte.strip(" ,.") for parte in _VIA_SPLIT.split(campo) if parte.strip(" ,.")]
        if not partes:
            continue
        street_1 = partes[0]
        street_2 = partes[1] if len(partes) > 1 else None
        break

    # Sin clave Y sin estructura de despacho, esto no es un despacho: es una
    # frase. Sin este corte, "sin nada útil acá" sale como una vía llamada "sin
    # nada útil acá" — la heurística no tiene forma de saber que no lo es, así
    # que se le exige al menos una de las dos marcas del formato. En el camino
    # real no cambia nada (`parse_dispatches` ya filtró por clave); protege a
    # quien llame a esta función desde otra fuente.
    tiene_estructura = len(campos) > 1 or bool(_VIA_SPLIT.search(limpio))
    if not clave and not tiene_estructura:
        return None
    if not clave and not street_1:
        return None

    return {
        "clave": clave,
        "significado": significado,
        "street_1": street_1,
        "street_2": street_2,
        # La comuna nunca se deduce: ver la regla del prompt. El formato
        # telegráfico no la trae, y "Guacolda" existe en cuatro comunas.
        "city": None,
        "resumen": format_dispatch_summary(
            clave=clave,
            significado=significado,
            street_1=street_1,
            street_2=street_2,
            city=None,
            source_handle=source_handle,
        ),
    }


async def decode_dispatch(text: str, *, source_handle: str) -> dict[str, Any] | None:
    """Punto de entrada del camino de despachos: modelo primero, reglas después.

    Espejo de `transporteinforma_worker.extract_streets_via_llm`, y la simetría
    es deliberada: las dos capas eligen igual, fallan igual y se anotan igual.
    """
    payload = " ".join(str(text or "").split())
    if not payload:
        return None

    if is_configured():
        decoded = await extract_dispatch(payload, source_handle=source_handle)
        if decoded is not None:
            return decoded
        logger.debug("Gemini no decodificó el despacho; se intenta con las reglas")

    return dispatch_summary_heuristic(payload, source_handle=source_handle)


__all__ = [
    "CLAVE_GLOSSARY",
    "DISPATCH_RESPONSE_SCHEMA",
    "DISPATCH_SYSTEM_INSTRUCTION",
    "MAX_INPUT_CHARS",
    "MODE_GEMINI",
    "MODE_HEURISTIC",
    "RESPONSE_SCHEMA",
    "SUMMARY_TEMPLATE",
    "SYSTEM_INSTRUCTION",
    "UBICACION_DESCONOCIDA",
    "GeminiUnavailableError",
    "build_location",
    "decode_dispatch",
    "dispatch_summary_heuristic",
    "extract_dispatch",
    "extract_streets",
    "format_dispatch_summary",
    "is_configured",
    "parse_dispatch_response",
    "parse_response",
    "response_text",
]
