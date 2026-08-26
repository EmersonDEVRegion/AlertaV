"""Cliente de la API de Apify. Sólo LEE; nunca dispara una corrida.

Por qué este módulo no arranca Actors
-------------------------------------
Apify cobra **por resultado raspado**, no por petición a su API. El Actor de
Instagram publicado por Apify está tarifado en torno a 1,5 USD por cada 1.000
posts en los planes de pago (2,3–2,7 USD en los planes bajos), y cobra igual por
un post que ya procesamos ayer que por uno nuevo: no hay descuento por
repetición, porque el gasto es el scraping, no la entrega.

De ahí la única cuenta que importa para decidir la arquitectura:

    3 cuentas × 10 posts por corrida × 288 corridas/día (cada 5 min)
      = 8.640 resultados/día ≈ 13 USD/día ≈ 390 USD/mes

    3 cuentas × 5 posts por corrida × 96 corridas/día (cada 15 min)
      = 1.440 resultados/día ≈ 2,2 USD/día ≈ 65 USD/mes

Mismo sistema, seis veces el precio, y la diferencia es sólo cada cuánto se
raspa. Por eso **quién dispara el Actor no es este backend**: lo dispara un
Schedule de Apify, configurado una vez y visible en un solo lugar. Nuestro CRON
de 5 minutos se limita a leer el dataset de la última corrida exitosa, que es
gratis y no consume nada.

El corolario incómodo, y conviene decirlo en voz alta: el *delta fetching* del
worker (`instagram_apify_worker`) **no ahorra un peso de Apify**. Ahorra tokens
de Gemini y llamadas a Nominatim, que es otro presupuesto. Lo único que mueve la
factura de Apify es `resultsLimit` del Actor y la cadencia del Schedule.

Los dos endpoints que se usan
-----------------------------
``GET /v2/acts/{actorId}/runs/last?status=SUCCEEDED``
    Metadatos de la última corrida **exitosa**: `status`, `finishedAt`,
    `defaultDatasetId`, `stats`. Es la que permite distinguir "hoy no hubo
    accidentes" de "el Actor lleva tres días fallando".

``GET /v2/acts/{actorId}/runs/last/dataset/items?status=SUCCEEDED``
    Atajo que resuelve el `defaultDatasetId` de esa corrida y devuelve sus
    items. Responde un **array desnudo**, no un `{"data": ...}` como el resto de
    la API — es la asimetría del cliente y está contemplada en `fetch_items`.

Ese `status=SUCCEEDED` es imprescindible y es también la trampa principal: sin
él, "última corrida" incluye la que está corriendo ahora mismo y devolvería un
dataset a medio llenar. Con él, en cambio, aparece el modo de fallo silencioso
que este proyecto persigue en todas partes — si el Actor lleva días fallando, la
API sigue devolviendo alegremente el dataset de la última corrida buena y
nosotros reprocesaríamos posts viejos reportando `success`. Eso lo cubre
`run_looks_stale`.

Autenticación
-------------
`Authorization: Bearer <token>`, nunca `?token=` en la query. Apify acepta las
dos y recomienda la cabecera: una URL con el token dentro termina en los logs de
acceso, en `collector_runs.params` si alguien la guarda por descuido, y en el
mensaje de error de `CollectorError`, que se serializa a la base.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.collectors.geoservices import parse_timestamp, request_json
from app.core.config import settings
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: Estados terminales de una corrida de Apify. Sólo el primero produce datos
#: confiables; los demás se listan para poder nombrarlos en un diagnóstico.
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED")


@dataclass(frozen=True, slots=True)
class ApifyRun:
    """Metadatos de una corrida del Actor, sin los datos.

    Se pide aparte del dataset —una petición más, gratis— porque es la única
    forma de saber **cuándo** se raspó lo que estamos a punto de leer. El
    dataset por sí solo no lo dice: sus items traen la fecha del post, no la de
    la corrida, y un post de hace dos horas se ve idéntico venga de una corrida
    de hace cinco minutos o de una de anteayer.
    """

    run_id: str | None
    status: str | None
    finished_at: datetime | None
    dataset_id: str | None
    raw: Mapping[str, Any]

    @property
    def age(self) -> timedelta | None:
        if self.finished_at is None:
            return None
        return datetime.now(UTC) - self.finished_at

    def as_dict(self) -> dict[str, Any]:
        """Lo que viaja a `raw_data._apify` de cada señal.

        Deliberadamente NO incluye `raw`: los metadatos de Apify traen la URL de
        la build, el `userId` y el detalle de consumo, y nada de eso pertenece a
        una fila de `raw_events`.
        """
        return {
            "run_id": self.run_id,
            "status": self.status,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "dataset_id": self.dataset_id,
        }


def run_looks_stale(run: ApifyRun, max_age_minutes: int) -> tuple[bool, str | None]:
    """¿La última corrida exitosa es demasiado vieja? Devuelve `(rancia, motivo)`.

    Es el equivalente de `page_looks_broken` en el worker del MTT y de
    `revisar_feed` en el de Bomberos, y existe por el mismo motivo: un endpoint
    que responde 200 con contenido válido puede estar describiendo un mundo de
    hace tres días.

    El fallo concreto que cubre: el Schedule de Apify se rompe —Instagram cambia
    su HTML, el Actor empieza a devolver `FAILED`, la cuenta se queda sin
    crédito— y `runs/last?status=SUCCEEDED` sigue sirviendo el dataset de la
    última corrida buena. Sin esta comprobación el collector leería para siempre
    los mismos posts, los descartaría todos por `external_id` conocido y
    reportaría `success` con 0 eventos. Indistinguible de un día tranquilo.
    """
    if run.finished_at is None:
        return (True, "la última corrida exitosa no informa `finishedAt`")

    age = run.age
    assert age is not None  # finished_at no es None, garantizado arriba
    limit = timedelta(minutes=max_age_minutes)
    if age > limit:
        minutes = int(age.total_seconds() // 60)
        return (
            True,
            f"la última corrida exitosa del Actor terminó hace {minutes} min "
            f"(máximo tolerado: {max_age_minutes} min). ¿Se cayó el Schedule de "
            f"Apify o se agotó el crédito?",
        )
    return (False, None)


def describe_items(items: Sequence[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Separa los items utilizables de los que son un error disfrazado.

    Los Actors de Instagram **no fallan** cuando un perfil es privado, cambió de
    nombre o fue dado de baja: empujan al dataset un item con forma de error
    (`{"error": "no_items", "errorDescription": "..."}`) y la corrida termina en
    `SUCCEEDED`. Si nadie los mira, una cuenta que dejó de existir se ve
    exactamente igual que una cuenta que no publicó nada.

    Devuelve `(items_buenos, motivos)`. Los motivos van a `warn()` y de ahí a
    `collector_runs`, donde una persona puede verlos.
    """
    good: list[dict[str, Any]] = []
    problems: list[str] = []

    for item in items:
        if not isinstance(item, Mapping):
            problems.append(f"item que no es un objeto: {type(item).__name__}")
            continue
        error = item.get("error") or item.get("errorDescription")
        if error:
            perfil = item.get("username") or item.get("inputUrl") or "?"
            problems.append(f"Apify devolvió un error para «{perfil}»: {error}")
            continue
        good.append(dict(item))

    return (good, problems)


