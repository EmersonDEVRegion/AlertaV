"""Tuits de tránsito que llegan por el webhook de Apify (@TTIValparaiso).

Por qué existe
--------------
TransporteInforma Región de Valparaíso, el canal del MTT, publica al día en su
cuenta de X (su portal casi no se actualiza y dejó de leerse). La cuenta viaja
en el mismo Task de Apify que las centrales de Bomberos, así que sus tuits
llegan mezclados en la misma entrega del webhook.

Lo que NO se hace
-----------------
Leerlos como despachos. Un tuit del MTT no tiene clave de Bomberos: con la
tabla de una central entraría con peso 1.00 y un significado inventado. El
webhook los separa por cuenta (`es_cuenta_transito`) antes de buscar tabla.

Lo que sí
---------
`separar` reparte cada tuit:

* **Choques** (`clasificar_transito`): la tubería de siempre —`resolver_avisos`
  (delta, Gemini y Nominatim con sus topes) y `eventos_de_avisos`—, como
  `ACCIDENT`, al motor.
* **El resto de lo útil** (`vocabulary.subtipo_mtt`): congestión, tránsito
  lento, semáforo apagado, pista ocupada, marcha y normalidad. Gemini extrae
  vía y tramo (`gemini.extraer_tramo`), `tramos.ubicar` lo dibuja sobre la
  forma real de la calle, y entra como `ROAD_CLOSURE` (pista ocupada, marcha)
  o `TRAFFIC_CONDITION` (lo demás), con el subtipo y la línea en `_mtt`. Ninguno
  de los dos entra al motor.
* Lo demás —«Buenos días», el trolebús— se descarta.

Entran como `EventSource.TRANSPORTE_INFORMA` con confianza 0,80, en una
corrida propia, `transporte_informa_x`, para que su salud se lea aparte de la
de Bomberos. El `external_id` es `mtt:x:<id del tuit>` (choque),
`mtt:closure:x:<id>` (pista ocupada, marcha) o `mtt:transito:x:<id>`.

Normalidad y reaperturas
------------------------
«Habilitado tránsito en Avenida Libertad» o «Tránsito normal y fluido en Av
Errázuriz» no son avisos de un problema: son el MTT cerrando los suyos. Se
consultan antes que el accidente (un «Habilitado tránsito tras accidente» no es
un choque nuevo), marcan con `_mtt_fin` los avisos del MTT de las últimas
`REAPERTURA_VENTANA` que nombran la misma vía en la misma ciudad, y entran como
`TRAFFIC_CONDITION` de subtipo `normalidad`: el mapa la pinta en verde un rato.

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

from sqlalchemy import select, update

from app.collectors import tramos
from app.collectors.geoservices import normalise_text
from app.collectors.traffic import gemini
from app.collectors.traffic.transporteinforma_worker import (
    ROAD_CLOSURE_CONFIDENCE,
    TrafficNotice,
    eventos_de_avisos,
    external_id_de,
    resolver_avisos,
)
from app.collectors.vocabulary import clasificar_transito, es_reapertura, subtipo_mtt
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.nombres_via import nombres_calzan
from app.models.enums import CollectorStatus, EventSource, EventType
from app.models.event import RawEvent
from app.schemas.event import EventCreate
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)

#: Cuánto hacia atrás busca una reapertura el aviso que cierra. Un corte por una
#: marcha o un choque se despeja en horas; uno de días lo publica el MOP.
REAPERTURA_VENTANA = timedelta(hours=12)

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


#: Subtipos que ocupan la vía: van a la capa de cortes como `ROAD_CLOSURE`.
SUBTIPOS_CORTE = frozenset({"pista_ocupada", "marcha"})
#: Subtipos que pueden pintar la vía entera («en toda su extensión»).
SUBTIPOS_VIA_COMPLETA = frozenset({"normalidad"})


def separar(
    avisos: Sequence[TrafficNotice],
) -> tuple[list[TrafficNotice], list[tuple[TrafficNotice, str]], int]:
    """`(choques, [(aviso, subtipo)], descartados)`.

    La reapertura va antes que el choque: «Habilitado tránsito tras accidente en
    Ruta 68» cierra uno, no abre otro.
    """
    choques: list[TrafficNotice] = []
    condiciones: list[tuple[TrafficNotice, str]] = []
    descartados = 0
    for aviso in avisos:
        if es_reapertura(aviso.text):
            condiciones.append((aviso, "normalidad"))
        elif clasificar_transito(aviso.text) is EventType.ACCIDENT:
            choques.append(aviso)
        elif subtipo := subtipo_mtt(aviso.text):
            condiciones.append((aviso, subtipo))
        else:
            descartados += 1
    return choques, condiciones, descartados


def tipo_de(subtipo: str) -> EventType:
    return EventType.ROAD_CLOSURE if subtipo in SUBTIPOS_CORTE else EventType.TRAFFIC_CONDITION


def external_id_condicion(aviso: TrafficNotice, subtipo: str) -> str:
    """Los cortes conservan su prefijo de siempre; el estado del tránsito, uno propio."""
    tipo = tipo_de(subtipo)
    if tipo is EventType.ROAD_CLOSURE:
        return external_id_de(aviso, tipo)
    return f"mtt:transito:{aviso.notice_id}"


def evento_condicion(
    aviso: TrafficNotice,
    subtipo: str,
    extraccion: dict[str, Any] | None,
    ubicacion: tramos.Ubicacion | None,
    sin_ubicar: Sequence[str],
    *,
    ahora: datetime,
) -> EventCreate:
    """Un aviso del MTT que no es choque → `EventCreate`. Pura."""
    momento = min(aviso.published_at or ahora, ahora)
    datos = dict(extraccion or {})
    comunas = tramos.comunas_de(datos.get("ciudad"))
    return EventCreate(
        timestamp=momento,
        source=EventSource.TRANSPORTE_INFORMA,
        type=tipo_de(subtipo),
        lat=ubicacion.lat if ubicacion else None,
        lon=ubicacion.lon if ubicacion else None,
        text=aviso.text[:10_000],
        external_id=external_id_condicion(aviso, subtipo),
        confidence=ROAD_CLOSURE_CONFIDENCE,
        raw_data={
            **dict(aviso.raw),
            "comuna": tramos.nombre_de_comuna(comunas),
            "_collector": COLLECTOR_NAME,
            "_extraction": {
                **datos,
                "mode": gemini.MODE_GEMINI if extraccion else gemini.MODE_HEURISTIC,
            },
            "_mtt": {
                "tipo": subtipo,
                "via": datos.get("via"),
                "desde": datos.get("desde"),
                "hasta": datos.get("hasta"),
                "punto": datos.get("punto"),
                "sentido": datos.get("sentido"),
                "linea": ubicacion.geometria if ubicacion else None,
                "ubicacion": ubicacion.como_dict() if ubicacion else None,
                "sin_ubicar": list(dict.fromkeys(sin_ubicar)),
            },
        },
    )


def calles_de(extraccion: dict[str, Any] | None) -> tuple[list[str], str | None]:
    """Calles y ciudad de una extracción: la de un choque (`street_1`, `street_2`,
    `city`) o la de un tramo (`via`, `ciudad`)."""
    if not extraccion:
        return [], None
    calles = [
        str(extraccion.get(campo) or "").strip()
        for campo in ("street_1", "street_2", "via")
        if str(extraccion.get(campo) or "").strip()
    ]
    ciudad = str(extraccion.get("city") or extraccion.get("ciudad") or "").strip() or None
    if ciudad:
        # «ViñadelMar» (etiqueta) y «Viña del Mar» son la misma ciudad.
        ciudad = tramos.nombre_de_comuna(tramos.comunas_de(ciudad)) or ciudad
    return calles, ciudad


def cierra(
    calles: Sequence[str],
    ciudad: str | None,
    otras_calles: Sequence[str],
    otra_ciudad: str | None,
) -> bool:
    """¿La reapertura habla de la misma vía que el aviso? Una calle en común y,
    si las dos dicen la ciudad, la misma: «Avenida España» hay en Valparaíso y en
    Viña del Mar."""
    if ciudad and otra_ciudad and normalise_text(ciudad) != normalise_text(otra_ciudad):
        return False
    return any(nombres_calzan(a, b) or nombres_calzan(b, a) for a in calles for b in otras_calles)


async def cerrar_reabiertos(
    session: Any, reapertura: TrafficNotice, extraccion: dict[str, Any] | None, *, ahora: datetime
) -> int:
    """Marca con `_mtt_fin` los avisos del MTT que esta reapertura cierra."""
    calles, ciudad = calles_de(extraccion)
    if not calles:
        return 0
    hora = reapertura.published_at or ahora
    filas = (
        await session.execute(
            select(RawEvent.id, RawEvent.raw_data)
            .where(RawEvent.source == EventSource.TRANSPORTE_INFORMA)
            .where(
                RawEvent.type.in_(
                    [EventType.ACCIDENT, EventType.ROAD_CLOSURE, EventType.TRAFFIC_CONDITION]
                )
            )
            .where(RawEvent.timestamp >= hora - REAPERTURA_VENTANA)
            .where(RawEvent.timestamp <= hora)
        )
    ).all()
    cerrados = []
    for fila in filas:
        datos = fila.raw_data or {}
        if "_mtt_fin" in datos or (datos.get("_mtt") or {}).get("tipo") == "normalidad":
            continue
        otras, otra_ciudad = calles_de(datos.get("_extraction"))
        if cierra(calles, ciudad, otras, otra_ciudad):
            cerrados.append(fila.id)
    for evento_id in cerrados:
        await session.execute(
            update(RawEvent)
            .where(RawEvent.id == evento_id)
            .values(
                raw_data=RawEvent.raw_data.op("||")(
                    {
                        "_mtt_fin": {
                            "hora": hora.isoformat(),
                            "texto": reapertura.text[:300],
                            "url": (reapertura.raw or {}).get("url"),
                        }
                    }
                )
            )
        )
    return len(cerrados)


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
                choques, condiciones, descartados = separar(frescos)
                clasificados = [(aviso, EventType.ACCIDENT) for aviso in choques]

                claves = [external_id_de(aviso, tipo) for aviso, tipo in clasificados]
                claves_condicion = [external_id_condicion(a, sub) for a, sub in condiciones]
                conocidos = await service.repo.puntos_conocidos(
                    EventSource.TRANSPORTE_INFORMA, claves + claves_condicion
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

                # El resto: extraer el tramo, ubicarlo y guardarlo. Lo ya
                # guardado no vuelve a pagar modelo ni Nominatim.
                nuevas = [
                    (aviso, sub, clave)
                    for (aviso, sub), clave in zip(condiciones, claves_condicion, strict=True)
                    if clave not in conocidos
                ]
                extracciones: list[tuple[TrafficNotice, str, dict[str, Any] | None]] = []
                ubicados = 0
                cupo_llm = settings.GEMINI_MAX_CALLS_PER_RUN
                cupo_nominatim = settings.APIFY_X_TRANSITO_MAX_GEOCODES
                for aviso, subtipo, _ in nuevas:
                    extraccion = None
                    if cupo_llm > 0:
                        cupo_llm -= 1
                        extraccion = await gemini.extraer_tramo(aviso.text)
                    else:
                        avisos_degradados.append("sin cupo de Gemini para tramos del MTT")
                    ubicacion, sin_ubicar = await tramos.ubicar(
                        extraccion,
                        permitir_via_completa=subtipo in SUBTIPOS_VIA_COMPLETA,
                        nominatim=cupo_nominatim > 0,
                    )
                    cupo_nominatim -= 1
                    ubicados += ubicacion is not None
                    eventos.append(
                        evento_condicion(
                            aviso, subtipo, extraccion, ubicacion, sin_ubicar, ahora=momento
                        )
                    )
                    extracciones.append((aviso, subtipo, extraccion))

                resultado = await service.ingest_batch(eventos) if eventos else None
                insertados = resultado.inserted if resultado else 0
                duplicados = resultado.duplicated if resultado else 0

                # Después de ingerir: el corte que esta misma entrega trajo
                # también puede ser el que la normalidad cierra.
                cerrados = 0
                for aviso, subtipo, extraccion in extracciones:
                    if subtipo == "normalidad":
                        cerrados += await cerrar_reabiertos(
                            session, aviso, extraccion, ahora=momento
                        )
                if cerrados:
                    await session.commit()

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
                        "estado_transito": len(nuevas),
                        "ubicados": ubicados,
                        "avisos_cerrados": cerrados,
                        "accidentes": accidentes,
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
    "REAPERTURA_VENTANA",
    "SUBTIPOS_CORTE",
    "aviso_de_tuit",
    "calles_de",
    "cerrar_reabiertos",
    "cierra",
    "cuentas_transito",
    "es_cuenta_transito",
    "es_fresco",
    "evento_condicion",
    "external_id_condicion",
    "limpiar_texto",
    "procesar_avisos_transito",
    "separar",
    "tipo_de",
]
