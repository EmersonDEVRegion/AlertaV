"""Webhook de Apify: acuse de recibo, autenticación y lectura del dataset.

Qué se prueba acá y por qué en ese orden
----------------------------------------
La ruta tiene dos mitades con garantías distintas y se prueban por separado:

1. **La petición.** Tiene que responder rápido y casi siempre 200, porque Apify
   reintenta ante cualquier otra cosa y acaba deshabilitando la integración. Lo
   único que se verifica del cuerpo es que se pudo sacar el `dataset_id`.
2. **La tarea de fondo.** Es la que lee, decodifica e ingiere, y su contrato es
   que **no lanza nunca**: una excepción escapando de una `BackgroundTask` no la
   ve nadie. Todo fallo termina en `collector_runs`.

La tarea se ejercita llamándola directamente y no a través del cliente de
pruebas. `TestClient` sí corre las `BackgroundTasks`, pero entonces un fallo de
la tarea se confundiría con un fallo de la ruta, que es justo la distinción que
estos tests existen para sostener.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.enums import CollectorStatus, EventSource
from app.services import apify_webhook_service as svc

WEBHOOK = "/api/v1/apify/webhook"
DATASET_ID = "abcDEF123"
ITEMS_URL = f"https://api.apify.com/v2/datasets/{DATASET_ID}/items"

AHORA = datetime.now(UTC)


#: Id del Actor de X/Twitter, en la forma corta que manda el webhook de verdad.
#: NO es `usuario~actor`: esa forma sirve para las rutas de la API, y confundir
#: las dos es el error que `APIFY_BOMBEROS_ACTOR_IDS` tiene que hacer evidente.
ACTOR_X = "nfp1fpt5gUlBwPcor"
#: El otro Actor de la misma cuenta. Comparte el secreto —los webhooks de Apify
#: lo tienen por cuenta, no por Actor— y por eso el secreto no lo distingue.
ACTOR_INSTAGRAM = "shu8hvrXbJbY3Eb9W"


def payload_apify(
    dataset_id: str | None = DATASET_ID,
    *,
    act_id: str | None = ACTOR_X,
    task_id: str | None = None,
) -> dict:
    """El cuerpo que manda Apify en `ACTOR.RUN.SUCCEEDED`, recortado."""
    recurso: dict = {
        "id": "runIdDePrueba",
        "status": "SUCCEEDED",
        "startedAt": "2026-09-01T12:00:00.000Z",
        "finishedAt": "2026-09-01T12:01:30.000Z",
    }
    evento: dict = {"actorRunId": "runIdDePrueba"}
    if act_id is not None:
        recurso["actId"] = act_id
        evento["actorId"] = act_id
    if task_id is not None:
        recurso["actorTaskId"] = task_id
        evento["actorTaskId"] = task_id
    if dataset_id is not None:
        recurso["defaultDatasetId"] = dataset_id
    return {
        "userId": "usuarioDePrueba",
        "createdAt": "2026-09-01T12:01:31.000Z",
        "eventType": "ACTOR.RUN.SUCCEEDED",
        "eventData": evento,
        "resource": recurso,
    }


def tuit(texto: str, *, id_: str = "1", minutos: int = 5) -> dict:
    momento = AHORA - timedelta(minutes=minutos)
    return {
        "id": id_,
        "full_text": texto,
        "createdAt": momento.isoformat(),
        "url": f"https://x.com/CGI_CBV/status/{id_}",
    }


@pytest.fixture
def tarea(monkeypatch):
    """Sustituye la tarea de fondo y registra con qué se la encoló.

    `TestClient` **ejecuta** las `BackgroundTasks` antes de devolver la
    respuesta. Sin este doble, cada test de la ruta arrastraría la tarea entera
    —red hacia Apify y una sesión contra Postgres, que no existe en la suite— y
    un fallo de la tarea se leería como un fallo de la ruta. Que es justo la
    distinción que separa las dos mitades de este archivo.
    """
    encoladas: list[tuple] = []

    async def falsa(dataset_id, payload):
        encoladas.append((dataset_id, payload))

    monkeypatch.setattr("app.api.v1.endpoints.apify.process_dataset", falsa)
    return encoladas


@pytest.fixture
def cliente(tarea):
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _config_de_prueba():
    """Repone la configuración al salir.

    `settings` es un singleton de módulo: un test que lo deja modificado le
    cambia la configuración a toda la suite, y el fallo aparece después, en otro
    archivo, con aspecto de bug del código.
    """
    previos = {
        nombre: getattr(settings, nombre)
        for nombre in (
            "APIFY_TOKEN",
            "APIFY_WEBHOOK_SECRET",
            "APIFY_BOMBEROS_ACTOR_IDS",
            "APIFY_WEBHOOK_MAX_ITEMS",
            "APIFY_WEBHOOK_MAX_AGE_MINUTES",
            "BOMBEROS_ACCIDENT_KEYS",
            "BOMBEROS_MAX_LLM_CALLS",
        )
    }
    settings.APIFY_TOKEN = "token-de-prueba"
    settings.APIFY_WEBHOOK_SECRET = ""
    # Guard apagado por defecto, que es el estado de un despliegue que todavía
    # no conoce el id de su Actor. Los tests que lo ejercitan lo encienden.
    settings.APIFY_BOMBEROS_ACTOR_IDS = []
    # Sin llamadas al modelo: la decodificación cae a las reglas, que es el
    # camino de cualquier despliegue sin `GEMINI_API_KEY` y no necesita red.
    settings.BOMBEROS_MAX_LLM_CALLS = 0
    yield
    for nombre, valor in previos.items():
        setattr(settings, nombre, valor)


# --- 1. La petición: acuse de recibo -----------------------------------------


def test_un_aviso_bien_formado_responde_200_y_nombra_el_dataset(cliente):
    """El caso feliz, para que los tests de fallo signifiquen algo."""
    respuesta = cliente.post(WEBHOOK, json=payload_apify())

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "accepted"
    assert cuerpo["dataset_id"] == DATASET_ID


def test_el_dataset_se_encola_en_vez_de_leerse_dentro_de_la_peticion(cliente, tarea):
    """El acuse dice «sé qué leer», no «ya está en la base».

    Leer el dataset, decodificar cada despacho y escribir en la base tarda más
    que la ventana que Apify espera antes de reintentar. Hacerlo dentro de la
    petición garantizaría timeouts, reintentos y el mismo lote procesado varias
    veces.
    """
    respuesta = cliente.post(WEBHOOK, json=payload_apify())

    assert respuesta.status_code == 200
    assert len(tarea) == 1, "la lectura tiene que quedar encolada, no ejecutada"
    encolado_dataset, encolado_payload = tarea[0]
    assert encolado_dataset == DATASET_ID
    assert encolado_payload["eventType"] == "ACTOR.RUN.SUCCEEDED"


def test_un_aviso_rechazado_no_encola_nada(cliente, tarea):
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"

    cliente.post(WEBHOOK, json=payload_apify())
    cliente.post(WEBHOOK, json=payload_apify(dataset_id=None), headers={
        "X-AlertaV-Apify-Secret": "secreto-compartido"
    })

    assert tarea == []


def test_un_evento_de_prueba_sin_dataset_no_provoca_un_4xx(cliente):
    """El botón «Test» del panel manda un aviso sin corrida detrás.

    Con un 4xx, Apify lo reintenta once veces y después deshabilita la
    integración entera — o sea, un despacho perdido por cada corrida futura, por
    culpa de un clic de prueba. Se acusa recibo y se grita por el log.
    """
    respuesta = cliente.post(WEBHOOK, json=payload_apify(dataset_id=None))

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ignored"


def test_un_cuerpo_que_no_es_un_objeto_si_es_un_422(cliente):
    """Eso no lo manda Apify, y responderle 200 escondería el error."""
    assert cliente.post(WEBHOOK, json=["no", "soy", "un", "objeto"]).status_code == 422


def test_el_dataset_id_tambien_se_lee_de_una_plantilla_personalizada(cliente):
    """El panel permite payloads a medida; una vieja manda el id suelto."""
    respuesta = cliente.post(WEBHOOK, json={"defaultDatasetId": "sueltoXYZ"})

    assert respuesta.status_code == 200
    assert respuesta.json()["dataset_id"] == "sueltoXYZ"


# --- 2. El secreto compartido ------------------------------------------------


def test_sin_secreto_configurado_la_ruta_queda_abierta(cliente):
    """Documentado y deliberado: un despliegue de prueba tiene que arrancar.

    El endpoint avisa por log en cada llamada. Si este test empieza a fallar
    porque alguien cerró la ruta por defecto, es una mejora — hay que actualizar
    el test, no revertir el cambio.
    """
    settings.APIFY_WEBHOOK_SECRET = ""
    assert cliente.post(WEBHOOK, json=payload_apify()).status_code == 200


def test_con_secreto_configurado_una_llamada_sin_el_es_401(cliente):
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    assert cliente.post(WEBHOOK, json=payload_apify()).status_code == 401


def test_el_secreto_viaja_por_cualquiera_de_las_dos_cabeceras(cliente):
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"

    por_cabecera_propia = cliente.post(
        WEBHOOK, json=payload_apify(), headers={"X-AlertaV-Apify-Secret": "secreto-compartido"}
    )
    por_bearer = cliente.post(
        WEBHOOK, json=payload_apify(), headers={"Authorization": "Bearer secreto-compartido"}
    )

    assert por_cabecera_propia.status_code == 200
    assert por_bearer.status_code == 200


def test_un_secreto_parecido_no_pasa(cliente):
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    respuesta = cliente.post(
        WEBHOOK, json=payload_apify(), headers={"X-AlertaV-Apify-Secret": "secreto-compartid"}
    )
    assert respuesta.status_code == 401


# --- 3. El Actor que entrega --------------------------------------------------
#
# El secreto responde "¿quién llama?"; esto responde "¿qué trae?". En Apify el
# secreto de un webhook es de CUENTA: todos los del panel llevan el mismo, así
# que un segundo Actor apuntado a esta URL pasa la autenticación entera.


def test_sin_lista_configurada_cualquier_actor_entrega(cliente):
    """La lista vacía deja la ruta como estaba, igual que el secreto vacío.

    No es laxitud: el id corto del Actor sólo se conoce mirando una entrega
    real, así que exigirlo desde el primer despliegue sería pedir un dato que
    todavía no existe.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = []

    respuesta = cliente.post(WEBHOOK, json=payload_apify(act_id=ACTOR_INSTAGRAM))

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "accepted"