def build_client(timeout: float | None = None) -> httpx.AsyncClient:
    """Cliente con la cabecera de autenticación ya puesta.

    El token se lee acá y en ningún otro sitio: ni en la URL, ni en los
    parámetros, ni en `run_params()`. Que exista una sola función que lo toque es
    lo que permite afirmar que no se filtra a la base ni a los logs.
    """
    token = settings.APIFY_TOKEN.strip()
    if not token:
        raise CollectorError(
            "APIFY_TOKEN no está configurada; el collector de Instagram no "
            "tiene con qué autenticarse."
        )
    return httpx.AsyncClient(
        timeout=timeout or settings.APIFY_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            # Apify no lo exige, pero identificarse es barato y aparece en su
            # panel de uso cuando hay que averiguar quién consumió qué.
            "User-Agent": settings.NOMINATIM_USER_AGENT,
        },
    )


def _actor_path(actor_id: str) -> str:
    """URL base del Actor.

    El id lleva **tilde** y no barra cuando se escribe como `usuario~actor`
    (`apify~instagram-scraper`). Con barra, la ruta se rompe y Apify responde un
    404 que parece un actor inexistente en vez de un id mal escrito: se
    normaliza acá para que ese error no llegue nunca a producirse.
    """
    normalised = actor_id.strip().replace("/", "~")
    base = settings.APIFY_BASE_URL.rstrip("/")
    return f"{base}/acts/{normalised}"


