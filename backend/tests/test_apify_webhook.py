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


def payload_apify(dataset_id: str | None = DATASET_ID) -> dict:
    """El cuerpo que manda Apify en `ACTOR.RUN.SUCCEEDED`, recortado."""
    recurso: dict = {
        "id": "runIdDePrueba",
        "actId": "apify~tweet-scraper",
        "status": "SUCCEEDED",
        "startedAt": "2026-09-01T12:00:00.000Z",
        "finishedAt": "2026-09-01T12:01:30.000Z",
    }
    if dataset_id is not None:
        recurso["defaultDatasetId"] = dataset_id
    return {
        "userId": "usuarioDePrueba",
        "createdAt": "2026-09-01T12:01:31.000Z",
        "eventType": "ACTOR.RUN.SUCCEEDED",
        "eventData": {"actorId": "apify~tweet-scraper", "actorRunId": "runIdDePrueba"},
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
            "APIFY_WEBHOOK_MAX_ITEMS",
            "APIFY_WEBHOOK_MAX_AGE_MINUTES",
            "BOMBEROS_ACCIDENT_KEYS",
            "BOMBEROS_MAX_LLM_CALLS",
        )
    }
    settings.APIFY_TOKEN = "token-de-prueba"
    settings.APIFY_WEBHOOK_SECRET = ""
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


# --- 3. Lectura del dataset: la URL y el token -------------------------------


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


# --- 4. Del tuit al despacho -------------------------------------------------


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


# --- 5. La tarea de fondo: no lanza nunca ------------------------------------


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
def test_un_lote_sin_despachos_no_es_un_aviso(servicio):
    """La central publica muchas cosas que no son claves.

    Avisarlo en cada entrega enseñaría a ignorar los avisos, que es como se
    pierde el que sí importa. Mismo criterio que el feed sin 10-4.
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
