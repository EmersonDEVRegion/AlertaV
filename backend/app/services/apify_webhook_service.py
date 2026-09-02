"""Ingesta empujada por Apify: del dataset de una corrida a señales de Bomberos.

Por qué existe este servicio y no otro collector
------------------------------------------------
Todo lo demás en este backend **pregunta**: un CRON despierta cada N minutos,
lee una fuente y se duerme. Acá es al revés. Apify raspa la cuenta de la central
según su propio Schedule y **avisa** al terminar; este módulo es lo que corre
cuando llega ese aviso.

La diferencia no es de estilo, y conviene tenerla clara antes de tocar nada:

* **No hay corrida siguiente.** Un collector que falla vuelve a intentar en cinco
  minutos y el hueco se cierra solo. Un webhook perdido —el proceso reiniciando,
  una excepción no atrapada, Apify reintentando contra un 500— **se pierde para
  siempre**. De ahí que la tarea de fondo no deje escapar ninguna excepción y
  que todo, incluido el fracaso, quede escrito en `collector_runs`.
* **La latencia es el punto.** Una 10-4 llega en segundos en vez de en los hasta
  cinco minutos del pull. Para la fuente de confianza 1.00 del sistema —la única
  que lleva un incidente a certeza por sí sola— esos minutos son la diferencia
  entre avisar y contar.
* **El disparo no es nuestro.** Igual que en el collector de Instagram: el
  Schedule vive en el panel de Apify, que es también donde se paga. Si alguien
  lo pausa, acá no falla nada — simplemente deja de llegar, que es el modo de
  fallo silencioso que `collector_runs` existe para hacer visible.

Qué trae el dataset
-------------------
Items de un Actor de X/Twitter sobre la cuenta de la central. El formato varía
entre Actors del marketplace —cambian de precio o dejan de funcionar cuando X
mueve algo, y migrar es cuestión de cambiar el Actor en el Schedule— así que el
texto y la fecha se buscan por **alias de campo**, no por una ruta fija. Ver
`_TEXT_KEYS` y `_DATE_KEYS`.

Sobre el token en la URL
------------------------
La lectura del dataset se autentica con `Authorization: Bearer`, **nunca** con
`?token=` en la query, aunque la API de Apify acepte las dos. Un token en la
query termina en los logs de acceso del proxy, en el mensaje de cualquier
`CollectorError` —que se serializa a `collector_runs.error`, o sea a la base— y
en el historial de quien copie la URL para depurar. `apify_client.build_client`
es la única función del repositorio que toca el token, y esa unicidad es lo que
permite afirmar que no se filtra.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.collectors.geoservices import parse_timestamp, request_json
from app.collectors.social.apify_client import build_client, describe_items
from app.collectors.traffic.bomberos_10_4_worker import (
    Dispatch,
    decode_dispatches,
    dispatches_to_events,
    strip_html,
)
from app.collectors.vocabulary import matches_key
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus, EventSource
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)

#: Nombre con el que esta puerta aparece en `collector_runs.collector`. Distinto
#: del `bomberos_10_4` del feed a propósito: son dos caminos con modos de fallo
#: distintos y mezclarlos en la misma etiqueta haría imposible responder "¿está
#: llegando el webhook?" mirando la tabla.
COLLECTOR_NAME = "bomberos_apify_webhook"

#: Dónde puede venir el texto del tuit según el Actor. El orden importa: el
#: primero que traiga algo gana, y los completos van antes que los truncados
#: (`text` de la API v2 llega recortado a 280 cuando el tuit es más largo).
_TEXT_KEYS = ("full_text", "fullText", "text", "content", "rawContent", "body", "title")

#: Ídem para la fecha de publicación.
_DATE_KEYS = ("createdAt", "created_at", "date", "timestamp", "publishedAt", "time")

#: Ídem para el identificador estable del tuit.
_ID_KEYS = ("id", "id_str", "tweetId", "rest_id", "url", "twitterUrl", "permalink")


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def extract_dataset_id(payload: Any) -> str | None:
    """`resource.defaultDatasetId` del cuerpo del webhook. None si no está.

    Apify manda el objeto de la corrida completo bajo `resource`; el dataset por
    defecto es el campo que interesa. Se aceptan dos alias más porque el panel
    permite plantillas de payload personalizadas y una configuración vieja puede
    estar mandando sólo el id suelto — leerlo igual cuesta dos líneas y evita
    que una integración funcione a medias sin decir por qué.
    """
    if not isinstance(payload, Mapping):
        return None

    resource = payload.get("resource")
    if isinstance(resource, Mapping):
        for key in ("defaultDatasetId", "datasetId"):
            value = resource.get(key)
            if value and str(value).strip():
                return str(value).strip()

    for key in ("defaultDatasetId", "datasetId"):
        value = payload.get(key)
        if value and str(value).strip():
            return str(value).strip()

    return None


def dataset_items_url(dataset_id: str) -> str:
    """URL de los items de un dataset. **Sin token**: va en la cabecera."""
    base = settings.APIFY_BASE_URL.rstrip("/")
    return f"{base}/datasets/{dataset_id.strip()}/items"


async def fetch_dataset_items(dataset_id: str, *, limit: int) -> list[Any]:
    """GET a `/v2/datasets/{id}/items`. Devuelve un array desnudo.

    `clean=true` descarta los campos internos del Actor y los items vacíos;
    `desc=true` + `limit` leen lo nuevo y no el fondo del dataset. Igual que en
    `apify_client.fetch_items`, este endpoint es de los que **no** envuelven la
    respuesta en `{"data": ...}`.
    """
    async with build_client() as client:
        payload = await request_json(
            client,
            dataset_items_url(dataset_id),
            {"clean": "true", "desc": "true", "limit": str(max(1, limit))},
            origin="apify_webhook_dataset",
        )

    if isinstance(payload, list):
        return payload

    # Un objeto acá casi siempre es `{"error": {...}}` servido con HTTP 200: la
    # forma que tiene Apify de decir "este token no puede leer este dataset".
    if isinstance(payload, Mapping) and payload.get("error"):
        raise CollectorError(
            f"Apify rechazó la lectura del dataset: {payload['error']}",
            detail={"dataset_id": dataset_id},
        )

    raise CollectorError(
        f"el dataset de Apify no devolvió una lista sino {type(payload).__name__}; "
        f"el formato de la API cambió",
        detail={"dataset_id": dataset_id, "muestra": str(payload)[:300]},
    )


def parse_tweet(payload: Any, keys: Sequence[str]) -> Dispatch | None:
    """Un item del dataset → `Dispatch`, si trae una clave configurada.

    Devuelve None —sin ruido— para todo lo que no sea un despacho: retuits del
    municipio, agradecimientos, avisos de corte de agua. La cuenta de una central
    publica mucho más que despachos y el filtro por clave es lo único que separa
    una cosa de la otra.

    El `guid` sale del id del tuit y no de un hash del texto, por el mismo
    motivo que en el worker de Instagram: la central corrige un despacho editando
    el mensaje —la calle mal escrita, la comuna equivocada— y un id derivado del
    texto convertiría cada corrección en un segundo incidente en el mapa.
    """
    if not isinstance(payload, Mapping):
        return None

    text = strip_html(str(_first(payload, _TEXT_KEYS) or ""))
    if not text:
        return None

    key = matches_key(text, keys)
    if key is None:
        return None

    identificador = _first(payload, _ID_KEYS)
    guid = f"x:{identificador}" if identificador else None

    return Dispatch(
        key=key,
        # El texto del aviso ES la dirección disponible. No se recorta ni se
        # intenta aislar la calle: cualquier heurística que lo hiciera
        # descartaría contexto que un operador sí sabe leer.
        address=text,
        occurred_at=parse_timestamp(_first(payload, _DATE_KEYS)),
        commune=None,
        raw_text=text[:2000],
        guid=guid,
    )


def is_fresh(dispatch: Dispatch, *, now: datetime, max_age_minutes: int) -> bool:
    """¿El despacho describe el presente?

    Un despacho **sin fecha** pasa: perder una 10-4 por un campo que el Actor no
    supo mapear es peor que ingerir una vieja, y `dispatches_to_events` ya la
    marca usando la hora de ingesta. El corte existe para el otro caso, que es
    el real: una corrida del Actor puede arrastrar el timeline entero de la
    cuenta, y sin este filtro la primera llamada del webhook llenaría el mapa de
    siniestros resueltos hace meses.
    """
    if dispatch.occurred_at is None:
        return True
    return (now - dispatch.occurred_at) <= timedelta(minutes=max_age_minutes)


def _run_fingerprint(dataset_id: str, payload: Mapping[str, Any]) -> str:
    """Huella corta del aviso, para poder seguir una entrega en los logs."""
    resource = payload.get("resource")
    run_id = resource.get("id") if isinstance(resource, Mapping) else None
    semilla = f"{dataset_id}|{run_id or ''}"
    return hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:12]


async def process_dataset(dataset_id: str, payload: Mapping[str, Any]) -> None:
    """Tarea de fondo del webhook. **No lanza nunca.**

    El contrato con FastAPI es absoluto: una excepción escapando de una
    `BackgroundTask` se registra como error no atrapado y no la ve nadie que esté
    mirando la salud del sistema. Todo lo que puede salir mal termina en una fila
    de `collector_runs` con estado `failed` y el motivo dentro, que es donde el
    resto del proyecto ya sabe mirar.

    La sesión se abre acá y no se hereda de la petición: la de la petición ya se
    cerró cuando se devolvió el 200. Ese es el bug clásico de este patrón y el
    síntoma —`InterfaceError: connection is closed`— no aparece hasta que el
    webhook llega bajo carga.
    """
    traza = _run_fingerprint(dataset_id, payload)
    try:
        await _process(dataset_id, traza)
    except Exception:
        # Última barrera. Lo de adentro ya intenta dejar el fallo en
        # `collector_runs`, pero abrir la sesión y registrar la corrida son ellos
        # mismos operaciones que pueden fallar —Postgres caído, el pool agotado—
        # y una excepción escapando de acá no la ve absolutamente nadie: el
        # llamador respondió 200 hace rato y Apify no reintenta un 2xx. El log
        # es lo único que queda.
        logger.exception(
            "el webhook de Apify falló antes de poder registrar la corrida",
            extra={"traza": traza, "dataset_id": dataset_id},
        )


async def _process(dataset_id: str, traza: str) -> None:
    """El cuerpo de `process_dataset`, con la sesión abierta. Puede lanzar."""
    async with AsyncSessionLocal() as session:
        service = IngestService(session)
        keys = [key.strip() for key in settings.BOMBEROS_ACCIDENT_KEYS if key.strip()]

        run = await service.start_run(
            source=EventSource.BOMBEROS,
            collector=COLLECTOR_NAME,
            # El `dataset_id` sí, el token jamás. `params` se serializa a la base.
            params={"dataset_id": dataset_id, "keys": keys, "traza": traza},
        )

        try:
            if not keys:
                raise CollectorError("BOMBEROS_ACCIDENT_KEYS quedó vacía")

            items = await fetch_dataset_items(
                dataset_id, limit=settings.APIFY_WEBHOOK_MAX_ITEMS
            )
            buenos, problemas = describe_items(items)

            now = datetime.now(UTC)
            dispatches: list[Dispatch] = []
            descartados_por_edad = 0

            for item in buenos:
                dispatch = parse_tweet(item, keys)
                if dispatch is None:
                    continue
                if not is_fresh(
                    dispatch, now=now, max_age_minutes=settings.APIFY_WEBHOOK_MAX_AGE_MINUTES
                ):
                    descartados_por_edad += 1
                    continue
                dispatches.append(dispatch)

            decodificados, por_reglas = await decode_dispatches(
                dispatches,
                source_handle=settings.BOMBEROS_SOURCE_HANDLE,
                max_llm_calls=settings.BOMBEROS_MAX_LLM_CALLS,
            )
            events, undated = dispatches_to_events(decodificados, collector=COLLECTOR_NAME)

            resultado = await service.ingest_batch(events) if events else None
            inserted = resultado.inserted if resultado else 0
            duplicated = resultado.duplicated if resultado else 0

            # `partial` sólo cuando Apify reportó un problema con un perfil: eso
            # es una cuenta privada, renombrada o dada de baja, y necesita a una
            # persona. Que el lote no traiga despachos NO es un aviso — la
            # central publica muchas cosas que no son claves, y avisarlo en cada
            # entrega enseñaría a ignorar los avisos.
            estado = CollectorStatus.PARTIAL if problemas else CollectorStatus.SUCCESS

            await service.finish_run(
                run,
                status=estado,
                fetched=len(items),
                inserted=inserted,
                duplicate=duplicated,
                error="; ".join(problemas)[:2000] if problemas else None,
            )

            logger.info(
                "webhook de Apify procesado",
                extra={
                    "traza": traza,
                    "dataset_id": dataset_id,
                    "items": len(items),
                    "despachos": len(dispatches),
                    "insertados": inserted,
                    "duplicados": duplicated,
                    "descartados_por_edad": descartados_por_edad,
                    "sin_fecha": undated,
                    "por_reglas": por_reglas,
                },
            )

        except Exception as exc:
            # Incluido `CollectorError`. Nada sube: ver el docstring.
            motivo = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "el webhook de Apify no pudo procesar el dataset",
                extra={"traza": traza, "dataset_id": dataset_id},
            )
            try:
                await service.finish_run(run, status=CollectorStatus.FAILED, error=motivo[:2000])
            except Exception:  # pragma: no cover — la base ya no responde
                logger.exception(
                    "tampoco se pudo registrar el fallo del webhook",
                    extra={"traza": traza},
                )


__all__ = [
    "COLLECTOR_NAME",
    "dataset_items_url",
    "extract_dataset_id",
    "fetch_dataset_items",
    "is_fresh",
    "parse_tweet",
    "process_dataset",
]
