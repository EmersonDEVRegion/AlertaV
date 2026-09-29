"""Ingesta empujada por Apify: del dataset de una corrida a señales de Bomberos.

Por qué existe este servicio y no otro collector
------------------------------------------------
Todo lo demás en este backend **pregunta**: un CRON despierta cada N minutos,
lee una fuente y se duerme. Acá es al revés. Apify raspa la cuenta de la central
según su propio Schedule y **avisa** al terminar; este módulo es lo que corre
cuando llega ese aviso.

La diferencia no es de estilo, y conviene tenerla clara antes de tocar nada:

* **No hay corrida siguiente.** Un collector que falla vuelve a intentar en cinco
  minutos y el hueco se cierra solo. Un webhook perdido **se pierde para
  siempre**. De ahí el inbox (abajo) y que todo, incluido el fracaso, quede
  escrito en `collector_runs`.
* **La latencia es el punto.** Una 10-4 llega en segundos en vez de en los hasta
  cinco minutos del pull. Para la fuente de confianza 1.00 del sistema —la única
  que lleva un incidente a certeza por sí sola— esos minutos son la diferencia
  entre avisar y contar.
* **El disparo no es nuestro.** El Schedule vive en el panel de Apify, que es
  también donde se paga. Si alguien lo pausa, acá no falla nada — simplemente
  deja de llegar, que es el modo de fallo silencioso que `collector_runs`
  existe para hacer visible.

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

El inbox (desde el 2026-09-23)
------------------------------
Hasta esa fecha el endpoint respondía 200 y procesaba en una `BackgroundTask`
del proceso de la API. Tres defectos, los tres de la auditoría:

* **Un redeploy perdía la entrega.** Render reinicia el contenedor con cada
  push; la tarea en vuelo moría con él y Apify no reintenta un 2xx.
* **Postgres caído también.** El 200 ya se había enviado cuando la tarea
  intentaba abrir la sesión: el fallo quedaba sólo en el log.
* **Gemini y Nominatim desde dos procesos.** El limitador de 1 req/s de
  Nominatim es por proceso; la API y los workers lo duplicaban contra la misma
  IP de salida.

Ahora el endpoint sólo **encola** (`encolar_dataset`): escribe una fila
`running` en `collector_runs` con `params.inbox = "pendiente"` y responde. Si la
base no responde, responde 503 y Apify reintenta — el aviso ya no se pierde. El
proceso de workers la **reclama** (`procesar_siguiente`, con `FOR UPDATE SKIP
LOCKED`) y la procesa con la misma lógica de siempre. Una fila reclamada que no
termina en `INBOX_RECLAMO_VENCE` —el proceso murió a mitad— se vuelve a
reclamar, hasta `INBOX_MAX_INTENTOS`; después se cierra `failed`.

La misma tabla y no una cola nueva: la fila del inbox ES la corrida, así que
`/collectors/health` la ve desde que llega el aviso, y una entrega atascada
(workers apagados) se marca `failing` en vez de verse como «recién llegó».
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text

from app.collectors.geoservices import parse_timestamp, request_json
from app.collectors.social.apify_client import build_client, describe_items
from app.collectors.traffic.bomberos_10_4_worker import (
    Dispatch,
    build_external_id,
    comuna_de_handle,
    decode_dispatches,
    dispatches_to_events,
    geocode_dispatches,
    strip_html,
)
from app.collectors.vocabulary import (
    CBV,
    SISTEMAS_CLAVES,
    SistemaClaves,
    clave_label,
    find_claves,
    matches_key,
    sistema_de_cuenta,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus, EventSource
from app.models.event import CollectorRun
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)

#: Nombre con el que esta puerta aparece en `collector_runs.collector`. Distinto
#: del `bomberos_10_4` del feed a propósito: son dos caminos con modos de fallo
#: distintos y mezclarlos en la misma etiqueta haría imposible responder "¿está
#: llegando el webhook?" mirando la tabla.
COLLECTOR_NAME = "bomberos_apify_webhook"

#: Estados del inbox, en `collector_runs.params["inbox"]`.
INBOX_PENDIENTE = "pendiente"
INBOX_EN_PROCESO = "en_proceso"
INBOX_HECHO = "hecho"
INBOX_ABANDONADO = "abandonado"

#: Una entrega reclamada que no termina en este plazo se da por huérfana (el
#: proceso murió a mitad) y se vuelve a reclamar. Holgado a propósito: una
#: entrega con 25 despachos cuesta 25 llamadas al modelo y hasta 25 s de
#: Nominatim, y reclamar una que sigue viva duplica ese gasto.
INBOX_RECLAMO_VENCE = timedelta(minutes=15)

#: Reclamos antes de cerrar la entrega en `failed`. Un dataset que tumba el
#: proceso tres veces seguidas no lo va a dejar de tumbar a la cuarta.
INBOX_MAX_INTENTOS = 3

#: Ventana en la que el mismo `dataset_id` se considera reentrega de Apify y
#: no se vuelve a encolar. Apify reintenta con backoff durante horas cuando no
#: recibe el 2xx a tiempo; un día cubre eso con margen.
INBOX_VENTANA_DUPLICADO = timedelta(hours=24)

#: Tabla calificada con el esquema. Constante del código, no dato externo: es
#: seguro interpolarla en el SQL crudo del reclamo.
_TABLA_RUNS: str = CollectorRun.__table__.fullname  # type: ignore[attr-defined]

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


#: Dónde puede venir la identidad de quien produjo la corrida. Se miran las
#: cuatro y no una: `resource` trae el objeto de la corrida y `eventData` el
#: resumen del evento —la plantilla por defecto del panel manda los dos—, y
#: dentro de cada uno el Actor y el Task son identificadores DISTINTOS para la
#: misma corrida. Un webhook colgado del Task y otro colgado del Actor entregan
#: cuerpos que no coinciden en un solo campo, así que exigir uno concreto
#: rechazaría media configuración legítima.
_ACTOR_ID_PATHS: tuple[tuple[str, str], ...] = (
    ("resource", "actId"),
    ("resource", "actorTaskId"),
    ("eventData", "actorId"),
    ("eventData", "actorTaskId"),
)


def extract_actor_ids(payload: Any) -> list[str]:
    """Identificadores del Actor y del Task que produjeron esta corrida.

    Lista y no un solo valor: son dos identidades para el mismo hecho y el
    operador puede haber autorizado cualquiera de las dos. Sin duplicados y en
    orden estable, porque este resultado se escribe en el log del rechazo y un
    orden que baila hace irreproducible el mensaje que alguien va a pegar en la
    configuración.

    Vacía cuando el cuerpo no dice de quién viene. Eso NO es lo mismo que "viene
    de un Actor no autorizado", y quien llame tiene que decidir qué hacer con la
    diferencia: una plantilla personalizada que se dejó fuera `resource` es un
    error de configuración nuestro, no una entrega ajena.
    """
    if not isinstance(payload, Mapping):
        return []

    encontrados: list[str] = []
    for contenedor, campo in _ACTOR_ID_PATHS:
        seccion = payload.get(contenedor)
        if not isinstance(seccion, Mapping):
            continue
        valor = seccion.get(campo)
        if not valor:
            continue
        texto = str(valor).strip()
        if texto and texto not in encontrados:
            encontrados.append(texto)

    return encontrados


def dataset_items_url(dataset_id: str) -> str:
    """URL de los items de un dataset. **Sin token**: va en la cabecera."""
    base = settings.APIFY_BASE_URL.rstrip("/")
    return f"{base}/datasets/{dataset_id.strip()}/items"


async def fetch_dataset_items(dataset_id: str, *, limit: int) -> list[Any]:
    """GET a `/v2/datasets/{id}/items`. Devuelve un array desnudo.

    `clean=true` descarta los campos internos del Actor y los items vacíos;
    `desc=true` + `limit` leen lo nuevo y no el fondo del dataset. Este endpoint
    es de los que **no** envuelven la respuesta en `{"data": ...}`.
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