def test_el_actor_autorizado_entrega(cliente):
    settings.APIFY_BOMBEROS_ACTOR_IDS = [ACTOR_X]

    respuesta = cliente.post(WEBHOOK, json=payload_apify())

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "accepted"


def test_otro_actor_de_la_misma_cuenta_no_entrega(cliente, tarea):
    """El caso que motivó todo esto: Instagram apuntado al webhook de Bomberos.

    Con el secreto correcto —lo tiene, es de cuenta— y sin este guard, sus posts
    entran por `parse_tweet`, no traen claves 10-x, y la corrida cierra en
    `success` con 0 insertados. La fila verde que queda en `collector_runs` es la
    señal que se mira para saber si el webhook de Bomberos está llegando, y así
    miente: el dataset ni siquiera tiene que leerse para hacer el daño.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    settings.APIFY_BOMBEROS_ACTOR_IDS = [ACTOR_X]

    respuesta = cliente.post(
        WEBHOOK,
        json=payload_apify(act_id=ACTOR_INSTAGRAM),
        headers={"X-AlertaV-Apify-Secret": "secreto-compartido"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ignored"
    assert tarea == [], "un Actor ajeno no puede hacer que se lea su dataset"


def test_el_rechazo_no_es_un_4xx(cliente):
    """Un 403 haría que Apify reintentara once veces y deshabilitara el webhook.

    Deshabilitaría el del Actor equivocado —el que ya no queremos— pero el
    operador vería «integración deshabilitada» y sospecharía del backend. Se
    acusa recibo y se grita por el log, misma regla que el evento de prueba.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = [ACTOR_X]

    respuesta = cliente.post(WEBHOOK, json=payload_apify(act_id=ACTOR_INSTAGRAM))

    assert respuesta.status_code == 200


