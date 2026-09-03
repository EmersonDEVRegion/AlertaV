"""Webhook de Apify: la única ruta de este backend que recibe datos empujados.

Cómo se conecta
---------------
En el panel de Apify, sobre el Actor de X/Twitter: *Integrations → Webhooks →
Add webhook*, evento `ACTOR.RUN.SUCCEEDED`, URL
`https://<host>/api/v1/apify/webhook`, y en *Headers* un
`X-AlertaV-Apify-Secret` con el mismo valor que `APIFY_WEBHOOK_SECRET`. El
payload por defecto ya trae `resource.defaultDatasetId`, que es lo único que
esta ruta necesita: **no** hay que personalizar la plantilla.

**Sólo el Actor de X/Twitter.** Esta ruta NO es genérica: ingiere como Bomberos,
con `EventSource.BOMBEROS` y `parse_tweet` buscando claves 10-x. El Actor de
Instagram no va acá — lo lee `InstagramApifyCollector` por su cuenta, cada cinco
minutos, con `runs/last`. Apuntarlo a esta URL no da error: sus posts no traen
claves, la corrida cierra en `success` con 0 insertados y la fila verde que deja
en `collector_runs` hace parecer que el webhook de Bomberos está llegando cuando
no llega nada. `APIFY_BOMBEROS_ACTOR_IDS` existe para que eso se anuncie en vez
de ocurrir en silencio.

**Y en un solo nivel.** Un webhook colgado del Actor dispara también para las
corridas que lanza un Task suyo. Si está en los dos, cada corrida entrega dos
veces: no duplica eventos —`external_id` es determinista— pero duplica llamadas
al modelo y hace competir dos lecturas por el presupuesto de geocodificación,
que es de `BOMBEROS_MAX_GEOCODES` a 1 req/s.

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

El rechazo es siempre 401; el nivel del log, no
------------------------------------------------
Una petición sin ninguna cabecera de autenticación se rechaza exactamente igual
que una con el secreto equivocado —el 401 no distingue— pero se registra como
`INFO` y no como `WARNING`. El motivo está en el cuerpo del endpoint: un secreto
equivocado significa que una integración nuestra dejó de entregar despachos y
alguien tiene que enterarse hoy; una petición sin credencial es un webhook
huérfano en el panel de Apify o el barrido de fondo de cualquier URL pública, y
levantar una alarma por cada una termina apagando la alarma entera.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, status

from app.core.config import settings
from app.services.apify_press_service import process_dataset as process_press_dataset
from app.services.apify_webhook_service import (
    extract_actor_ids,
    extract_dataset_id,
    process_dataset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apify", tags=["apify"])


#: Nombre exacto de la cabecera propia. Se declara acá para que el log del
#: rechazo pueda citarlo: un "secreto inválido" a secas no dice si el problema
#: es el valor o el nombre del campo, y el segundo es el error frecuente.
SECRET_HEADER = "X-AlertaV-Apify-Secret"


def _autorizado_en(ids_recibidos: list[str], permitidos: Sequence[str]) -> bool:
    """Versión genérica de `_actor_autorizado`, para la segunda puerta.

    Las dos rutas comparten la regla —lista vacía deja pasar, un cuerpo sin
    identidad no pasa— y difieren sólo en CUÁL lista consultan. Escribirla dos
    veces garantizaría que dentro de seis meses una de las dos se relaje sin que
    nadie lo note.
    """
    limpios = [i.strip() for i in permitidos if i.strip()]
    if not limpios:
        return True
    return any(recibido in limpios for recibido in ids_recibidos)


def _actor_autorizado(ids_recibidos: list[str]) -> bool:
    """¿La corrida viene de un Actor que puede entregar despachos de Bomberos?

    True cuando `APIFY_BOMBEROS_ACTOR_IDS` está vacía: la lista apagada deja la
    ruta como estaba, igual que el secreto vacío la deja abierta. Un despliegue
    que todavía no sabe el id de su Actor tiene que poder arrancar, y el id sólo
    se conoce después de la primera entrega — que es justamente la que este log
    hace legible.

    Un cuerpo que no dice de quién viene NO pasa con la lista puesta. La
    alternativa —dejar pasar lo que no se puede verificar— convierte el guard en
    decorativo: bastaría mandar `{"defaultDatasetId": "…"}` a secas para
    saltárselo entero.
    """
    return _autorizado_en(ids_recibidos, settings.APIFY_BOMBEROS_ACTOR_IDS)


def _candidatos(secret_header: str | None, authorization: str | None) -> list[str]:
    """Valores que podrían ser el secreto, de las dos cabeceras aceptadas.

    `Authorization` se pela de su prefijo `Bearer` **y también se prueba
    entera**: el panel de Apify permite escribir el valor a secas, y un
    `Authorization: <secreto>` sin esquema es una configuración razonable que no
    hay motivo para rechazar.
    """
    valores: list[str] = []

    if secret_header and secret_header.strip():
        valores.append(secret_header.strip())

    if authorization and authorization.strip():
        valor = authorization.strip()
        prefijo = "bearer "
        if valor.lower().startswith(prefijo):
            valores.append(valor[len(prefijo) :].strip())
        else:
            valores.append(valor)

    return [valor for valor in valores if valor]


def _authorised(secret_header: str | None, authorization: str | None) -> bool:
    """¿Viene con el secreto correcto? True también cuando no hay secreto puesto.

    Se aceptan dos cabeceras porque el panel de Apify permite las dos y no hay
    motivo para obligar a una: `X-AlertaV-Apify-Secret: <valor>` o
    `Authorization: Bearer <valor>`.

    La comparación va envuelta en `try/except TypeError` por un detalle de
    `secrets.compare_digest` que muerde exactamente en este caso:
    **con dos `str`, sólo acepta ASCII**. Un secreto generado con acentos, con
    `±` o con cualquier carácter fuera de ASCII —o un valor pegado con un
    espacio duro invisible, que es lo que ocurre copiando desde un panel web—
    hace que la función lance `TypeError`, no que devuelva `False`. Sin la
    captura eso sube como 500: Apify lo lee como fallo transitorio, reintenta
    once veces y termina deshabilitando la integración, todo sin que el log
    diga nunca «secreto inválido». Se codifica a UTF-8 antes de comparar, que
    es la forma que sí admite cualquier byte y conserva el tiempo constante.
    """
    esperado = settings.APIFY_WEBHOOK_SECRET.strip()
    if not esperado:
        return True

    esperado_bytes = esperado.encode("utf-8")
    return any(
        secrets.compare_digest(candidato.encode("utf-8"), esperado_bytes)
        for candidato in _candidatos(secret_header, authorization)
    )


@router.post(
    "/webhook/prensa",
    status_code=status.HTTP_200_OK,
    summary="Aviso de Apify: corrida del Task de prensa y transporte",
    description=(
        "Segunda puerta de X, para las cuentas que NO son la central de "
        "Bomberos: el MTT, la concesionaria de la Ruta 68, prensa local y la "
        "red ciudadana. Mismo Actor, otro Task, otras bandas de confianza.\n\n"
        "Existe porque `/webhook` está cableado a Bomberos: filtra por claves "
        "`10-x` e ingiere con confianza 1.00, la única banda que por sí sola "
        "confirma un incidente. Un tuit de prensa por ahí o se descarta entero "
        "o entra con el peso de un despacho oficial, y ninguna de las dos "
        "sirve.\n\n"
        "**Sólo entran las cuentas declaradas en `HANDLES`**, con la confianza "
        "que se les declara ahí. Es lo que impide que un término de búsqueda "
        "mal puesto en el Task convierta a cualquier vecino en fuente."
    ),
)
async def apify_webhook_prensa(
    payload: Annotated[Any, Body()],
    background_tasks: BackgroundTasks,
    x_alertav_apify_secret: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Gemela de `apify_webhook`, con otra lista blanca y otro procesador.

    El secreto es el MISMO —es de cuenta, no de ruta— así que por sí solo no
    distingue una puerta de la otra. Lo que las separa es
    `APIFY_PRENSA_ACTOR_IDS`: como los dos Tasks salen del mismo Actor,
    comparten `actId` y hay que autorizar el **id del Task**. `extract_actor_ids`
    lee también `actorTaskId` justamente para esto.
    """
    if not _authorised(x_alertav_apify_secret, authorization):
        recibidos = _candidatos(x_alertav_apify_secret, authorization)
        logger.log(
            logging.INFO if not recibidos else logging.WARNING,
            "webhook de prensa rechazado: %s",
            "sin credencial" if not recibidos else "secreto inválido",
            extra={"cabecera_esperada": SECRET_HEADER},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"secreto de webhook inválido. Configura en Apify la cabecera "
                f"{SECRET_HEADER} con el valor de APIFY_WEBHOOK_SECRET."
            ),
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="el cuerpo del webhook debe ser un objeto JSON",
        )

    ids_actor = extract_actor_ids(payload)
    if not _autorizado_en(ids_actor, settings.APIFY_PRENSA_ACTOR_IDS):
        # Mismo criterio que en la puerta de Bomberos: 200 para que Apify no
        # reintente once veces y deshabilite la integración, WARNING porque
        # llegó CON el secreto correcto y por lo tanto es configuración nuestra.
        logger.warning(
            "webhook de prensa ignorado: la corrida no viene de un Task autorizado",
            extra={
                "ids_recibidos": ids_actor,
                "ids_autorizados": list(settings.APIFY_PRENSA_ACTOR_IDS),
                "remedio": (
                    "los dos Tasks comparten actId; hay que autorizar el "
                    "actorTaskId del Task de prensa en APIFY_PRENSA_ACTOR_IDS"
                ),
            },
        )
        return {
            "status": "ignored",
            "reason": "la corrida no viene de un Task autorizado en APIFY_PRENSA_ACTOR_IDS",
            "actor_ids": ids_actor,
        }

    dataset_id = extract_dataset_id(payload)
    if dataset_id is None:
        logger.warning("webhook de prensa sin `resource.defaultDatasetId`")
        return {"status": "ignored", "reason": "el payload no trae resource.defaultDatasetId"}

    background_tasks.add_task(process_press_dataset, dataset_id, payload)
    logger.info(
        "webhook de prensa aceptado",
        extra={"dataset_id": dataset_id, "ids_actor": ids_actor},
    )
    return {"status": "accepted", "dataset_id": dataset_id}


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
        # El aviso dice CUÁL de las dos cabeceras llegó y con qué longitud. Sin
        # eso, "secreto inválido" cubre tres fallos distintos —no llegó ninguna
        # cabecera, llegó con otro nombre, llegó con el valor equivocado— y
        # depurarlo desde el panel de Apify, que sólo ve un 401, es adivinar.
        #
        # La longitud y no el valor: una diferencia de largo delata al instante
        # el error más común (comillas o espacios pegados al copiar), y no
        # revela el secreto. El valor entero jamás va al log — quedaría escrito
        # en claro en el sistema de registro del proveedor.
        recibidos = _candidatos(x_alertav_apify_secret, authorization)

        # El NIVEL del registro depende de si venía alguna credencial, y la
        # distinción no es cosmética.
        #
        # Un rechazo con cabecera presente es una integración **nuestra** mal
        # configurada: alguien copió mal el secreto o lo rotó a medias, y
        # mientras tanto hay despachos de Bomberos que no están entrando. Eso es
        # un `WARNING` y tiene que encender la alarma.
        #
        # Un rechazo SIN ninguna cabecera no es eso. Son los webhooks heredados
        # que quedaron colgando en el panel de Apify disparando peticiones vacías
        # contra esta URL, más el barrido de fondo que recibe cualquier endpoint
        # público. Se rechazan igual —el 401 de abajo no se toca— pero registrar
        # cada uno como `WARNING` inunda el log de producción y, peor, entrena a
        # quien mira el panel de Render a ignorar los avisos de esta ruta. Un
        # canal que grita siempre deja de comunicar, que es la misma lección del
        # `partial` permanente del USGS.
        #
        # `INFO` los conserva —siguen siendo auditables si alguna vez hay que
        # investigar un intento dirigido— sin gastar el presupuesto de atención.
        sin_credencial = not recibidos
        nivel = logging.INFO if sin_credencial else logging.WARNING
        logger.log(
            nivel,
            "webhook de Apify rechazado: %s",
            "sin credencial" if sin_credencial else "secreto inválido",
            extra={
                "trae_cabecera_propia": x_alertav_apify_secret is not None,
                "trae_authorization": authorization is not None,
                "largos_recibidos": [len(valor) for valor in recibidos],
                "largo_esperado": len(settings.APIFY_WEBHOOK_SECRET.strip()),
                "cabecera_esperada": SECRET_HEADER,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"secreto de webhook inválido. Configura en Apify la cabecera "
                f"{SECRET_HEADER} con el valor de APIFY_WEBHOOK_SECRET, o "
                f"Authorization: Bearer <valor>."
            ),
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

    # El secreto ya dijo QUIÉN llama; esto dice QUÉ trae, y son dos preguntas
    # distintas. El secreto de un webhook de Apify es de cuenta, no de Actor:
    # todos los del panel llevan el mismo, así que un segundo Actor apuntado a
    # esta URL pasa la autenticación entera y entrega sus items por la puerta de
    # Bomberos, que ingiere con confianza 1.00.
    #
    # Y lo hace EN VERDE. Los items de otro Actor no traen claves 10-x, así que
    # `_process` no encuentra despachos, no considera eso un aviso —a propósito:
    # la central publica mucho que no es una clave— y cierra la corrida en
    # `success` con 0 insertados. Queda una fila `bomberos_apify_webhook`
    # impecable en `collector_runs`, que es exactamente donde se mira para
    # responder "¿está llegando el webhook?". Un Actor ajeno disparando cada
    # media hora falsifica esa respuesta: la tabla se ve viva mientras los
    # despachos de X llevan días sin entrar. Es el mismo modo de fallo que el
    # `partial` permanente del USGS, pero al revés — verde en vez de rojo, y por
    # eso peor.
    ids_actor = extract_actor_ids(payload)
    if not _actor_autorizado(ids_actor):
        # 200 y no 403, por la misma regla que el evento de prueba: un 4xx hace
        # que Apify reintente once veces y termine deshabilitando la
        # integración. Acá eso sería especialmente absurdo, porque la
        # integración que se deshabilitaría es la del Actor equivocado — la que
        # ya no queremos— pero el operador vería "webhook deshabilitado" y
        # sospecharía del backend.
        #
        # `WARNING` y no `INFO`: esto llegó CON el secreto correcto, así que no
        # es el barrido de fondo de una URL pública ni un webhook huérfano
        # anónimo. Es una integración nuestra apuntando donde no debe, y hay
        # alguien que puede arreglarla hoy. Misma frontera que separa el
        # "secreto inválido" del "sin credencial" más arriba.
        #
        # Los ids recibidos van al log ENTEROS y ese es el punto del mensaje: el
        # id que manda el webhook es el corto (`nfp1fpt5gUlBwPcor`), no la forma
        # `usuario~actor` que se usa en las rutas de la API, y adivinarlo desde
        # el panel es incómodo. Acá sale listo para pegar en la variable. No es
        # un secreto: identifica un Actor, no autoriza nada por sí solo.
        logger.warning(
            "webhook de Apify ignorado: la corrida no viene de un Actor autorizado",
            extra={
                "ids_recibidos": ids_actor,
                "ids_autorizados": list(settings.APIFY_BOMBEROS_ACTOR_IDS),
                "evento": str(payload.get("eventType") or "?")[:80],
                "remedio": (
                    "si este Actor SÍ debe entregar despachos, agregá su id a "
                    "APIFY_BOMBEROS_ACTOR_IDS; si no, quitá el webhook de ese "
                    "Actor en el panel de Apify"
                ),
            },
        )
        return {
            "status": "ignored",
            "reason": (
                "la corrida no viene de un Actor autorizado en "
                "APIFY_BOMBEROS_ACTOR_IDS"
            ),
            "actor_ids": ids_actor,
        }

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
            # También en el camino feliz, y no sólo en el rechazo. Con la lista
            # apagada —el estado de cualquier despliegue nuevo— el rechazo no
            # ocurre nunca, así que si el id sólo saliera ahí habría que romper
            # una entrega a propósito para poder autorizar al Actor legítimo.
            # Acá la PRIMERA entrega buena ya deja el valor listo para pegar en
            # `APIFY_BOMBEROS_ACTOR_IDS`, que es el orden en que esto se
            # configura de verdad: primero llega algo, después se cierra la
            # puerta detrás.
            "ids_actor": ids_actor,
            "guard_actor_activo": bool(
                [i for i in settings.APIFY_BOMBEROS_ACTOR_IDS if i.strip()]
            ),
        },
    )
    return {"status": "accepted", "dataset_id": dataset_id}