#: Dónde dice el Actor quién publicó el tuit. Varía por Actor igual que el texto.
_AUTHOR_KEYS = (
    "userName", "username", "screen_name", "screenName", "handle",
    "authorUsername", "author_username",
)
_AUTHOR_CONTAINERS = ("author", "user")
#: Dónde viene el enlace público del tuit.
_URL_KEYS = ("url", "twitterUrl", "tweetUrl", "tweet_url", "permalink")
_X_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"})


def tweet_handle(payload: Any) -> str | None:
    """Cuenta que publicó el tuit, con arroba («@CBVM132»). None si no lo dice.

    Hace falta desde que el Task raspa dos centrales en la misma corrida: el
    dataset mezcla despachos de Valparaíso y de Viña, y cada uno se lee con el
    diccionario de su Cuerpo. Se mira primero el autor declarado y después la
    URL del tuit (`x.com/<cuenta>/status/<id>`), que traen todos los Actors
    conocidos aunque no traigan el objeto `author`.
    """
    if not isinstance(payload, Mapping):
        return None

    for contenedor in _AUTHOR_CONTAINERS:
        seccion = payload.get(contenedor)
        if isinstance(seccion, Mapping):
            valor = _first(seccion, _AUTHOR_KEYS)
            if isinstance(valor, str) and valor.strip():
                return f"@{valor.strip().lstrip('@')}"

    valor = _first(payload, _AUTHOR_KEYS)
    if isinstance(valor, str) and valor.strip():
        return f"@{valor.strip().lstrip('@')}"

    for clave in _URL_KEYS:
        url = payload.get(clave)
        if not isinstance(url, str):
            continue
        try:
            partes = urlsplit(url.strip())
        except ValueError:
            continue
        segmentos = [s for s in partes.path.split("/") if s]
        if partes.netloc.lower() in _X_HOSTS and len(segmentos) >= 2 and segmentos[1] == "status":
            return f"@{segmentos[0]}"
    return None