def test_el_rechazo_devuelve_los_ids_para_pegarlos_en_la_variable(cliente):
    """El id que manda el webhook es el corto, no `usuario~actor`.

    Sin este dato en la respuesta y en el log, autorizar un Actor legítimo es
    adivinar cuál de los identificadores del panel es el que viaja.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = [ACTOR_X]

    cuerpo = cliente.post(WEBHOOK, json=payload_apify(act_id=ACTOR_INSTAGRAM)).json()

    assert ACTOR_INSTAGRAM in cuerpo["actor_ids"]


def test_la_entrega_aceptada_tambien_registra_el_id_del_actor(cliente, caplog):
    """El id sale en el camino feliz, no sólo en el rechazo.

    Con la lista apagada el rechazo no ocurre nunca, así que si el id sólo
    apareciera ahí habría que romper una entrega a propósito para poder
    autorizar al Actor legítimo. Este es el orden real: primero llega algo,
    después se cierra la puerta detrás.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = []

    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.apify"):
        cliente.post(WEBHOOK, json=payload_apify())

    aceptados = [r for r in caplog.records if "aceptado" in r.message]
    assert aceptados, "la entrega buena tiene que dejar registro"
    assert aceptados[-1].ids_actor == [ACTOR_X]
    assert aceptados[-1].guard_actor_activo is False


def test_autorizar_por_el_id_del_task_tambien_sirve(cliente):
    """Un webhook colgado del Task manda `actorTaskId`; el del Actor, `actId`.

    Son dos identidades de la misma corrida y el operador puede tener a mano
    cualquiera. Exigir una concreta rechazaría media configuración legítima.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = ["taskDeBomberos"]

    respuesta = cliente.post(
        WEBHOOK, json=payload_apify(act_id=ACTOR_X, task_id="taskDeBomberos")
    )

    assert respuesta.json()["status"] == "accepted"


def test_un_cuerpo_sin_identidad_no_pasa_el_guard_encendido(cliente):
    """Lo que no se puede verificar no pasa.

    Dejarlo pasar volvería el guard decorativo: bastaría mandar
    `{"defaultDatasetId": "…"}` a secas para saltárselo entero. Con la lista
    apagada este mismo cuerpo sí entra, y hay un test que lo fija.
    """
    settings.APIFY_BOMBEROS_ACTOR_IDS = [ACTOR_X]

    respuesta = cliente.post(WEBHOOK, json={"defaultDatasetId": "sueltoXYZ"})

    assert respuesta.json()["status"] == "ignored"


def test_el_secreto_no_alcanza_para_distinguir_actors(cliente, tarea):
    """El invariante que justifica que este guard exista y no sobre.

    Si el secreto bastara, la lista sería redundante. No basta: la MISMA
    credencial que autoriza al Actor de X autoriza al de Instagram, porque en
    Apify el secreto se escribe por webhook y todos llevan el de la cuenta.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    settings.APIFY_BOMBEROS_ACTOR_IDS = []
    cabeceras = {"X-AlertaV-Apify-Secret": "secreto-compartido"}

    for actor in (ACTOR_X, ACTOR_INSTAGRAM):
        respuesta = cliente.post(
            WEBHOOK, json=payload_apify(act_id=actor), headers=cabeceras
        )
        assert respuesta.status_code == 200, f"{actor} pasó la autenticación"

    assert len(tarea) == 2


