"""Cliente de la API de Apify. Sólo LEE; nunca dispara una corrida.

Hoy lo usa una sola puerta: el webhook de Bomberos
(`app/services/apify_webhook_service.py`), que lee el dataset que Apify le
nombra al terminar cada corrida del Task de X. El collector de Instagram, que
usaba el resto de este módulo (`runs/last`, detección de corridas rancias), se
borró el 2026-09-23.

Por qué este backend no arranca Actors
--------------------------------------
Apify cobra **por resultado raspado**, no por petición a su API. Quién dispara
el Actor es un Schedule de Apify, configurado una vez y visible en un solo
lugar; lo único que mueve la factura es `maxItems` del Task y la cadencia de
ese Schedule.

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
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import CollectorError
from app.core.identidad import user_agent

logger = logging.getLogger(__name__)


def describe_items(items: Sequence[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Separa los items utilizables de los que son un error disfrazado.

    Los Actors de Apify **no fallan** cuando un perfil es privado, cambió de
    nombre o fue dado de baja: empujan al dataset un item con forma de error
    (`{"error": "no_items", "errorDescription": "..."}`) y la corrida termina en
    `SUCCEEDED`. Si nadie los mira, una cuenta que dejó de existir se ve
    exactamente igual que una cuenta que no publicó nada.

    Devuelve `(items_buenos, motivos)`. Los motivos van a `collector_runs`,
    donde una persona puede verlos.
    """
    good: list[dict[str, Any]] = []
    problems: list[str] = []
    relleno = 0

    for item in items:
        if not isinstance(item, Mapping):
            problems.append(f"item que no es un objeto: {type(item).__name__}")
            continue
        error = item.get("error") or item.get("errorDescription")
        if error:
            perfil = item.get("username") or item.get("inputUrl") or "?"
            problems.append(f"Apify devolvió un error para «{perfil}»: {error}")
            continue
        if es_marcador_vacio(item):
            relleno += 1
            continue
        good.append(dict(item))

    if relleno:
        # Uno por lote y no uno por item: el Actor del 2026-09 mandaba diez
        # iguales en cada corrida, y diez líneas idénticas en `collector_runs`
        # no dicen más que una con el número.
        problems.append(
            f"el Actor devolvió {relleno} items de relleno sin datos "
            "(noResults / demo): no está entregando resultados reales"
        )
    return (good, problems)


#: Claves con las que los Actors marcan un item de relleno en vez de fallar.
#:
#: `noResults` es la de `apidojo/tweet-scraper`: desde septiembre de 2026, en
#: el plan Free y lanzado por el Scheduler, el Actor no raspa nada, escribe
#: diez `{"noResults": true}` y termina en `SUCCEEDED`. Cobra la corrida igual.
_MARCAS_DE_RELLENO = ("noResults", "no_results", "isDemo", "demo")


def es_marcador_vacio(item: Mapping[str, Any]) -> bool:
    """¿Es un item que el Actor puso para no dejar el dataset vacío?

    Se lo reconoce por una marca explícita o por no traer nada de un tuit: ni
    texto, ni id, ni URL. Un item así no es un tuit sin clave —que sería
    silencio legítimo de la central— sino un Actor que no ve.
    """
    if any(item.get(marca) is True for marca in _MARCAS_DE_RELLENO):
        return True
    return not any(item.get(clave) for clave in _CLAVES_DE_TUIT)


#: Lo mínimo que trae cualquier tuit real, en los nombres de los Actors conocidos.
_CLAVES_DE_TUIT = (
    "text", "full_text", "fullText", "content", "rawContent",
    "id", "id_str", "tweetId", "rest_id",
    "url", "twitterUrl", "tweetUrl", "tweet_url", "permalink",
)


def build_client(timeout: float | None = None) -> httpx.AsyncClient:
    """Cliente con la cabecera de autenticación ya puesta.

    El token se lee acá y en ningún otro sitio: ni en la URL, ni en los
    parámetros, ni en `run_params()`. Que exista una sola función que lo toque es
    lo que permite afirmar que no se filtra a la base ni a los logs.
    """
    token = settings.APIFY_TOKEN.strip()
    if not token:
        raise CollectorError(
            "APIFY_TOKEN no está configurada; el webhook no tiene con qué leer "
            "los datasets de Apify."
        )
    return httpx.AsyncClient(
        timeout=timeout or settings.APIFY_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            # Apify no lo exige, pero identificarse es barato y aparece en su
            # panel de uso cuando hay que averiguar quién consumió qué.
            "User-Agent": user_agent(),
        },
    )


__all__ = ["build_client", "describe_items"]
