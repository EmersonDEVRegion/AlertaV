"""Webhook de Apify: la única ruta de este backend que recibe datos empujados.

Cómo se conecta
---------------
En el panel de Apify, sobre el Actor de X/Twitter: *Integrations → Webhooks →
Add webhook*, evento `ACTOR.RUN.SUCCEEDED`, URL
`https://<host>/api/v1/apify/webhook`, y en *Headers* un
`X-AlertaV-Apify-Secret` con el mismo valor que `APIFY_WEBHOOK_SECRET`. El
payload por defecto ya trae `resource.defaultDatasetId`, que es lo único que
esta ruta necesita: **no** hay que personalizar la plantilla.

Las tres decisiones de esta ruta
--------------------------------
**1. Responde antes de trabajar.** Apify espera unos segundos y reintenta con
backoff ante cualquier respuesta que no sea 2xx. Leer el dataset, llamar al
modelo por cada despacho y escribir en la base tarda bastante más que eso, así
que hacerlo dentro de la petición garantizaría timeouts, reintentos y el mismo
lote procesado varias veces. Se extrae el `dataset_id`, se responde 200 y el
trabajo va a una `BackgroundTask`. Lo que se afirma con ese 200 es "recibí el
aviso y sé qué dataset leer", no "ya está ingerido".

**2. Casi todo responde 200.** Un webhook que devuelve 4xx ante un aviso que
nunca va a poder procesar —un evento de prueba, un payload sin dataset— provoca
que Apify lo reintente once veces y después deshabilite la integración. Por eso
lo inutilizable se responde `200 {"status": "ignored"}` con el motivo, y se
grita por el log, que es donde una persona puede verlo. Las dos excepciones son
deliberadas: un secreto incorrecto es 401 —quien llama tiene que saber que fue
rechazado— y un cuerpo que ni siquiera es un objeto JSON es 422, porque eso no
lo manda Apify.

**3. La idempotencia no se resuelve acá.** Apify puede entregar el mismo aviso
dos veces y esta ruta no lleva registro de lo que ya vio. No hace falta: el
`external_id` de cada despacho es determinista y `EventRepository.upsert_many`
actualiza en vez de duplicar. Reprocesar un dataset entero cuesta unas llamadas
al modelo y cero filas de más. Un candado acá sería un segundo mecanismo de
idempotencia que puede desincronizarse del primero.

Sobre la autenticación
----------------------
`APIFY_WEBHOOK_SECRET` vacío deja la ruta **abierta**, y el endpoint lo advierte
en cada llamada. No es una omisión: la ruta tiene que poder ejercitarse en un
despliegue de prueba sin secretos. Pero en producción, abierta significa que
cualquiera que descubra la URL puede hacer que este backend lea un dataset ajeno
y lo ingiera como despachos de Bomberos — la fuente de peso 1.00, la que lleva
un incidente a certeza sin necesitar corroboración. Es la peor inyección posible
en este sistema y por eso el aviso es `warning` y no `info`.

La comparación es en tiempo constante (`secrets.compare_digest`). Un `==` sobre
cadenas corta en el primer byte distinto, y esa diferencia de microsegundos es
suficiente para adivinar un secreto byte a byte con paciencia y una red estable.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, status

from app.core.config import settings
from app.services.apify_webhook_service import extract_dataset_id, process_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apify", tags=["apify"])


def _authorised(secret_header: str | None, authorization: str | None) -> bool:
    """¿Viene con el secreto correcto? True también cuando no hay secreto puesto.

    Se aceptan dos cabeceras porque el panel de Apify permite las dos y no hay
    motivo para obligar a una: `X-AlertaV-Apify-Secret: <valor>` o
    `Authorization: Bearer <valor>`.
    """
    esperado = settings.APIFY_WEBHOOK_SECRET.strip()
    if not esperado:
        return True

    candidatos: list[str] = []
    if secret_header and secret_header.strip():
        candidatos.append(secret_header.strip())
    if authorization and authorization.strip():
        valor = authorization.strip()
        prefijo = "bearer "
        if valor.lower().startswith(prefijo):
            valor = valor[len(prefijo) :].strip()
        candidatos.append(valor)

    return any(secrets.compare_digest(candidato, esperado) for candidato in candidatos)


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Aviso de Apify: una corrida del Actor terminó",
    response_description=(
        "Acuse de recibo. `accepted` = el dataset quedó encolado para lectura; "
        "`ignored` = el aviso llegó bien pero no había nada que leer."
    ),
)
async def apify_webhook(
    # `Body()` explícito y no `payload: Any` a secas: con una anotación que no
    # es un modelo ni un escalar, FastAPI da por hecho que el parámetro es de
    # **query** y responde 422 «Field required» a cada aviso de Apify. Falla en
    # el momento correcto —ninguna entrega llega a procesarse— pero por un
    # motivo imposible de adivinar leyendo el log de Apify, que sólo ve un 422.
    payload: Annotated[Any, Body()],
    background_tasks: BackgroundTasks,
    x_alertav_apify_secret: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Recibe el aviso, encola la lectura del dataset y responde 200.

    `payload` se declara `Any` y no como un modelo de Pydantic a propósito. El
    cuerpo lo define Apify, no nosotros: trae los metadatos completos de la
    corrida y el panel permite plantillas personalizadas. Un modelo estricto
    convertiría cualquier campo nuevo suyo en un 422 —o sea, en despachos
    perdidos— por un cambio que no nos afecta. Lo único que este backend exige
    está en `extract_dataset_id`, y esa exigencia se verifica a mano.
    """
    if not _authorised(x_alertav_apify_secret, authorization):
        logger.warning("webhook de Apify rechazado: secreto inválido")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="secreto de webhook inválido",
        )

    if not settings.APIFY_WEBHOOK_SECRET.strip():
        logger.warning(
            "el webhook de Apify está SIN VERIFICAR: cualquiera que conozca la "
            "URL puede inyectar despachos con confianza 1.00. Configurar "
            "APIFY_WEBHOOK_SECRET antes de exponer este backend."
        )

    if not isinstance(payload, dict):
        # Apify manda un objeto. Otra cosa no viene de Apify.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="el cuerpo del webhook debe ser un objeto JSON",
        )

    dataset_id = extract_dataset_id(payload)
    if dataset_id is None:
        # El caso normal acá es el evento de prueba que dispara el botón "Test"
        # del panel, que no lleva corrida detrás. Se acusa recibo y se sigue: un
        # 4xx haría que Apify reintentara y acabara deshabilitando la integración
        # por un aviso que jamás va a traer dataset.
        logger.warning(
            "webhook de Apify sin `resource.defaultDatasetId`; no hay nada que leer",
            extra={"evento": str(payload.get("eventType") or "?")[:80]},
        )
        return {
            "status": "ignored",
            "reason": "el payload no trae resource.defaultDatasetId",
        }

    background_tasks.add_task(process_dataset, dataset_id, payload)

    logger.info(
        "webhook de Apify aceptado",
        extra={
            "dataset_id": dataset_id,
            "evento": str(payload.get("eventType") or "?")[:80],
        },
    )
    return {"status": "accepted", "dataset_id": dataset_id}