def test_los_ids_se_leen_de_las_dos_secciones_del_cuerpo():
    """`resource` y `eventData` traen lo mismo por caminos distintos.

    Una plantilla personalizada puede dejar fuera cualquiera de las dos, y el
    guard no puede depender de la que falte.
    """
    solo_recurso = {"resource": {"actId": "A1"}}
    solo_evento = {"eventData": {"actorId": "A1"}}

    assert svc.extract_actor_ids(solo_recurso) == ["A1"]
    assert svc.extract_actor_ids(solo_evento) == ["A1"]
    # Y cuando vienen las dos con el mismo valor, no se repite: este resultado
    # se escribe en el log del rechazo y se pega a mano en la configuración.
    assert svc.extract_actor_ids(payload_apify()) == [ACTOR_X]


# --- 4. Lectura del dataset: la URL y el token -------------------------------


def test_el_token_no_viaja_en_la_query():
    """Invariante del repositorio, no una preferencia de estilo.

    Una URL con el token dentro termina en los logs de acceso del proxy y en
    `collector_runs.error`, que es la base de datos. La API de Apify acepta
    `?token=`; este backend no lo usa nunca.
    """
    url = svc.dataset_items_url(DATASET_ID)

    assert url == ITEMS_URL
    assert "token" not in url


@respx.mock
def test_el_token_viaja_en_la_cabecera():
    ruta = respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[]))

    asyncio.run(svc.fetch_dataset_items(DATASET_ID, limit=10))

    peticion = ruta.calls[0].request
    assert peticion.headers["Authorization"] == "Bearer token-de-prueba"
    assert b"token" not in peticion.url.query


@respx.mock
def test_un_error_servido_con_http_200_se_convierte_en_fallo_con_nombre():
    """La forma que tiene Apify de decir «este token no lee este dataset»."""
    from app.core.exceptions import CollectorError

    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json={"error": {"type": "insufficient-permissions"}})
    )

    with pytest.raises(CollectorError, match="rechazó la lectura"):
        asyncio.run(svc.fetch_dataset_items(DATASET_ID, limit=10))


# --- 5. Del tuit al despacho -------------------------------------------------


def test_solo_pasan_los_tuits_con_una_clave_configurada():
    """La cuenta de una central publica mucho más que despachos."""
    claves = ["10-4", "12"]

    assert svc.parse_tweet(tuit("81 * RUTA 68 KM 42 * CLAVE 10-4"), claves) is not None
    assert svc.parse_tweet(tuit("81 * DIEGO COOK / GUACOLDA * CLAVE 12"), claves) is not None
    assert svc.parse_tweet(tuit("Gracias a la comunidad por su apoyo"), claves) is None
    assert svc.parse_tweet(tuit("Corte de agua programado en Playa Ancha"), claves) is None


def test_la_clave_12_literal_entra_y_es_la_que_no_entraba_antes():
    """Regresión del fallo mudo que corrige `vocabulary.parse_key`.

    `normalise_code("12")` devuelve None —un número suelto no es una clave del
    lado del texto— y `matches_key` saltaba esa clave con `continue`. Resultado:
    configurar `12` no daba error, no daba advertencia y no daba coincidencias.
    """
    despacho = svc.parse_tweet(tuit("81 * DIEGO COOK / GUACOLDA * CLAVE 12"), ["12"])

    assert despacho is not None
    assert despacho.key == "12"


def test_una_impostora_de_la_clave_no_entra():
    """`10-40` es emanación de gas, no rescate vehicular."""
    assert svc.parse_tweet(tuit("Clave 10-40 emanación de gas, Av. Brasil"), ["10-4"]) is None


def test_el_identificador_sale_del_tuit_y_no_del_texto():
    """La central corrige un despacho editando el mensaje.

    Con un id derivado del texto, cada corrección sería un segundo incidente en
    el mapa en vez de una actualización de la misma fila.
    """
    original = svc.parse_tweet(tuit("81 * RUTA 68 * CLAVE 10-4", id_="99"), ["10-4"])
    corregido = svc.parse_tweet(tuit("81 * RUTA 68 KM 42 * CLAVE 10-4", id_="99"), ["10-4"])

    assert original is not None and corregido is not None
    assert original.guid == corregido.guid == "x:99"


