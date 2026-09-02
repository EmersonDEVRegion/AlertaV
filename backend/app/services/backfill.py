"""Rescate de eventos que quedaron guardados y mudos.

Por qué hace falta
------------------
Los collectors sólo miran hacia adelante. Cuando la extracción de calles falla,
el evento se guarda igual —perder el hecho por no saber dónde sería peor— pero
sin coordenadas, y `cluster_unassigned_events` filtra por `geom IS NOT NULL`:
esa fila existe en `/events` y no existe en el mapa.

El filtro delta (`unseen`) la condena a quedarse así. Descarta el post por
`external_id` en cada corrida siguiente, y hace bien: reprocesar todo en cada
ciclo costaría una llamada al modelo por post y por corrida. El precio de esa
economía es que **arreglar el extractor no rescata lo que ya está guardado**.

El 2026-09-02 eso tuvo nombre: el accidente de Av. España quedó en `raw_events`
con el texto completo, el tipo correcto y `lat: null`. Se arregló el extractor
esa misma noche y el evento siguió sin aparecer, porque el arreglo no alcanzaba
hacia atrás.

Cómo funciona
-------------
Reingresa por la puerta normal. Lee los eventos sin geometría, vuelve a correr
la extracción y la geocodificación con el código de HOY, y reconstruye un
`EventCreate` con el mismo `external_id`. `upsert_many` empareja por
`(source, external_id)` y actualiza `lat`, `lon` y `raw_data`; `geom` es una
columna generada desde lat/lon, así que se recalcula sola y la fila entra al
próximo ciclo de correlación como cualquier otra.

No hay un camino de escritura nuevo. Eso es deliberado: un `UPDATE` a medida
sobre `raw_events` sería una segunda forma de escribir eventos que puede
divergir de la primera, y este repositorio ya pagó ese precio una vez con los
dos `external_id` de Bomberos.

Qué NO hace
-----------
No inventa. Un evento cuya calle sigue sin reconocerse se queda como estaba: la
alternativa —geocodificar al centroide de la comuna— produce un punto plausible
y falso, que es peor que la ausencia porque el mapa no lo distingue de un dato.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.nominatim import build_client as build_geo_client
from app.collectors.social.instagram_apify_worker import geocode_text
from app.models.enums import CORRELATABLE_EVENT_TYPES
from app.repositories.event_repository import EventRepository
from app.schemas.event import EventCreate
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackfillResult:
    examined: int = 0
    geocoded: int = 0
    updated: int = 0
    #: Los que siguieron sin resolver. No es un fallo: es el extractor diciendo
    #: que ese texto no nombra un lugar que se pueda ubicar.
    unresolved: int = 0
    errors: list[str] = field(default_factory=list)


async def backfill_geocoding(
    session: AsyncSession, *, hours: int = 48, limit: int = 25
) -> BackfillResult:
    """Reintenta la geocodificación de los eventos sin coordenadas.

    `limit` es un presupuesto real, no una paginación: Nominatim admite 1 req/s
    y el limitador del proceso lo respeta, así que 25 eventos son 25 segundos de
    corrida. Subirlo mucho convierte esta llamada en algo que expira.

    `hours` acota a la ventana que el mapa muestra. Rescatar un evento de hace
    una semana no sirve para nada: entraría como incidente y el motor lo
    marcaría rancio en la misma pasada.
    """
    resultado = BackfillResult()
    repo = EventRepository(session)
    since = datetime.now(UTC) - timedelta(hours=hours)

    pendientes = await repo.list_ungeocoded(
        since=since, limit=limit, types=sorted(CORRELATABLE_EVENT_TYPES, key=lambda t: t.value)
    )
    resultado.examined = len(pendientes)
    if not pendientes:
        return resultado

    rescatados: list[EventCreate] = []

    async with build_geo_client() as geo_client:
        for event in pendientes:
            try:
                streets, point = await geocode_text(event.text or "", geo_client=geo_client)
            except Exception as exc:
                # Un fallo de Nominatim no puede llevarse el lote entero: los
                # otros veinticuatro eventos no tienen la culpa de esta esquina.
                resultado.errors.append(f"{event.external_id}: {type(exc).__name__}: {exc}")
                continue

            if point is None:
                resultado.unresolved += 1
                continue

            resultado.geocoded += 1
            raw = dict(event.raw_data or {})
            # La traza del rescate queda escrita en la propia fila. Sin esto,
            # un punto que mañana esté mal no diría si lo puso la corrida
            # original o este reintento, que corrieron con código distinto.
            raw["_extraction"] = {**streets, "mode": "backfill"}
            raw["_geocoding"] = point.as_dict()
            raw["_backfill_at"] = datetime.now(UTC).isoformat()

            rescatados.append(
                EventCreate(
                    timestamp=event.timestamp,
                    source=event.source,
                    type=event.type,
                    lat=point.lat,
                    lon=point.lon,
                    text=event.text,
                    external_id=event.external_id,
                    confidence=event.confidence,
                    raw_data=raw,
                )
            )

    if rescatados:
        ingest = await IngestService(session).ingest_batch(rescatados)
        # `duplicated` y no `inserted`: son filas que YA existían y se
        # actualizaron. Un `inserted` acá significaría que el emparejamiento por
        # `external_id` falló y se creó un evento gemelo, que es un defecto.
        resultado.updated = ingest.duplicated
        if ingest.inserted:
            resultado.errors.append(
                f"{ingest.inserted} eventos se INSERTARON en vez de actualizarse: "
                f"el emparejamiento por external_id falló"
            )

    logger.info(
        "backfill de geocodificación",
        extra={
            "examinados": resultado.examined,
            "geocodificados": resultado.geocoded,
            "actualizados": resultado.updated,
            "sin_resolver": resultado.unresolved,
        },
    )
    return resultado
