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

    raw = getattr(response, "text", None)
    if not raw:
        # Pasa cuando el filtro de seguridad bloquea la respuesta. Un aviso de
        # accidente con heridos puede activarlo, así que no es un caso teórico.
        logger.warning(
            "Gemini respondió sin texto (¿filtro de seguridad?)",
            extra={"feedback": str(getattr(response, "prompt_feedback", ""))[:200]},
        )
        return None

    return parse_response(raw)


__all__ = [
    "MAX_INPUT_CHARS",
    "MODE_GEMINI",
    "MODE_HEURISTIC",
    "RESPONSE_SCHEMA",
    "SYSTEM_INSTRUCTION",
    "GeminiUnavailableError",
    "extract_streets",
    "is_configured",
    "parse_response",
]