def test_el_texto_se_busca_por_alias_de_campo():
    """Migrar de Actor es cambiar una línea en el Schedule, no tocar código."""
    for campo in ("full_text", "fullText", "text", "content", "rawContent"):
        item = {"id": "7", campo: "81 * RUTA 68 * CLAVE 10-4"}
        assert svc.parse_tweet(item, ["10-4"]) is not None, campo


def test_un_despacho_viejo_se_descarta_y_uno_sin_fecha_pasa():
    """Asimetría deliberada. Ver el docstring de `is_fresh`."""
    viejo = svc.parse_tweet(tuit("81 * RUTA 68 * CLAVE 10-4", minutos=600), ["10-4"])
    sin_fecha = svc.parse_tweet({"id": "3", "text": "81 * RUTA 68 * CLAVE 10-4"}, ["10-4"])

    assert viejo is not None and sin_fecha is not None
    assert svc.is_fresh(viejo, now=AHORA, max_age_minutes=180) is False
    assert svc.is_fresh(sin_fecha, now=AHORA, max_age_minutes=180) is True


# --- 6. La tarea de fondo: no lanza nunca ------------------------------------


class SesionFalsa:
    """Sustituye a `AsyncSession`. No hay base en la suite."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None


class ServicioFalso:
    """Registra lo que la tarea de fondo le pide, que es lo que se observa."""

    ultimo: ServicioFalso | None = None

    def __init__(self, _session) -> None:
        self.status: CollectorStatus | None = None
        self.error: str | None = None
        self.eventos: list = []
        self.params: dict = {}
        ServicioFalso.ultimo = self

    async def start_run(self, *, source, collector, params):
        self.source = source
        self.collector = collector
        self.params = params
        return object()

    async def finish_run(self, _run, *, status, fetched=0, inserted=0, duplicate=0, error=None):
        self.status = status
        self.error = error
        self.fetched = fetched
        self.inserted = inserted

    async def ingest_batch(self, events):
        self.eventos = list(events)
        return type("R", (), {"inserted": len(events), "duplicated": 0, "received": len(events)})()


@pytest.fixture
def servicio(monkeypatch):
    monkeypatch.setattr(svc, "AsyncSessionLocal", SesionFalsa)
    monkeypatch.setattr(svc, "IngestService", ServicioFalso)
    # La geocodificación queda APAGADA por defecto en este archivo. No es
    # desinterés: `geocode_dispatches` habla con Nominatim, que respeta 1 req/s,
    # y dejarla encendida en cada test metería una espera real por despacho para
    # verificar cosas que no tienen que ver con el punto. Los tests que sí la
    # ejercitan la encienden y montan la respuesta (ver el bloque del final).
    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 0)
    return ServicioFalso


@respx.mock
def test_la_tarea_ingiere_los_despachos_y_deja_la_corrida_en_success(servicio):
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                tuit("81 * RUTA 68 KM 42 * CLAVE 10-4", id_="1"),
                tuit("Gracias a la comunidad", id_="2"),
                tuit("22 * DIEGO COOK / GUACOLDA * CLAVE 12", id_="3"),
            ],
        )
    )
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4", "12"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    hecho = ServicioFalso.ultimo
    assert hecho is not None
    assert hecho.status is CollectorStatus.SUCCESS
    assert hecho.source is EventSource.BOMBEROS
    assert len(hecho.eventos) == 2, "el agradecimiento no es un despacho"
    assert hecho.inserted == 2


@respx.mock
def test_la_corrida_queda_registrada_con_el_dataset_y_sin_el_token(servicio):
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[]))

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    params = ServicioFalso.ultimo.params
    assert params["dataset_id"] == DATASET_ID
    assert "token-de-prueba" not in str(params), "`params` se serializa a la base"


@respx.mock
def test_un_5xx_de_apify_no_escapa_como_excepcion(servicio, monkeypatch):
    """El contrato absoluto de la tarea de fondo.

    Una excepción escapando de una `BackgroundTask` se pierde: no la ve el
    llamador —que ya respondió 200— ni `collector_runs`. Y a diferencia de un
    collector, acá no hay corrida siguiente que cierre el hueco.
    """
    import app.collectors.geoservices as geoservices

    async def sin_dormir(_s: float) -> None:
        return None

    monkeypatch.setattr(geoservices.asyncio, "sleep", sin_dormir)
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(503))

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    hecho = ServicioFalso.ultimo
    assert hecho.status is CollectorStatus.FAILED
    assert "503" in (hecho.error or "")


@respx.mock
def test_un_dataset_con_forma_inesperada_tampoco_escapa(servicio):
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json={"no": "soy una lista"}))

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    assert ServicioFalso.ultimo.status is CollectorStatus.FAILED


@respx.mock
def test_un_perfil_caido_deja_la_corrida_en_partial(servicio):
    """Los Actors no fallan con un perfil privado: empujan un item con error.

    Sin mirarlo, una cuenta que dejó de existir se ve igual que una cuenta que
    no publicó nada.
    """
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"error": "no_items", "errorDescription": "cuenta suspendida", "username": "CGI_CBV"},
                tuit("81 * RUTA 68 * CLAVE 10-4", id_="5"),
            ],
        )
    )
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    hecho = ServicioFalso.ultimo
    assert hecho.status is CollectorStatus.PARTIAL
    assert "CGI_CBV" in (hecho.error or ""), "el motivo tiene que nombrar el perfil"
    assert len(hecho.eventos) == 1, "el despacho bueno del mismo lote no se pierde"


@respx.mock
@respx.mock
def test_una_clave_no_configurada_deja_aviso_en_vez_de_desaparecer(servicio, caplog):
    """El hallazgo del 2026-09-02, con el tuit real de @CGI_CBV.

    La central publica «CLAVE 5-1» y `BOMBEROS_ACCIDENT_KEYS` sólo tenía la
    familia 10 y el 12. Ese despacho —un accidente en Avenida España con
    Avenida Argentina, de la fuente de confianza 1.00, con la esquina exacta en
    el texto— se descartaba sin una sola línea de log: `parse_tweet` devolvía
    None igual que ante un tuit cualquiera de la cuenta, y las dos cosas se
    veían idénticas.

    NO se ingiere. Una clave sin significado en `CLAVE_MEANINGS` no se puede
    tipificar, y adivinarle el tipo a un despacho de peso 1.00 es peor que
    perderlo. Lo que cambia es que ahora se sabe que existe.
    """
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                tuit("91, 71 * AVENIDA ESPANA / AVENIDA ARGENTINA * CLAVE 5-1", id_="1"),
                tuit("Saludos a la comunidad en su aniversario", id_="2"),
            ],
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.services.apify_webhook_service"):
        asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    avisos = [r for r in caplog.records if "no están en BOMBEROS_ACCIDENT_KEYS" in r.message]
    assert avisos, "una clave que la central usa y nosotros tiramos tiene que verse"
    assert "5-1" in avisos[-1].claves
    # El saludo NO cuenta: no traía clave, y ése sí es silencio legítimo.
    assert sum(avisos[-1].claves.values()) == 1


@respx.mock
def test_un_lote_sin_despachos_no_es_un_aviso(servicio):
    """La central publica muchas cosas que no son claves.

    Avisarlo en cada entrega enseñaría a ignorar los avisos, que es como se
    pierde el que sí importa. Mismo criterio que el feed sin 10-4.

    (El `@respx.mock` faltaba: este test venía pasando por el router global que
    dejaba abierto el test anterior, o sea dependiendo del orden de ejecución.
    Insertar un test entremedio lo destapó.)
    """
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[tuit("Feliz aniversario a la 3a Compañía")])
    )

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    hecho = ServicioFalso.ultimo
    assert hecho.status is CollectorStatus.SUCCESS
    assert hecho.eventos == []


@respx.mock
def test_el_evento_ingerido_lleva_la_confianza_y_el_tipo_de_bomberos(servicio):
    """1.00 y `accident`: es la fuente que lleva un incidente a certeza."""
    from app.models.enums import EventType

    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[tuit("81 * RUTA 68 KM 42 * CLAVE 10-4", id_="9")])
    )
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    evento = ServicioFalso.ultimo.eventos[0]
    assert evento.source is EventSource.BOMBEROS
    assert evento.type is EventType.ACCIDENT
    assert evento.confidence == 1.0
    assert evento.external_id.startswith("bomberos:")
    assert evento.raw_data["_collector"] == svc.COLLECTOR_NAME


@respx.mock
def test_el_mismo_despacho_produce_el_mismo_external_id_por_las_dos_puertas(servicio):
    """La razón de que `dispatches_to_events` sea una función libre.

    Si el webhook armara su propio evento, la misma 10-4 entraría con
    `external_id` distinto según la puerta y aparecería dos veces en el mapa.
    """
    from app.collectors.traffic.bomberos_10_4_worker import Dispatch, build_external_id

    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[tuit("81 * RUTA 68 * CLAVE 10-4", id_="77")])
    )
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))
    por_webhook = ServicioFalso.ultimo.eventos[0].external_id

    por_feed = build_external_id(
        Dispatch(
            key="10-4",
            address="x",
            occurred_at=None,
            commune=None,
            raw_text="x",
            guid="x:77",
        )
    )

    assert por_webhook == por_feed


# =============================================================================
#  El paso que faltaba: geocodificación
# =============================================================================
#
# Sin coordenadas, un despacho **no llega al mapa**. No es una degradación
# elegante, es un cero: `cluster_unassigned_events` filtra por
# `geom IS NOT NULL` y el Paso B sólo adosa alertas de SENAPRED a incidentes
# que ya existen. Durante todo ese tiempo la fuente de confianza 1.00 del
# catálogo —la única que lleva un incidente a certeza por sí sola— quedaba
# consultable en `/events` y ausente de los contadores de Incendios, Accidentes
# y Otras emergencias.
#
# Los tests de abajo cubren las dos mitades: que el punto llega cuando Nominatim
# responde, y que **nada se pierde** cuando no responde. La segunda importa más:
# el paso se añadió para ganar el mapa, no para arriesgar la señal.

NOMINATIM_URL = settings.NOMINATIM_URL


def _nominatim(lat: str = "-33.045", lon: str = "-71.62"):
    return httpx.Response(
        200,
        json=[
            {
                "lat": lat,
                "lon": lon,
                "display_name": "Diego Cook con Guacolda, Valparaíso",
                "osm_type": "node",
                "importance": 0.41,
            }
        ],
    )


@pytest.fixture
def con_geocodificacion(monkeypatch):
    """Enciende el presupuesto y quita la espera del rate limiter."""
    from app.collectors import nominatim

    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 5)
    monkeypatch.setattr(nominatim.settings, "NOMINATIM_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(nominatim, "_LIMITER", nominatim.RateLimiter(0.0))


@respx.mock
def test_el_despacho_geocodificado_entra_con_coordenadas(servicio, con_geocodificacion):
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200, json=[tuit("81 * DIEGO COOK / GUACOLDA * 10-4", id_="5")]
        )
    )
    respx.get(url__startswith=NOMINATIM_URL).mock(return_value=_nominatim())
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    evento = ServicioFalso.ultimo.eventos[0]
    assert evento.lat == pytest.approx(-33.045)
    assert evento.lon == pytest.approx(-71.62)
    # El punto y lo que lo produjo van SEPARADOS: si mañana una coordenada está
    # mal, esto dice si falló el decodificador o el geocodificador.
    assert evento.raw_data["_geocoding"]["provider"] == "nominatim"
    assert evento.raw_data["_extraction"]["street_1"]


@respx.mock
def test_si_nominatim_falla_el_despacho_entra_igual(servicio, con_geocodificacion):
    """La propiedad que no se puede perder al ganar el punto.

    Una 10-4 vale por su certeza sobre el hecho. Tirarla porque OpenStreetMap no
    conoce una esquina sería cambiar un problema de cobertura por uno de datos.
    """
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200, json=[tuit("81 * CALLE INEXISTENTE / OTRA * 10-4", id_="6")]
        )
    )
    respx.get(url__startswith=NOMINATIM_URL).mock(side_effect=httpx.ConnectError("sin red"))
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    hecho = ServicioFalso.ultimo
    assert hecho.status is CollectorStatus.SUCCESS, "un fallo de Nominatim no es un fallo de la corrida"
    assert len(hecho.eventos) == 1
    assert hecho.eventos[0].lat is None
    assert hecho.eventos[0].raw_data["_geocoding"] is None


@respx.mock
def test_el_presupuesto_de_geocodificacion_se_respeta(servicio, monkeypatch):
    """Con el tope en cero no se toca la red, y los despachos entran igual."""
    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 0)
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[tuit("81 * RUTA 68 * 10-4", id_="7")])
    )
    ruta = respx.get(url__startswith=NOMINATIM_URL).mock(return_value=_nominatim())
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-4"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    assert not ruta.called
    assert len(ServicioFalso.ultimo.eventos) == 1


@respx.mock
def test_un_incendio_estructural_no_entra_como_accidente(servicio):
    """El literal `EventType.ACCIDENT` que la ingesta ampliada dejó obsoleto.

    `BOMBEROS_ACCIDENT_KEYS` trae la familia 10 entera desde hace tiempo; el tipo
    seguía fijo. Un 10-1 quedaba en la familia `traffic`, incapaz de corroborar
    ninguna señal de fuego, y sumando al contador equivocado de la interfaz.
    """
    from app.models.enums import EventType

    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                tuit("B2 * ALDUNATE 1200 * 10-1", id_="10"),
                tuit("M5 * CAMINO LA POLVORA * 10-2", id_="11"),
                tuit("81 * DIEGO COOK / GUACOLDA * CLAVE 12", id_="12"),
                tuit("21 * RUTA 68 KM 42 * 10-4", id_="13"),
            ],
        )
    )
    settings.BOMBEROS_ACCIDENT_KEYS = ["10-1", "10-2", "10-4", "12"]

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    tipos = [evento.type for evento in ServicioFalso.ultimo.eventos]
    assert tipos == [
        EventType.STRUCTURAL_FIRE,
        EventType.WILDFIRE,
        EventType.OTHER,
        EventType.ACCIDENT,
    ]


# =============================================================================
#  El secreto: los dos fallos que producían "secreto inválido" sin explicarlo
# =============================================================================


def test_un_secreto_con_caracteres_no_ascii_se_compara_sin_reventar(monkeypatch):
    """`secrets.compare_digest` con dos `str` **sólo acepta ASCII**.

    No devuelve False ante un carácter fuera de ASCII: lanza `TypeError`. Eso
    subía como 500, y un 500 lo lee Apify como fallo transitorio: reintenta once
    veces y termina deshabilitando la integración, sin que el log diga jamás
    "secreto inválido".

    Se prueba contra `_authorised` y no por HTTP a propósito, porque el camino
    real pasa por una capa que este test no puede reproducir con `TestClient`:
    las cabeceras viajan como bytes y Starlette las decodifica con **latin-1**,
    así que un secreto UTF-8 con `ñ` llega al endpoint convertido en `Ã±`. Esa
    cadena —no la original— es la que entraba a `compare_digest` y la hacía
    lanzar. `TestClient` rechaza antes el valor no-ASCII, de modo que por HTTP
    el fallo es inalcanzable y la regresión pasaría inadvertida.
    """
    from app.api.v1.endpoints.apify import _authorised

    monkeypatch.setattr(settings, "APIFY_WEBHOOK_SECRET", "contraseña-ñandú")

    # Lo que de verdad llega tras el viaje UTF-8 → latin-1 de Starlette.
    mojibake = "contraseña-ñandú".encode().decode("latin-1")

    assert _authorised("contraseña-ñandú", None) is True
    assert _authorised(mojibake, None) is False, "distinto es False, no TypeError"
    assert _authorised("otra-cosa", None) is False


def test_authorization_sin_el_esquema_bearer_tambien_sirve(cliente):
    """El panel de Apify permite escribir el valor a secas.

    Antes sólo se probaba el valor pelado del prefijo, así que un
    `Authorization: <secreto>` —que es una configuración razonable— quedaba
    fuera aunque el secreto fuera el correcto.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    respuesta = cliente.post(
        WEBHOOK, json=payload_apify(), headers={"Authorization": "secreto-compartido"}
    )
    assert respuesta.status_code == 200


