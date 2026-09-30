"""Moderación del texto de los reportes ciudadanos.

Quién revisa
------------
Tres capas, y el texto sólo se publica si una de las dos últimas lo aprueba:

1. **El filtro** (`ciudadanos.prefiltro`), al recibir el reporte: teléfonos,
   correos, RUT, enlaces y menciones a cuentas se rechazan sin preguntar.
2. **Gemini**, después de cada pasada del motor (`moderar_pendientes`): aprueba
   lo que describe una emergencia y rechaza insultos, datos de personas,
   acusaciones y lo que no tiene que ver. Usa la misma clave y el mismo modelo
   que los despachos; son pocas llamadas de pocos tokens.
3. **Un operador**, con `OPERATOR_TOKEN`, en `/api/v1/moderacion`: puede
   aprobar o rechazar cualquier texto, y es quien decide lo que Gemini no pudo
   (sin clave, cuota agotada, caída).

Falla cerrada: si Gemini no responde, el texto queda **pendiente** —oculto— y
se reintenta en la pasada siguiente. Nunca se publica un texto por defecto. Lo
que se oculta es sólo el comentario: el reporte cuenta igual para el quórum.

Lo que el modelo NO decide
--------------------------
Si el reporte es verdadero, ni si se publica el incidente. Eso lo deciden el
quórum y las otras fuentes, con reglas deterministas. Acá sólo se juzga si un
texto se puede mostrar a desconocidos sin exponer ni ofender a nadie.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event import RawEvent
from app.repositories.event_repository import EventRepository
from app.services.ciudadanos import (
    APROBADO,
    CLAVE_MODERACION,
    PENDIENTE,
    RECHAZADO,
    prefiltro,
)

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
Eres moderador de AlertaV, un mapa ciudadano de emergencias de la Región de \
Valparaíso, Chile. Recibes el comentario que una persona escribió al reportar \
una emergencia (incendio, accidente u otra) y decides si se puede mostrar en \
público, junto al punto del mapa.

Responde SOLO con un objeto JSON: {"apto": true|false, "motivo": "..."}.

APTO (true) si describe lo que la persona ve o sabe de la emergencia: humo, \
llamas, choque, heridos, cortes de calle, dirección del viento, calles, \
sectores, cerros o lugares conocidos. Faltas de ortografía, mayúsculas, \
modismos chilenos o tono de urgencia NO son motivo de rechazo.

NO APTO (false) si contiene cualquiera de estas cosas:
- insultos, groserías dirigidas a alguien, discriminación u odio;
- nombres, apodos, patentes o datos que identifiquen a personas particulares \
(se permiten instituciones: Bomberos, Carabineros, CONAF, la municipalidad);
- acusaciones de culpa o de delito contra alguien identificable;
- teléfonos, correos, redes sociales o enlaces;
- publicidad, bromas, pruebas ("hola", "test") o texto que no trata de una \
emergencia;
- contenido sexual o violento gratuito.

"motivo": 3 a 8 palabras en español, sin repetir el texto. Ante la duda, false.\
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "apto": {"type": "BOOLEAN"},
        "motivo": {"type": "STRING"},
    },
    "required": ["apto", "motivo"],
}

#: El formulario acepta 2000 caracteres; para decidir si un comentario se puede
#: publicar alcanza con mucho menos, y un texto recortado nunca se aprueba a
#: medias porque lo que se publica es el texto entero.
MAX_INPUT_CHARS = 2_000


@dataclass(slots=True)
class PasadaModeracion:
    revisados: int = 0
    aprobados: int = 0
    rechazados: int = 0
    sin_respuesta: int = 0
    omitida: str | None = None
    errores: list[str] = field(default_factory=list)


def decision(
    estado: str, *, por: str, motivo: str | None, ahora: datetime | None = None
) -> dict[str, Any]:
    """El `_moderacion` que se escribe en `raw_data`."""
    if estado not in (APROBADO, RECHAZADO, PENDIENTE):
        raise ValueError(f"estado de moderación desconocido: {estado!r}")
    return {
        "estado": estado,
        "por": por,
        "motivo": (motivo or None) and str(motivo)[:200],
        "en": (ahora or datetime.now(UTC)).isoformat(),
    }


def aplicar(evento: RawEvent, moderacion: dict[str, Any]) -> None:
    """Escribe la decisión. Reasigna el dict: JSONB no detecta mutaciones."""
    evento.raw_data = {**(evento.raw_data or {}), CLAVE_MODERACION: moderacion}


def parse_veredicto(raw: str) -> tuple[bool, str | None] | None:
    """`{"apto": bool, "motivo": str}` → (apto, motivo). None si no sirve."""
    texto = raw.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto.removeprefix("json").strip()
    try:
        datos = json.loads(texto)
    except (ValueError, TypeError):
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("apto"), bool):
        return None
    motivo = datos.get("motivo")
    return (datos["apto"], str(motivo).strip() if motivo else None)


async def veredicto_gemini(texto: str) -> tuple[bool, str | None] | None:
    """Pregunta a Gemini. None si no hay clave, no responde o responde basura.

    Nunca lanza: un fallo deja el texto pendiente, que es oculto.
    """
    from app.collectors.traffic.gemini import (
        GeminiUnavailableError,
        _client,
        registrar_uso,
        response_text,
    )

    payload = " ".join(str(texto or "").split())[:MAX_INPUT_CHARS]
    if not payload:
        return None
    try:
        client = _client()
    except GeminiUnavailableError as exc:
        logger.info("moderación con Gemini no disponible: %s", exc)
        return None

    try:
        from google.genai import types

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.0,
                    max_output_tokens=128,
                ),
            ),
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("Gemini no respondió a tiempo; el texto queda pendiente")
        return None
    except Exception as exc:
        logger.warning(
            "la moderación con Gemini falló; el texto queda pendiente",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return None

    registrar_uso(response, "moderar_reporte")
    return parse_veredicto(response_text(response))


async def moderar_pendientes(
    session: AsyncSession, *, ahora: datetime | None = None
) -> PasadaModeracion:
    """Revisa con Gemini los textos pendientes. Un commit al final.

    Corre después de cada pasada del motor. Sin clave de Gemini o con la
    moderación apagada no hace nada: los textos esperan al operador.
    """
    from app.collectors.traffic.gemini import is_configured

    pasada = PasadaModeracion()
    if not settings.CITIZEN_MODERATION_ENABLED:
        pasada.omitida = "desactivada"
        return pasada
    if not is_configured():
        pasada.omitida = "sin GEMINI_API_KEY"
        return pasada

    ahora = ahora or datetime.now(UTC)
    repo = EventRepository(session)
    pendientes = await repo.pending_moderation(
        since=ahora - timedelta(hours=settings.CITIZEN_MODERATION_MAX_AGE_HOURS),
        limit=settings.CITIZEN_MODERATION_MAX_PER_PASS,
    )
    for evento in pendientes:
        pasada.revisados += 1
        # El filtro va de nuevo por si el reporte entró antes de que existiera.
        motivo_filtro = prefiltro(evento.text)
        if motivo_filtro is not None:
            aplicar(evento, decision(RECHAZADO, por="filtro", motivo=motivo_filtro, ahora=ahora))
            pasada.rechazados += 1
            continue

        veredicto = await veredicto_gemini(evento.text or "")
        if veredicto is None:
            pasada.sin_respuesta += 1
            # Si Gemini falla una vez, fallará con los siguientes también: se
            # corta la pasada en vez de gastar el resto de los intentos.
            break
        apto, motivo = veredicto
        estado = APROBADO if apto else RECHAZADO
        aplicar(evento, decision(estado, por="gemini", motivo=motivo, ahora=ahora))
        if apto:
            pasada.aprobados += 1
        else:
            pasada.rechazados += 1

    if pasada.aprobados or pasada.rechazados:
        await session.commit()
        logger.info(
            "moderación de reportes ciudadanos",
            extra={
                "revisados": pasada.revisados,
                "aprobados": pasada.aprobados,
                "rechazados": pasada.rechazados,
                "sin_respuesta": pasada.sin_respuesta,
            },
        )
    return pasada


__all__ = [
    "PasadaModeracion",
    "aplicar",
    "decision",
    "moderar_pendientes",
    "parse_veredicto",
    "veredicto_gemini",
]
