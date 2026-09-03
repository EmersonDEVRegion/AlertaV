"""Segunda puerta de X: cuentas de prensa e instituciones viales.

Por qué no cabía por la puerta de Bomberos
------------------------------------------
`apify_webhook_service` está cableado a la central de Bomberos: filtra por claves
`10-x` e ingiere como `EventSource.BOMBEROS` con confianza **1.00**, que es la
única banda del sistema que por sí sola marca un incidente como confirmado.

Meter por ahí las cuentas del MTT, de una concesionaria o de un diario tenía dos
desenlaces y ninguno servía. O sus tuits no traen claves y se descartan enteros
—crédito de Apify gastado para insertar cero— o, si alguno coincidiera por
casualidad con un patrón `10-x`, entraría con el peso de un despacho oficial. Un
vecino contando lo que vio pasaría por central de emergencias.

De ahí esta ruta: mismo Actor de X, otro Task, otra URL, otras bandas.

La lista blanca de cuentas es la pieza de seguridad
---------------------------------------------------
`HANDLES` no es configuración: es el contrato. **Sólo entra lo que publica una
cuenta declarada acá**, y con la confianza que se le declara acá. Si mañana
alguien agrega términos de búsqueda al Task —que es exactamente lo que había
antes de esta ruta— los tuits de cualquier persona que mencione «accidente» y
«Valparaíso» llegarían igual a este endpoint, y este filtro es lo único que
impide que entren al mapa.

Por eso la comprobación es por autor y no por contenido, y por eso vive en el
código y no en el `.env`: cada línea necesita una justificación escrita de por
qué esa cuenta merece esa banda, y esa justificación tiene que poder discutirse
en un diff.

Qué reutiliza
-------------
Todo lo que ya existe. El pre-filtro y la clasificación son los de prensa
(`vocabulary.es_emergencia` / `clasificar_noticia`, que ya saben leer titulares),
y la ubicación sale de `geocode_text`, el mismo adaptador que usa Instagram. Acá
no hay léxico nuevo ni geocodificador nuevo: hay una fuente nueva.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.collectors.nominatim import build_client as build_geo_client
from app.collectors.social.apify_client import describe_items
from app.collectors.social.instagram_apify_worker import geocode_text
from app.collectors.vocabulary import clasificar_noticia, es_emergencia
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus, EventSource
from app.schemas.event import EventCreate

# `fetch_dataset_items` vive en el servicio de Bomberos y se reutiliza tal cual:
# lee un dataset de Apify con el token en la cabecera y nada más. Duplicarla acá
# sería una segunda función que toca el token, y la unicidad de ese punto es lo
# que permite afirmar que la credencial no se filtra a los logs ni a la base.
from app.services.apify_webhook_service import fetch_dataset_items
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)

#: Nombre con el que esta puerta aparece en `collector_runs.collector`.
#: Distinto del de Bomberos a propósito: son dos integraciones con modos de
#: fallo distintos, y mezclarlas haría imposible responder «¿está llegando la
#: prensa?» mirando la tabla.
COLLECTOR_NAME = "prensa_x_webhook"


@dataclass(frozen=True, slots=True)
class Cuenta:
    """Una cuenta de X autorizada a entregar por esta ruta."""

    source: EventSource
    confidence: float
    #: Por qué esa banda y no otra. Obliga a que sumar una cuenta sea una
    #: decisión escrita en vez de un handle suelto en una lista.
    motivo: str


#: Las cuentas autorizadas, en minúsculas. **Nada fuera de esta tabla entra.**
HANDLES: dict[str, Cuenta] = {
    "ttivalparaiso": Cuenta(
        source=EventSource.TRANSPORTE_INFORMA,
        confidence=0.80,
        motivo=(
            "Cuenta oficial del MTT: mismo organismo y mismo canal que el "
            "portal que ya raspa `transporte_informa`, así que comparte su banda."
        ),
    ),
    "rutaspacificocl": Cuenta(
        source=EventSource.MEDIA,
        confidence=0.60,
        motivo=(
            "Concesionaria de la Ruta 68. Informa sobre su propia vía y tiene "
            "gente en ella, pero es privada y no encaja en ningún EventSource "
            "institucional sin mentir sobre lo que ese enum significa. Queda en "
            "`media` hasta que exista una banda para concesionarias."
        ),
    ),
    "sitiodelsuceso": Cuenta(
        source=EventSource.MEDIA,
        confidence=0.55,
        motivo=(
            "Prensa local. POR DEBAJO del 0.60 de `prensa_local` a propósito: un "
            "tuit se publica antes de pasar por la edición que sí tiene la nota "
            "del portal. Además su web lleva días devolviendo 403 tras "
            "Cloudflare, así que esta cuenta es hoy la única vía a esa fuente."
        ),
    ),
    "rnevalparaiso": Cuenta(
        source=EventSource.SOCIAL_MEDIA,
        confidence=0.45,
        motivo=(
            "Red ciudadana de emergencias. Reporta rápido y sin verificar, que "
            "es exactamente la definición de la banda `social_media`."
        ),
    ),
}

_TEXT_KEYS = ("full_text", "fullText", "text", "content", "rawContent", "body")
_ID_KEYS = ("id", "id_str", "tweetId", "rest_id", "url", "twitterUrl", "permalink")
_DATE_KEYS = ("createdAt", "created_at", "date", "timestamp", "publishedAt", "time")
#: Dónde viene el autor. Los Actors de X lo anidan de maneras distintas, y sin
#: autor este módulo no puede decidir nada: un tuit sin cuenta identificable se
#: descarta, porque la lista blanca es por autor.
_AUTHOR_PATHS = (
    ("author", "userName"),
    ("author", "screen_name"),
    ("author", "username"),
    ("user", "screen_name"),
    ("user", "username"),
)
_AUTHOR_KEYS = ("username", "userName", "screenName", "screen_name", "handle")


@dataclass(frozen=True, slots=True)
class Tuit:
    tweet_id: str
    handle: str
    text: str
    published_at: datetime | None
    raw: dict[str, Any]


def _first(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def extract_handle(payload: Mapping[str, Any]) -> str | None:
    """Cuenta que publicó el tuit, normalizada a minúsculas y sin arroba."""
    for contenedor, campo in _AUTHOR_PATHS:
        seccion = payload.get(contenedor)
        if isinstance(seccion, Mapping):
            valor = seccion.get(campo)
            if valor and str(valor).strip():
                return str(valor).strip().lstrip("@").lower()

    valor = _first(payload, _AUTHOR_KEYS)
    return str(valor).strip().lstrip("@").lower() if valor else None


def parse_tweet(payload: Any) -> Tuit | None:
    """Item del dataset → `Tuit`. None si no sirve. Función pura.

    Se descarta lo que no traiga id, texto **o autor**. El autor no es opcional
    acá aunque en otros contextos lo sea: sin él no se puede aplicar la lista
    blanca, y sin lista blanca esta ruta acepta lo que sea.
    """
    if not isinstance(payload, Mapping):
        return None

    texto = " ".join(str(_first(payload, _TEXT_KEYS) or "").split())
    if not texto:
        return None

    identificador = _first(payload, _ID_KEYS)
    if not identificador:
        return None

    handle = extract_handle(payload)
    if not handle:
        return None

    from app.collectors.geoservices import parse_timestamp

    return Tuit(
        tweet_id=str(identificador).strip(),
        handle=handle,
        text=texto,
        published_at=parse_timestamp(_first(payload, _DATE_KEYS)),
        raw=dict(payload),
    )


def is_fresh(tuit: Tuit, *, now: datetime, max_age_minutes: int) -> bool:
    """Un tuit sin fecha se considera fresco.

    Misma decisión que en Instagram y por el mismo motivo: procesar de más un
    tuit viejo lo atrapa el `external_id` en la corrida siguiente, mientras que
    descartarlo pierde un accidente por un campo que el Actor no llenó.
    """
    if tuit.published_at is None:
        return True
    return (now - tuit.published_at) <= timedelta(minutes=max_age_minutes)


async def process_dataset(dataset_id: str, payload: Any) -> None:
    """Envoltorio que no deja escapar nada. Ver `apify_webhook_service`."""
    try:
        await _process(dataset_id)
    except Exception:
        logger.exception(
            "el webhook de prensa falló antes de poder registrar la corrida",
            extra={"dataset_id": dataset_id},
        )


async def _process(dataset_id: str) -> None:
    async with AsyncSessionLocal() as session:
        service = IngestService(session)
        run = await service.start_run(
            source=EventSource.MEDIA,
            collector=COLLECTOR_NAME,
            params={"dataset_id": dataset_id, "handles": sorted(HANDLES)},
        )

        try:
            items = await fetch_dataset_items(
                dataset_id, limit=settings.APIFY_WEBHOOK_MAX_ITEMS
            )
            buenos, problemas = describe_items(items)

            now = datetime.now(UTC)
            candidatos: list[tuple[Tuit, Cuenta, Any]] = []
            ajenos = 0
            viejos = 0

            for item in buenos:
                tuit = parse_tweet(item)
                if tuit is None:
                    continue

                cuenta = HANDLES.get(tuit.handle)
                if cuenta is None:
                    # La lista blanca en acción. No es un aviso: el Task puede
                    # traer retuits o menciones de cuentas que no declaramos, y
                    # descartarlas es el trabajo haciéndose bien.
                    ajenos += 1
                    continue

                if not is_fresh(
                    tuit, now=now, max_age_minutes=settings.APIFY_WEBHOOK_MAX_AGE_MINUTES
                ):
                    viejos += 1
                    continue

                if not es_emergencia(tuit.text):
                    continue

                tipo = clasificar_noticia(tuit.text)
                if tipo is None:
                    continue

                candidatos.append((tuit, cuenta, tipo))

            eventos, geocodificados = await _localizar(candidatos)
            resultado = await service.ingest_batch(eventos) if eventos else None

            estado = CollectorStatus.PARTIAL if problemas else CollectorStatus.SUCCESS
            await service.finish_run(
                run,
                status=estado,
                fetched=len(items),
                inserted=resultado.inserted if resultado else 0,
                duplicate=resultado.duplicated if resultado else 0,
                error="; ".join(problemas)[:2000] if problemas else None,
            )

            logger.info(
                "webhook de prensa procesado",
                extra={
                    "dataset_id": dataset_id,
                    "items": len(items),
                    "de_cuentas_ajenas": ajenos,
                    "descartados_por_edad": viejos,
                    "emergencias": len(candidatos),
                    "geocodificados": geocodificados,
                    "insertados": resultado.inserted if resultado else 0,
                },
            )
        except CollectorError as exc:
            await service.finish_run(run, status=CollectorStatus.FAILED, error=exc.message)
            raise
        except Exception as exc:
            await service.finish_run(
                run, status=CollectorStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            raise


async def _localizar(
    candidatos: Sequence[tuple[Tuit, Cuenta, Any]],
) -> tuple[list[EventCreate], int]:
    """Geocodifica y arma los eventos. Un tuit sin punto entra igual.

    Sin coordenadas no llega al mapa —`cluster_unassigned_events` filtra por
    `geom IS NOT NULL`— pero sí queda en `/events` y lo alcanza el rescate de
    `services/backfill.py` cuando el extractor mejore. Descartarlo acá lo
    perdería para siempre.
    """
    if not candidatos:
        return ([], 0)

    eventos: list[EventCreate] = []
    geocodificados = 0

    async with build_geo_client() as geo_client:
        for tuit, cuenta, tipo in candidatos:
            streets: dict[str, Any] = {}
            punto = None
            if geocodificados < settings.BOMBEROS_MAX_GEOCODES:
                geocodificados += 1
                try:
                    streets, punto = await geocode_text(tuit.text, geo_client=geo_client)
                except Exception as exc:
                    logger.warning(
                        "geocodificación fallida para un tuit de prensa",
                        extra={"handle": tuit.handle, "error": f"{type(exc).__name__}: {exc}"},
                    )

            eventos.append(
                EventCreate(
                    timestamp=tuit.published_at or datetime.now(UTC),
                    source=cuenta.source,
                    type=tipo,
                    lat=punto.lat if punto else None,
                    lon=punto.lon if punto else None,
                    text=tuit.text[:10_000],
                    # `x:` como el de Bomberos y por el mismo motivo: el id sale
                    # del tuit y no de un hash del texto, así que corregir el
                    # tuit no crea un segundo incidente. El prefijo de cuenta lo
                    # separa del de la central, que puede citar el mismo hecho.
                    external_id=f"x:{tuit.handle}:{tuit.tweet_id}",
                    confidence=cuenta.confidence,
                    raw_data={
                        "cuenta": tuit.handle,
                        "banda": cuenta.motivo,
                        "_collector": COLLECTOR_NAME,
                        "_extraction": {**streets, "mode": "prensa_x"},
                        "_geocoding": punto.as_dict() if punto else None,
                    },
                )
            )

    return (eventos, geocodificados)