def tweet_url(payload: Any) -> str | None:
    """Enlace público del tuit, si el Actor lo trae como URL de X."""
    if not isinstance(payload, Mapping):
        return None
    for clave in _URL_KEYS:
        url = payload.get(clave)
        if isinstance(url, str) and url.strip().lower().startswith(("https://x.com/", "https://twitter.com/")):
            return url.strip()
    return None


def es_retuit(payload: Any) -> bool:
    """¿Es un retuit? No es un despacho de la central: es de otra cuenta.

    Un retuit trae el texto ajeno bajo la cuenta propia, y con peso 1.00 eso es
    justo lo que no puede pasar: el aviso de otro Cuerpo leído con el
    diccionario de éste.
    """
    if not isinstance(payload, Mapping):
        return False
    if payload.get("isRetweet") is True or payload.get("retweeted_status"):
        return True
    texto = str(_first(payload, _TEXT_KEYS) or "").lstrip()
    return texto.startswith("RT @")


#: Qué ajuste lista las claves que se ingieren de cada Cuerpo. Las claves son
#: configuración —se agregan sin tocar código— y el significado es léxico, que
#: vive en `vocabulary`. Un Cuerpo sin entrada acá no ingiere nada.
_AJUSTE_DE_CLAVES: dict[str, str] = {
    "cbv": "BOMBEROS_ACCIDENT_KEYS",
    "cbvm": "BOMBEROS_CBVM_KEYS",
}


def claves_de_ingesta(sistema: SistemaClaves) -> list[str]:
    """Claves configuradas para ingerir de ese Cuerpo."""
    ajuste = _AJUSTE_DE_CLAVES.get(sistema.slug)
    if ajuste is None:
        return []
    return [key.strip() for key in getattr(settings, ajuste, []) if key.strip()]


_MESES_EN = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
#: «Sun Mar 15 12:00:00 +0000 2026»: el `created_at` clásico de X, que es el que
#: devuelven los Actors de tuits (apidojo, xquik) en su formato por defecto.
_FECHA_X = re.compile(
    r"^[A-Za-z]{3}\s+(?P<mes>[A-Za-z]{3})\s+(?P<dia>\d{1,2})\s+"
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\s+(?P<tz>[+-]\d{4})\s+(?P<anio>\d{4})$"
)


def fecha_de_tuit(valor: Any) -> datetime | None:
    """Fecha de publicación de un tuit, en UTC. None si no se entiende.

    `parse_timestamp` sabe de ISO y de epoch, pero NO del formato clásico de X.
    Hasta el 2026-09-29 ese formato caía a None, y un despacho sin fecha pasa
    `is_fresh` por diseño: con un Actor que devolviera tuits de verdad, la
    primera entrega habría metido al mapa despachos de hace días con la hora
    de hoy. Se parsea a mano y no con `strptime("%a %b …")`, que depende del
    locale del proceso: en un Windows en español «Mar» es martes.
    """
    if isinstance(valor, str):
        encontrado = _FECHA_X.match(valor.strip())
        if encontrado:
            mes = _MESES_EN.get(encontrado["mes"].lower())
            if mes is None:
                return None
            tz = encontrado["tz"]
            signo = 1 if tz[0] == "+" else -1
            desfase = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * signo
            try:
                local = datetime(
                    int(encontrado["anio"]), mes, int(encontrado["dia"]),
                    int(encontrado["h"]), int(encontrado["m"]), int(encontrado["s"]),
                    tzinfo=UTC,
                )
            except ValueError:
                return None
            return local - desfase
    return parse_timestamp(valor)