def parse_run(payload: Any) -> ApifyRun:
    """Respuesta de `runs/last` → `ApifyRun`. Lanza si no hay ninguna corrida.

    Que `data` venga en `null` no es un fallo de red: significa que el Actor
    jamás corrió con éxito, casi siempre porque falta crear el Schedule. Es un
    problema de configuración y merece dejar la corrida en `failed`, no un
    `partial` que nadie mire.
    """
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise CollectorError(
            "Apify no reporta ninguna corrida exitosa del Actor. ¿Está creado el "
            "Schedule y tiene el token permisos sobre este Actor?",
            detail={"respuesta": str(payload)[:300]},
        )

    return ApifyRun(
        run_id=_text(data.get("id")),
        status=_text(data.get("status")),
        finished_at=parse_timestamp(data.get("finishedAt")),
        dataset_id=_text(data.get("defaultDatasetId")),
        raw=data,
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


async def fetch_last_run(client: httpx.AsyncClient, actor_id: str) -> ApifyRun:
    """Metadatos de la última corrida exitosa del Actor."""
    payload = await request_json(
        client,
        f"{_actor_path(actor_id)}/runs/last",
        {"status": "SUCCEEDED"},
        origin="apify_run",
    )
    return parse_run(payload)


async def fetch_items(
    client: httpx.AsyncClient, actor_id: str, *, limit: int
) -> list[Any]:
    """Items del dataset de la última corrida exitosa. Newest first.

    Tres parámetros y cada uno resuelve algo concreto:

    * `status=SUCCEEDED` — sin esto, "última corrida" incluye la que está
      corriendo ahora y devolvería un dataset a medio llenar.
    * `clean=true` — atajo de `skipHidden=true&skipEmpty=true`. Descarta los
      campos internos del Actor (los que empiezan con `#`) y los items vacíos,
      que en estos scrapers aparecen cuando un post se borra a mitad del raspado.
    * `desc=true` + `limit` — leemos lo nuevo, no lo primero que se raspó. Sin
      `desc`, un `limit` bajo devolvería el fondo del dataset.

    Devuelve un array desnudo: este endpoint es el único de la API que no
    envuelve la respuesta en `{"data": ...}`.
    """
    payload = await request_json(
        client,
        f"{_actor_path(actor_id)}/runs/last/dataset/items",
        {
            "status": "SUCCEEDED",
            "clean": "true",
            "desc": "true",
            "limit": str(max(1, limit)),
        },
        origin="apify_dataset",
    )

    if isinstance(payload, list):
        return payload

    # Un objeto acá significa que Apify contestó otra cosa: casi siempre un
    # `{"error": {"type": "...", "message": "..."}}` con HTTP 200, que es su
    # forma de decir "token sin permisos sobre este Actor". Se convierte en un
    # fallo con nombre en vez de en cero eventos.
    if isinstance(payload, Mapping) and payload.get("error"):
        raise CollectorError(
            f"Apify rechazó la lectura del dataset: {payload['error']}",
            detail={"actor": actor_id},
        )

    raise CollectorError(
        f"el dataset de Apify no devolvió una lista sino "
        f"{type(payload).__name__}; el formato de la API cambió",
        detail={"actor": actor_id, "muestra": str(payload)[:300]},
    )


__all__ = [
    "TERMINAL_STATUSES",
    "ApifyRun",
    "build_client",
    "describe_items",
    "fetch_items",
    "fetch_last_run",
    "parse_run",
    "run_looks_stale",
]