def test_el_401_dice_que_cabecera_hay_que_poner(cliente):
    """Depurar esto desde Apify es leer un 401 y adivinar. El cuerpo ayuda."""
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"
    respuesta = cliente.post(WEBHOOK, json=payload_apify())

    assert respuesta.status_code == 401
    detalle = respuesta.json()["detail"]
    assert "X-AlertaV-Apify-Secret" in detalle
    assert "secreto-compartido" not in detalle, "el secreto no se devuelve nunca"


# --- 8. El nivel del log del rechazo -----------------------------------------
#
# El 401 es el mismo en los dos casos; lo que cambia es a quién despierta. Ver
# la sección "El rechazo es siempre 401; el nivel del log, no" del endpoint.


def test_una_llamada_sin_credencial_se_registra_como_info(cliente, caplog):
    """Los webhooks huérfanos de Apify no pueden encender la alarma de Render.

    Disparan peticiones vacías contra esta URL a la par de las válidas. El
    rechazo tiene que seguir siendo 401, pero registrarlo como `WARNING`
    inundaba el log de producción y entrenaba a quien lo mira a ignorar los
    avisos de esta ruta.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"

    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.apify"):
        respuesta = cliente.post(WEBHOOK, json=payload_apify())

    assert respuesta.status_code == 401, "el rechazo no se relaja: sigue siendo 401"

    rechazos = [
        registro for registro in caplog.records if "rechazado" in registro.getMessage()
    ]
    assert len(rechazos) == 1
    assert rechazos[0].levelno == logging.INFO
    assert rechazos[0].trae_cabecera_propia is False


def test_una_llamada_con_secreto_equivocado_sigue_siendo_warning(cliente, caplog):
    """Esto sí es una integración nuestra rota, y hay que enterarse hoy.

    Un secreto mal copiado o rotado a medias significa despachos de Bomberos que
    no están entrando. Es justo el caso que el saneamiento de logs NO puede
    silenciar.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"

    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.apify"):
        respuesta = cliente.post(
            WEBHOOK,
            json=payload_apify(),
            headers={"X-AlertaV-Apify-Secret": "secreto-equivocado"},
        )

    assert respuesta.status_code == 401

    rechazos = [
        registro for registro in caplog.records if "rechazado" in registro.getMessage()
    ]
    assert len(rechazos) == 1
    assert rechazos[0].levelno == logging.WARNING


def test_una_authorization_vacia_cuenta_como_sin_credencial(cliente, caplog):
    """Una cabecera presente pero vacía no es una integración mal configurada.

    Lo que decide el nivel es si llegó algún VALOR utilizable, no si el nombre
    de la cabecera venía en la petición: `Authorization: ` a secas es ruido, no
    un secreto equivocado.
    """
    settings.APIFY_WEBHOOK_SECRET = "secreto-compartido"

    with caplog.at_level(logging.INFO, logger="app.api.v1.endpoints.apify"):
        respuesta = cliente.post(
            WEBHOOK, json=payload_apify(), headers={"Authorization": "   "}
        )

    assert respuesta.status_code == 401
    rechazos = [
        registro for registro in caplog.records if "rechazado" in registro.getMessage()
    ]
    assert rechazos[0].levelno == logging.INFO
