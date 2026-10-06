"""Tuits de tránsito que llegan por el webhook de Apify (@TTIValparaiso).

Por qué existe
--------------
TransporteInforma Región de Valparaíso, el canal del MTT, publica en dos
lugares: el portal que lee `transporte_informa` y su cuenta de X. El portal
arrastra avisos de semanas (el 06-10 traía faenas de agosto); la cuenta es el
canal fresco. Desde el 2026-10-06 la cuenta viaja en el mismo Task de Apify que
las centrales de Bomberos, así que sus tuits llegan mezclados en la misma
entrega del webhook.

Lo que NO se hace
-----------------
Leerlos como despachos. Un tuit del MTT no tiene clave de Bomberos: con la
tabla de una central entraría con peso 1.00 y un significado inventado. El
webhook los separa por cuenta (`es_cuenta_transito`) antes de buscar tabla.

Lo que sí
---------
La misma tubería del portal, sin copiarla: `clasificar_transito` decide si es
un accidente (`ACCIDENT`, al motor) o un desvío, corte o faena (`ROAD_CLOSURE`,
la capa táctica) y descarta el resto —«Buenos días», recomendaciones,
suspensiones de micros—; después `resolver_avisos` (delta contra lo guardado,
Gemini y Nominatim con sus topes) y `eventos_de_avisos`. Entran como
`EventSource.TRANSPORTE_INFORMA` con la misma confianza 0,80, en una corrida
propia, `transporte_informa_x`, para que su salud se lea aparte de la de
Bomberos.

El `external_id` es `mtt:x:<id del tuit>` (o `mtt:closure:x:<id>`): un espacio
propio, que no choca con los avisos del portal. Si el MTT publica el mismo
choque en los dos canales, el motor los junta en un incidente por cercanía, y
el decaimiento de la regla de `transporte_informa` impide que se corroboren a
sí mismos.

La ceguera (el Actor que deja de traer la cuenta) se mide en el canario de
Bomberos: @TTIValparaiso está en `APIFY_X_CUENTAS_ESPERADAS`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.collectors.traffic.transporteinforma_worker import (
    TrafficNotice,
    eventos_de_avisos,
    external_id_de,
    resolver_avisos,
)
from app.collectors.vocabulary import clasificar_transito
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.enums import CollectorStatus, EventSource, EventType
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)

#: Nombre de la corrida en `collector_runs` (y de `raw_data._collector`).
COLLECTOR_NAME = "transporte_informa_x"

_URL = re.compile(r"https?://\S+")
#: «#ViñadelMar» → «ViñadelMar»: el extractor y Nominatim leen palabras, no
#: etiquetas.
_ALMOHADILLA = re.compile(r"#(?=\w)")


def cuentas_transito() -> frozenset[str]:
    """Las cuentas de `APIFY_X_TRANSITO_HANDLES`, sin arroba y en minúsculas."""
    return frozenset(
        c.strip().lstrip("@").lower() for c in settings.APIFY_X_TRANSITO_HANDLES if c.strip()
    )


def es_cuenta_transito(cuenta: str | None) -> bool:
    """¿El tuit es de una cuenta de tránsito y no de una central de Bomberos?"""
    if not cuenta:
        return False
    return cuenta.strip().lstrip("@").lower() in cuentas_transito()


def limpiar_texto(texto: str) -> str:
    """Saca enlaces y almohadillas, y colapsa espacios."""
    sin_enlaces = _URL.sub(" ", texto or "")
    return " ".join(_ALMOHADILLA.sub("", sin_enlaces).split())


def aviso_de_tuit(
    *,
    texto: str,
    identificador: Any,
    publicado: datetime | None,
    cuenta: str | None,
    url: str | None,
) -> TrafficNotice | None:
    """Un tuit ya desarmado por el webhook → `TrafficNotice`. None si está vacío.

    El id sale del tuit, no del texto: el MTT corrige un aviso editándolo y un
    id derivado del texto convertiría cada corrección en una señal nueva.
    """
    limpio = limpiar_texto(texto)
    if not limpio:
        return None
    if identificador is not None and str(identificador).strip():
        base = str(identificador).strip()
    else:
        base = "h" + hashlib.md5(limpio.encode("utf-8")).hexdigest()[:16]
    return TrafficNotice(
        notice_id=f"x:{base}",
        text=limpio,
        published_at=publicado,
        raw={
            "url": url,
            "cuenta": cuenta,
            "_x": {"id": base, "texto_original": (texto or "")[:2000]},
        },
    )


def es_fresco(aviso: TrafficNotice, *, ahora: datetime, max_age_minutes: int) -> bool:
    """Un aviso sin fecha pasa (como un despacho); uno viejo no describe el presente."""
    if aviso.published_at is None:
        return True
    return ahora - aviso.published_at <= timedelta(minutes=max_age_minutes)


def clasificar(
    avisos: Sequence[TrafficNotice],
) -> tuple[list[tuple[TrafficNotice, EventType]], int]:
    """Accidentes primero (compiten por el mismo cupo de Gemini y Nominatim).

    Devuelve los clasificados y cuántos se descartaron por no ser ni accidente
    ni intervención de la vía.
    """
    clasificados = [
        (aviso, tipo) for aviso in avisos if (tipo := clasificar_transito(aviso.text)) is not None
    ]
    clasificados.sort(key=lambda par: par[1] is not EventType.ACCIDENT)
    return clasificados, len(avisos) - len(clasificados)


async def procesar_avisos_transito(
    avisos: Sequence[TrafficNotice],
    *,
    traza: str,
    ahora: datetime | None = None,
) -> None:
    """Ingiere los tuits de tránsito de una entrega. **No lanza nunca.**

    Corre después de cerrar la corrida de Bomberos, con su propia sesión y su
    propia fila en `collector_runs`: un tropiezo acá no puede tocar los
    despachos, que son la fuente de peso 1.00.
    """
    if not avisos:
        return
    momento = ahora or datetime.now(UTC)
    avisos_degradados: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            service = IngestService(session)
            run = await service.start_run(
                source=EventSource.TRANSPORTE_INFORMA,
                collector=COLLECTOR_NAME,
                params={
                    "traza": traza,
                    "cuentas": sorted(cuentas_transito()),
                    "max_age_minutes": settings.APIFY_X_TRANSITO_MAX_AGE_MINUTES,
                    "max_geocodes": settings.APIFY_X_TRANSITO_MAX_GEOCODES,
                },
            )
            try:
                frescos = [
                    a
                    for a in avisos
                    if es_fresco(
                        a,
                        ahora=momento,
                        max_age_minutes=settings.APIFY_X_TRANSITO_MAX_AGE_MINUTES,
                    )
                ]
                viejos = len(avisos) - len(frescos)
                clasificados, descartados = clasificar(frescos)

                claves = [external_id_de(aviso, tipo) for aviso, tipo in clasificados]
                conocidos = await service.repo.puntos_conocidos(
                    EventSource.TRANSPORTE_INFORMA, claves
                )
                # Lo que sigue es red (Gemini, Nominatim): la conexión no puede
                # quedar «idle in transaction» durante esa espera.
                await session.commit()

                resumen = await resolver_avisos(
                    clasificados,
                    claves=claves,
                    conocidos=conocidos,
                    max_llm_calls=settings.GEMINI_MAX_CALLS_PER_RUN,
                    max_geocodes=settings.APIFY_X_TRANSITO_MAX_GEOCODES,
                    avisar=avisos_degradados.append,
                )
                eventos = eventos_de_avisos(
                    resumen.resueltos,
                    collector=COLLECTOR_NAME,
                    avisar=avisos_degradados.append,
                    ahora=momento,
                )
                resultado = await service.ingest_batch(eventos) if eventos else None
                insertados = resultado.inserted if resultado else 0
                duplicados = resultado.duplicated if resultado else 0

                notas = list(dict.fromkeys(avisos_degradados))
                if viejos:
                    notas.append(
                        f"{viejos} tuits más viejos que "
                        f"{settings.APIFY_X_TRANSITO_MAX_AGE_MINUTES} min; se descartaron"
                    )
                await service.finish_run(
                    run,
                    # Igual que un collector: una degradación (sin cupo, Nominatim
                    # caído) es `partial`; los tuits viejos o irrelevantes no.
                    status=CollectorStatus.PARTIAL
                    if avisos_degradados
                    else CollectorStatus.SUCCESS,
                    fetched=len(avisos),
                    inserted=insertados,
                    duplicate=duplicados,
                    error="; ".join(notas)[:2000] if notas else None,
                )
                accidentes = sum(
                    1 for _, tipo, _, _ in resumen.resueltos if tipo is EventType.ACCIDENT
                )
                logger.info(
                    "tuits de tránsito procesados",
                    extra={
                        "traza": traza,
                        "collector": COLLECTOR_NAME,
                        "tuits": len(avisos),
                        "viejos": viejos,
                        "descartados": descartados,
                        "accidentes": accidentes,
                        "cortes_de_via": len(resumen.resueltos) - accidentes,
                        "reutilizados": resumen.reutilizados,
                        "extracciones_llm": resumen.llamadas_llm,
                        "geocodificados": resumen.geocodificados,
                        "insertados": insertados,
                    },
                )
            except Exception as exc:
                logger.exception(
                    "no se pudieron procesar los tuits de tránsito",
                    extra={"traza": traza},
                )
                try:
                    await service.finish_run(
                        run,
                        status=CollectorStatus.FAILED,
                        fetched=len(avisos),
                        error=f"{type(exc).__name__}: {exc}"[:2000],
                    )
                except Exception:  # pragma: no cover — la base ya no responde
                    logger.exception(
                        "tampoco se pudo registrar el fallo de tránsito",
                        extra={"traza": traza},
                    )
    except Exception:
        # Ni siquiera se pudo abrir la corrida. Queda en el log: no hay fila
        # donde anotarlo.
        logger.exception("tránsito: no se pudo abrir la corrida", extra={"traza": traza})


__all__ = [
    "COLLECTOR_NAME",
    "aviso_de_tuit",
    "clasificar",
    "cuentas_transito",
    "es_cuenta_transito",
    "es_fresco",
    "limpiar_texto",
    "procesar_avisos_transito",
]