def parse_tweet(payload: Any, keys: Sequence[str]) -> Dispatch | None:
    """Un item del dataset → `Dispatch`, si trae una clave configurada.

    Devuelve None —sin ruido— para todo lo que no sea un despacho: retuits del
    municipio, agradecimientos, avisos de corte de agua. La cuenta de una central
    publica mucho más que despachos y el filtro por clave es lo único que separa
    una cosa de la otra.

    El `guid` sale del id del tuit y no de un hash del texto: la central
    corrige un despacho editando el mensaje —la calle mal escrita, la comuna
    equivocada— y un id derivado del texto convertiría cada corrección en un
    segundo incidente en el mapa.
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
        occurred_at=fecha_de_tuit(_first(payload, _DATE_KEYS)),
        commune=None,
        raw_text=text[:2000],
        guid=guid,
        cuenta=tweet_handle(payload),
        url=tweet_url(payload),
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


async def encolar_dataset(dataset_id: str, payload: Mapping[str, Any]) -> bool:
    """Deja el aviso en el inbox. False si ese dataset ya estaba encolado.

    Lo llama el endpoint, dentro de la petición: es un INSERT y responde en
    milisegundos, muy por debajo de la paciencia de Apify. **Sí lanza** si la
    base no responde, y a propósito: el endpoint lo convierte en 503 y Apify
    reintenta con backoff, que es exactamente lo que se quiere — antes, con la
    `BackgroundTask`, el 200 ya había salido y el aviso se perdía.

    La reentrega del mismo dataset (Apify no recibió el 2xx a tiempo, o el
    webhook está colgado del Actor y del Task a la vez) no se encola dos veces.
    Un candado consultivo por `dataset_id` serializa dos entregas simultáneas;
    sin él, las dos verían «no existe» y las dos insertarían.
    """
    traza = _run_fingerprint(dataset_id, payload)
    async with AsyncSessionLocal() as session:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(dataset_id))))
        ya_estaba = await session.scalar(
            select(CollectorRun.id)
            .where(
                CollectorRun.source == EventSource.BOMBEROS,
                CollectorRun.collector == COLLECTOR_NAME,
                CollectorRun.params["dataset_id"].astext == dataset_id,
                CollectorRun.started_at > func.now() - INBOX_VENTANA_DUPLICADO,
            )
            .limit(1)
        )
        if ya_estaba is not None:
            await session.commit()
            logger.info(
                "webhook de Apify repetido; el dataset ya estaba en el inbox",
                extra={"traza": traza, "dataset_id": dataset_id, "run_id": ya_estaba},
            )
            return False

        session.add(
            CollectorRun(
                source=EventSource.BOMBEROS,
                collector=COLLECTOR_NAME,
                status=CollectorStatus.RUNNING.value,
                # El `dataset_id` sí, el token jamás. `params` se serializa a la base.
                params={
                    "dataset_id": dataset_id,
                    "traza": traza,
                    "inbox": INBOX_PENDIENTE,
                    "intentos": 0,
                },
            )
        )
        await session.commit()
    return True


async def _abandonar_vencidas(session) -> None:
    """Cierra en `failed` las entregas que agotaron sus reclamos."""
    abandonadas = (
        await session.execute(
            text(
                f"""
                UPDATE {_TABLA_RUNS}
                SET status = 'failed',
                    finished_at = now(),
                    error = 'el proceso murió ' || (params->>'intentos')
                            || ' veces procesando esta entrega; se abandona',
                    params = params || jsonb_build_object('inbox', CAST(:abandonado AS text))
                WHERE source = :source
                  AND collector = :collector
                  AND status = 'running'
                  AND params->>'inbox' = :en_proceso
                  AND (params->>'reclamado_en')::timestamptz < now() - make_interval(secs => :vence)
                  AND coalesce((params->>'intentos')::int, 0) >= :max_intentos
                RETURNING id, params->>'dataset_id' AS dataset_id
                """
            ),
            {
                "source": EventSource.BOMBEROS.value,
                "collector": COLLECTOR_NAME,
                "abandonado": INBOX_ABANDONADO,
                "en_proceso": INBOX_EN_PROCESO,
                "vence": INBOX_RECLAMO_VENCE.total_seconds(),
                "max_intentos": INBOX_MAX_INTENTOS,
            },
        )
    ).all()
    for fila in abandonadas:
        logger.error(
            "entrega del webhook abandonada tras agotar sus reclamos",
            extra={"run_id": fila.id, "dataset_id": fila.dataset_id},
        )


async def reclamar_siguiente() -> tuple[int, str, str, int] | None:
    """Toma la entrega pendiente más vieja: `(run_id, dataset_id, traza, intento)`.

    `FOR UPDATE SKIP LOCKED` hace que dos procesos de workers —el modo `split`,
    o un deploy solapado con el anterior— nunca tomen la misma. El reclamo se
    confirma en su propia transacción antes de procesar: la conexión no queda
    tomada mientras se llama al modelo.

    El filtro por `source` no es redundante: es lo que deja usar el índice
    `ix_collector_runs_source_started`. Sin él, la consulta que corre cada
    `APIFY_INBOX_POLL_SECONDS` recorrería la tabla entera, que crece con cada
    corrida de cada collector.
    """
    async with AsyncSessionLocal() as session:
        await _abandonar_vencidas(session)
        fila = (
            await session.execute(
                text(
                    f"""
                    UPDATE {_TABLA_RUNS} AS r
                    SET params = r.params || jsonb_build_object(
                            'inbox', CAST(:en_proceso AS text),
                            'reclamado_en', now(),
                            'intentos', coalesce((r.params->>'intentos')::int, 0) + 1)
                    WHERE r.id = (
                        SELECT id FROM {_TABLA_RUNS}
                        WHERE source = :source
                          AND collector = :collector
                          AND status = 'running'
                          AND (params->>'inbox' = :pendiente
                               OR (params->>'inbox' = :en_proceso
                                   AND (params->>'reclamado_en')::timestamptz
                                       < now() - make_interval(secs => :vence)))
                        ORDER BY started_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING r.id,
                              r.params->>'dataset_id' AS dataset_id,
                              r.params->>'traza' AS traza,
                              (r.params->>'intentos')::int AS intentos
                    """
                ),
                {
                    "source": EventSource.BOMBEROS.value,
                    "collector": COLLECTOR_NAME,
                    "pendiente": INBOX_PENDIENTE,
                    "en_proceso": INBOX_EN_PROCESO,
                    "vence": INBOX_RECLAMO_VENCE.total_seconds(),
                },
            )
        ).first()
        await session.commit()

    if fila is None:
        return None
    return int(fila.id), str(fila.dataset_id), str(fila.traza or ""), int(fila.intentos)


async def procesar_siguiente() -> bool:
    """Procesa UNA entrega del inbox. False si no había ninguna. No lanza por el dataset.

    Lo que falla procesando el dataset ya queda en la fila (`_process` la
    cierra `failed`). Lo que llega hasta acá es la base cayéndose a mitad: la
    fila queda `en_proceso` y se vuelve a reclamar al vencer el plazo.
    """
    reclamo = await reclamar_siguiente()
    if reclamo is None:
        return False
    run_id, dataset_id, traza, intento = reclamo
    logger.info(
        "entrega del webhook reclamada",
        extra={"run_id": run_id, "dataset_id": dataset_id, "traza": traza, "intento": intento},
    )
    try:
        await _process(dataset_id, traza, run_id=run_id)
    except Exception:
        logger.exception(
            "el inbox del webhook no pudo cerrar la entrega; se reintentará",
            extra={"run_id": run_id, "dataset_id": dataset_id, "traza": traza},
        )
    return True


async def process_dataset(dataset_id: str, payload: Mapping[str, Any]) -> None:
    """Procesa un dataset sin pasar por el inbox. **No lanza nunca.**

    Es el camino directo: abre su propia corrida en `collector_runs`. Lo usan
    los tests y quien quiera reprocesar un dataset a mano desde una consola; la
    ruta del webhook ya no lo llama (ver «El inbox» en el docstring del módulo).
    """
    traza = _run_fingerprint(dataset_id, payload)
    try:
        await _process(dataset_id, traza)
    except Exception:
        # Última barrera. Lo de adentro ya intenta dejar el fallo en
        # `collector_runs`, pero abrir la sesión y registrar la corrida son ellos
        # mismos operaciones que pueden fallar —Postgres caído, el pool agotado—
        # y el log es lo único que queda.
        logger.exception(
            "el webhook de Apify falló antes de poder registrar la corrida",
            extra={"traza": traza, "dataset_id": dataset_id},
        )


async def _sin_repetidos(
    service: IngestService, dispatches: list[Dispatch]
) -> tuple[list[Dispatch], int]:
    """Quita los despachos que ya están en la base tal cual. Devuelve `(nuevos, ya_ingeridos)`.

    Cada entrega relee los últimos `APIFY_WEBHOOK_MAX_ITEMS` tuits de la cuenta,
    así que casi todo lo que trae ya entró en la entrega anterior. Antes se
    decodificaba y geocodificaba todo de nuevo —una llamada al modelo y una a
    Nominatim por despacho repetido— para que el upsert terminara descartándolo.

    Sólo se salta lo que está idéntico Y ubicado: un despacho con el aviso
    corregido se reprocesa (el upsert actualiza), y uno que quedó sin punto
    tiene otra oportunidad de geocodificarse. Ese reintento está acotado por
    `is_fresh`: pasado `APIFY_WEBHOOK_MAX_AGE_MINUTES` ya no llega hasta acá.
    """
    if not dispatches:
        return [], 0
    claves = [build_external_id(d) for d in dispatches]
    conocidos = await service.repo.puntos_conocidos(EventSource.BOMBEROS, claves)
    nuevos: list[Dispatch] = []
    for dispatch, clave in zip(dispatches, claves, strict=True):
        previo = conocidos.get(clave)
        if (
            previo is not None
            and previo.lat is not None
            and (previo.raw_data.get("_bomberos") or {}).get("aviso") == dispatch.raw_text
        ):
            continue
        nuevos.append(dispatch)
    return nuevos, len(dispatches) - len(nuevos)


#: Si el tuit más nuevo de una central tiene más que esto, se anota. Las dos
#: publican varias veces al día; dos días sin nada nuevo huele a un Actor que
#: sirve un caché. Es nota y no cambia el estado: un feriado tranquilo existe.
SILENCIO_SOSPECHOSO = timedelta(hours=48)


def cuentas_esperadas() -> list[str]:
    """Cuentas que cada entrega tiene que traer, sin arroba y en minúsculas."""
    return [
        c.strip().lstrip("@").lower()
        for c in settings.APIFY_X_CUENTAS_ESPERADAS
        if c.strip().lstrip("@")
    ]


def cuentas_sin_tuits(vistos: Mapping[str, int]) -> list[str]:
    """Las cuentas esperadas de las que el Actor no trajo ni un tuit."""
    return [c for c in cuentas_esperadas() if not vistos.get(c)]


def estado_de_entrega(
    *, ciegas: Sequence[str], esperadas: Sequence[str], problemas: bool
) -> CollectorStatus:
    """El estado con que cierra una entrega.

    - **`degraded`** si no se vio NINGUNA de las centrales esperadas. Es lo
      que la salud muestra como ceguera, y lo que faltó durante todo
      septiembre de 2026: con diez items de relleno por corrida, cada entrega
      cerraba `success` y las tres familias del mapa se veían sanas.
    - **`partial`** si falta alguna, o hubo un problema reportado por Apify, o
      una clave sin configurar.
    - **`success`** en otro caso, incluido el lote que no trae despachos: la
      central publica mucho más que claves, y eso es silencio legítimo.
    """
    if esperadas and len(ciegas) == len(esperadas):
        return CollectorStatus.DEGRADED
    if ciegas or problemas:
        return CollectorStatus.PARTIAL
    return CollectorStatus.SUCCESS


def _marcar_inbox(run: Any, estado: str) -> None:
    """Deja el estado final del inbox en `params` antes de cerrar la corrida."""
    run.params = {**(getattr(run, "params", None) or {}), "inbox": estado}


async def _process(dataset_id: str, traza: str, run_id: int | None = None) -> None:
    """El cuerpo del procesamiento, con la sesión abierta. Puede lanzar.

    Con `run_id` retoma la fila que dejó `encolar_dataset`; sin él abre una.
    """
    async with AsyncSessionLocal() as session:
        service = IngestService(session)
        # Un juego de claves por Cuerpo: la misma `Clave 10` se ingiere en Viña
        # (otros servicios) y se descarta en Valparaíso (abastecer agua).
        claves_por_cuerpo = {
            sistema.slug: claves_de_ingesta(sistema) for sistema in SISTEMAS_CLAVES
        }
        keys = claves_por_cuerpo[CBV.slug]
        respaldo = settings.BOMBEROS_SOURCE_HANDLE

        # El `dataset_id` sí, el token jamás. `params` se serializa a la base.
        params = {
            "dataset_id": dataset_id,
            "keys": keys,
            "claves_por_cuerpo": claves_por_cuerpo,
            "traza": traza,
        }
        if run_id is None:
            run = await service.start_run(
                source=EventSource.BOMBEROS, collector=COLLECTOR_NAME, params=params
            )
        else:
            run = await service.resume_run(run_id, params)

        try:
            if not any(claves_por_cuerpo.values()):
                raise CollectorError(
                    "BOMBEROS_ACCIDENT_KEYS y BOMBEROS_CBVM_KEYS quedaron vacías"
                )

            items = await fetch_dataset_items(
                dataset_id, limit=settings.APIFY_WEBHOOK_MAX_ITEMS
            )
            buenos, problemas = describe_items(items)

            now = datetime.now(UTC)
            dispatches: list[Dispatch] = []
            descartados_por_edad = 0

            claves_no_configuradas: Counter[str] = Counter()
            cuentas_sin_tabla: Counter[str] = Counter()
            retuits = 0
            # Tuits de cada central que el Actor trajo, retuits incluidos: la
            # prueba de que la está viendo. Sólo cuenta el autor DECLARADO en el
            # item (objeto, campo o URL), nunca el de respaldo: un item sin autor
            # no demuestra nada.
            vistos: Counter[str] = Counter()
            mas_nuevo: dict[str, datetime] = {}

            for item in buenos:
                declarada = tweet_handle(item)
                if declarada is not None:
                    clave_cuenta = declarada.lstrip("@").lower()
                    vistos[clave_cuenta] += 1
                    fecha = fecha_de_tuit(_first(item, _DATE_KEYS))
                    if fecha is not None and (
                        clave_cuenta not in mas_nuevo or fecha > mas_nuevo[clave_cuenta]
                    ):
                        mas_nuevo[clave_cuenta] = fecha

                if es_retuit(item):
                    retuits += 1
                    continue

                # Cada tuit se lee con el diccionario de la central que lo
                # publicó. Sin autor en el item —Actors viejos, o el formato de
                # los tests— rige la cuenta configurada, como siempre.
                cuenta = declarada or respaldo
                sistema = sistema_de_cuenta(cuenta)
                if sistema is None:
                    # Una cuenta sin tabla NO se lee con la de otro Cuerpo. Es
                    # el error que obligó a separar las tablas: sus claves
                    # entrarían con peso 1.00 y otro significado.
                    cuentas_sin_tabla[cuenta] += 1
                    continue

                dispatch = parse_tweet(item, claves_por_cuerpo.get(sistema.slug, []))
                if dispatch is not None:
                    dispatch = replace(dispatch, cuenta=cuenta)
                if dispatch is None:
                    # Antes de seguir: ¿el tuit traía UNA CLAVE que no está
                    # configurada? Eso no es lo mismo que un tuit cualquiera de
                    # la central, y hasta ahora se veían idénticos —los dos
                    # devolvían None y desaparecían sin dejar rastro—.
                    #
                    # La diferencia importa porque el 2026-09-02 se descubrió
                    # que @CGI_CBV publica «CLAVE 5-1» y `BOMBEROS_ACCIDENT_KEYS`
                    # sólo tenía la familia 10 y el 12. Un accidente en Avenida
                    # España con Avenida Argentina, de la fuente de confianza
                    # 1.00 y con la esquina exacta en el texto, se tiraba a la
                    # basura sin una línea de log.
                    #
                    # NO se ingiere: una clave cuyo significado no está en
                    # `CLAVE_MEANINGS` no se puede clasificar, y adivinarle el
                    # tipo a un despacho de peso 1.00 es peor que perderlo. Lo
                    # que se hace es dejar constancia de que existe, para que
                    # alguien decida qué significa y la agregue.
                    #
                    # Las internas (academia, servicios internos, simulacro)
                    # tienen nombre y se descartan a propósito: no cuentan. Es
                    # lo que prometía el comentario de `CLAVE_MEANINGS` y el
                    # código no hacía, y con @CBVM132 importa: su clave más
                    # frecuente es la 16, carros moviéndose entre cuarteles, y
                    # contarla dejaría cada entrega en `partial`.
                    texto = strip_html(str(_first(item, _TEXT_KEYS) or ""))
                    for código in find_claves(texto):
                        if sistema.es_interna(código):
                            continue
                        etiqueta = clave_label(código)
                        if sistema is not CBV:
                            etiqueta = f"{sistema.slug.upper()} {etiqueta}"
                        claves_no_configuradas[etiqueta] += 1
                    continue
                if not is_fresh(
                    dispatch, now=now, max_age_minutes=settings.APIFY_WEBHOOK_MAX_AGE_MINUTES
                ):
                    descartados_por_edad += 1
                    continue
                dispatches.append(dispatch)

            leidos = len(dispatches)
            dispatches, ya_ingeridos = await _sin_repetidos(service, dispatches)
            # El SELECT del delta abrió una transacción (autobegin). Se cierra
            # antes del modelo y de Nominatim, que pueden tardar un minuto: una
            # conexión «idle in transaction» durante ese minuto es una conexión
            # menos para todos los demás.
            await session.commit()

            decodificados, por_reglas = await decode_dispatches(
                dispatches,
                source_handle=settings.BOMBEROS_SOURCE_HANDLE,
                max_llm_calls=settings.BOMBEROS_MAX_LLM_CALLS,
            )
            # Sin este paso los despachos entran sin `lat`/`lon` y el motor los
            # ignora: `cluster_unassigned_events` filtra por `geom IS NOT NULL`
            # y el Paso B sólo adosa alertas de SENAPRED a incidentes que ya
            # existen. La fuente de confianza 1.00 quedaba consultable en
            # `/events` y ausente del mapa. Ver `geocode_dispatches`.
            ubicados, geocodificados = await geocode_dispatches(
                decodificados,
                max_geocodes=settings.BOMBEROS_MAX_GEOCODES,
                # La central dice en qué comuna ocurrió mejor que cualquier
                # heurística sobre el texto, y hasta ahora se descartaba. Sin
                # esto la consulta sale sin guarda geográfica y una calle de
                # nombre común resuelve en otra comuna. Ver `HANDLE_COMUNA`.
                comuna=comuna_de_handle(settings.BOMBEROS_SOURCE_HANDLE),
            )
            events, undated = dispatches_to_events(ubicados, collector=COLLECTOR_NAME)

            resultado = await service.ingest_batch(events) if events else None
            inserted = resultado.inserted if resultado else 0
            duplicated = resultado.duplicated if resultado else 0

            # `partial` sólo cuando Apify reportó un problema con un perfil: eso
            # es una cuenta privada, renombrada o dada de baja, y necesita a una
            # persona. Que el lote no traiga despachos NO es un aviso — la
            # central publica muchas cosas que no son claves, y avisarlo en cada
            # entrega enseñaría a ignorar los avisos.
            if claves_no_configuradas:
                # WARNING, no INFO. Esto es la central publicando despachos que
                # este backend está tirando: no es ruido de la fuente, es un
                # hueco de configuración nuestro, y cada uno es una emergencia
                # real que no llegó al mapa.
                logger.warning(
                    "la central publicó claves que no están en BOMBEROS_ACCIDENT_KEYS",
                    extra={
                        "claves": dict(claves_no_configuradas.most_common(10)),
                        "configuradas": keys,
                        "configuradas_por_cuerpo": claves_por_cuerpo,
                        "remedio": (
                            "averiguar qué significa cada una, agregarla a "
                            "CLAVE_MEANINGS y CODE_TYPES, y recién entonces a "
                            "BOMBEROS_ACCIDENT_KEYS — sin significado no hay "
                            "tipo, y un despacho de peso 1.00 mal tipificado es "
                            "peor que uno perdido"
                        ),
                    },
                )

            # Lo que se descartó tiene que llegar a `collector_runs`, no sólo al
            # log de Render.
            #
            # `collector_runs` es lo que expone la API y lo que lee
            # `/collectors/health`; el log hay que ir a buscarlo al panel del
            # proveedor. Mientras el motivo del cero viviera sólo ahí, una
            # corrida `success · fetched 10 · inserted 0` era exactamente igual
            # de opaca que antes de todo este trabajo: la fila decía que había
            # leído diez tuits y nada más.
            #
            # Es el caso del 2026-09-03: el `5-1` de Avenida España llegó al
            # webhook con seis horas encima y se descartó por edad —bien
            # descartado, un despacho de hace seis horas no describe el
            # presente— pero para saberlo hubo que hacer la resta a mano entre
            # la hora del tuit y la de la corrida.
            notas = list(problemas)
            if claves_no_configuradas:
                notas.append(
                    "claves que la central usa y no están configuradas: "
                    + ", ".join(
                        f"{clave}×{veces}"
                        for clave, veces in claves_no_configuradas.most_common(5)
                    )
                )
            if descartados_por_edad:
                notas.append(
                    f"{descartados_por_edad} despachos más viejos que "
                    f"{settings.APIFY_WEBHOOK_MAX_AGE_MINUTES} min; se descartaron"
                )
            # Se anota y no se alarma, igual que la edad: una cuenta sin tabla
            # en el dataset es un `twitterHandles` mal puesto en el Task, y un
            # retuit es la central compartiendo algo ajeno.
            if cuentas_sin_tabla:
                notas.append(
                    "tuits de cuentas sin tabla de claves, no se ingieren: "
                    + ", ".join(
                        f"{cuenta}×{veces}"
                        for cuenta, veces in cuentas_sin_tabla.most_common(5)
                    )
                )
            if retuits:
                notas.append(f"{retuits} retuits; no son despachos propios")

            # Una clave sin configurar es una degradación real —se están
            # tirando despachos de la fuente de peso 1.00— y merece `partial`.
            # El descarte por edad NO: es el filtro haciendo su trabajo, y
            # marcarlo pintaría de amarillo cada corrida nocturna. Se anota
            # igual, porque anotar y alarmar son cosas distintas.
            ciegas = cuentas_sin_tuits(vistos)
            esperadas = cuentas_esperadas()
            if ciegas:
                notas.insert(
                    0,
                    "el Actor no trajo ningún tuit de "
                    + ", ".join(f"@{c}" for c in ciegas)
                    + f" ({len(items)} items en el dataset): esa central no se está viendo",
                )
            for cuenta_vista, fecha in sorted(mas_nuevo.items()):
                if cuenta_vista in esperadas and now - fecha > SILENCIO_SOSPECHOSO:
                    horas = int((now - fecha).total_seconds() // 3600)
                    notas.append(
                        f"el tuit más reciente de @{cuenta_vista} que trajo el Actor "
                        f"tiene {horas} h: ¿está devolviendo un timeline viejo?"
                    )

            estado = estado_de_entrega(
                ciegas=ciegas,
                esperadas=esperadas,
                problemas=bool(problemas or claves_no_configuradas),
            )

            if run_id is not None:
                _marcar_inbox(run, INBOX_HECHO)
            await service.finish_run(
                run,
                status=estado,
                fetched=len(items),
                inserted=inserted,
                # Los que el delta saltó SON duplicados: antes los contaba el
                # upsert, ahora se cuentan sin haber gastado nada en ellos.
                duplicate=duplicated + ya_ingeridos,
                error="; ".join(notas)[:2000] if notas else None,
            )

            logger.info(
                "webhook de Apify procesado",
                extra={
                    "traza": traza,
                    "dataset_id": dataset_id,
                    "items": len(items),
                    "despachos": leidos,
                    "ya_ingeridos": ya_ingeridos,
                    "insertados": inserted,
                    "duplicados": duplicated,
                    "descartados_por_edad": descartados_por_edad,
                    "cuentas_sin_tabla": dict(cuentas_sin_tabla),
                    "retuits": retuits,
                    "por_cuenta": dict(Counter(d.cuenta or respaldo for d in dispatches)),
                    "sin_fecha": undated,
                    "por_reglas": por_reglas,
                    # Los que quedan sin punto no entran al Paso A del motor:
                    # es la métrica que dice cuántos despachos se registran
                    # pero no llegan al mapa.
                    "geocodificados": geocodificados,
                    "sin_punto": len(ubicados) - geocodificados,
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
                # Si la base es lo que falló, este cierre también falla y la
                # entrega queda `en_proceso`: el inbox la reclama al vencer.
                if run_id is not None:
                    _marcar_inbox(run, INBOX_HECHO)
                await service.finish_run(run, status=CollectorStatus.FAILED, error=motivo[:2000])
            except Exception:  # pragma: no cover — la base ya no responde
                logger.exception(
                    "tampoco se pudo registrar el fallo del webhook",
                    extra={"traza": traza},
                )


__all__ = [
    "COLLECTOR_NAME",
    "INBOX_ABANDONADO",
    "INBOX_EN_PROCESO",
    "INBOX_HECHO",
    "INBOX_MAX_INTENTOS",
    "INBOX_PENDIENTE",
    "INBOX_RECLAMO_VENCE",
    "SILENCIO_SOSPECHOSO",
    "claves_de_ingesta",
    "cuentas_esperadas",
    "cuentas_sin_tuits",
    "dataset_items_url",
    "encolar_dataset",
    "es_retuit",
    "estado_de_entrega",
    "extract_dataset_id",
    "fecha_de_tuit",
    "fetch_dataset_items",
    "is_fresh",
    "parse_tweet",
    "procesar_siguiente",
    "process_dataset",
    "reclamar_siguiente",
    "tweet_handle",
    "tweet_url",
]
