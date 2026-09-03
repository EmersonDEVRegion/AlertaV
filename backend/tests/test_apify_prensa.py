"""La segunda puerta de X: prensa e instituciones viales.

Existe porque `/apify/webhook` está cableado a Bomberos —claves `10-x`,
confianza 1.00— y meter por ahí al MTT, a una concesionaria o a un diario tenía
dos desenlaces y ninguno servía: o se descartaban enteros, o entraban con el peso
de un despacho oficial.

Casi todo este archivo prueba la lista blanca. Es la única pieza que impide que
un término de búsqueda mal puesto en el Task convierta a cualquier vecino en
fuente confirmada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.enums import EventSource, EventType
from app.services import apify_press_service as svc

RUTA = "/api/v1/apify/webhook/prensa"
AHORA = datetime.now(UTC)


def tuit(
    texto: str,
    *,
    handle: str = "sitiodelsuceso",
    id_: str = "1",
    minutos: int = 5,
) -> dict:
    return {
        "id": id_,
        "full_text": texto,
        "createdAt": (AHORA - timedelta(minutes=minutos)).isoformat(),
        "author": {"userName": handle},
    }


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_SECRET", "")
    monkeypatch.setattr(settings, "APIFY_PRENSA_ACTOR_IDS", [])
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_MAX_AGE_MINUTES", 180)


# =============================================================================
#  1. La lista blanca de cuentas
# =============================================================================


def test_una_cuenta_declarada_entra_con_su_banda():
    parsed = svc.parse_tweet(tuit("Accidente en Av. España, Valparaíso."))

    assert parsed is not None
    assert parsed.handle == "sitiodelsuceso"
    assert svc.HANDLES[parsed.handle].source is EventSource.MEDIA


def test_una_cuenta_no_declarada_no_existe_para_esta_ruta():
    """El caso que motivó la ruta.

    Con términos de búsqueda en el Task, este endpoint recibe tuits de cualquiera
    que mencione «accidente» y «Valparaíso». Sin la lista blanca, ese vecino
    entraría al mapa como fuente.
    """
    parsed = svc.parse_tweet(tuit("Choque terrible en Av. España!!", handle="vecino_random"))

    assert parsed is not None, "se parsea..."
    assert parsed.handle not in svc.HANDLES, "...pero no está autorizado"


def test_un_tuit_sin_autor_se_descarta():
    """Sin autor no hay lista blanca que aplicar, y sin lista blanca esta ruta
    acepta lo que sea."""
    sin_autor = {"id": "1", "full_text": "Accidente en Av. España.", "createdAt": "2026-09-02T22:00:00Z"}

    assert svc.parse_tweet(sin_autor) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"author": {"userName": "@SitioDelSuceso"}},
        {"author": {"screen_name": "sitiodelsuceso"}},
        {"user": {"screen_name": "SITIODELSUCESO"}},
        {"username": "@sitiodelsuceso"},
    ],
)
def test_el_autor_se_normaliza_venga_como_venga(payload):
    """Los Actors de X anidan el autor de maneras distintas, y el panel escribe
    el handle con arroba o sin ella, en mayúsculas o no."""
    assert svc.extract_handle(payload) == "sitiodelsuceso"


# =============================================================================
#  2. Las bandas de confianza
# =============================================================================


def test_ninguna_cuenta_de_esta_ruta_alcanza_la_banda_de_bomberos():
    """El invariante que separa las dos puertas.

    1.00 es la única banda que por sí sola confirma un incidente, y está
    reservada a quien fue al lugar. Ninguna cuenta de X califica: ni el MTT, que
    informa desde su escritorio.
    """
    for handle, cuenta in svc.HANDLES.items():
        assert cuenta.confidence < 1.0, handle
        assert cuenta.source is not EventSource.BOMBEROS, handle


def test_el_mtt_pesa_mas_que_la_red_ciudadana():
    """El orden tiene que reflejar cuánto verifica cada uno antes de publicar."""
    assert (
        svc.HANDLES["ttivalparaiso"].confidence
        > svc.HANDLES["sitiodelsuceso"].confidence
        > svc.HANDLES["rnevalparaiso"].confidence
    )


def test_un_tuit_de_prensa_pesa_menos_que_su_propia_nota_publicada():
    """0.55 contra el 0.60 de `prensa_local`, y no es un descuido.

    Un tuit se publica antes de pasar por la edición que sí tiene la nota del
    portal. La fuente es la misma; el filtro editorial, no.
    """
    assert svc.HANDLES["sitiodelsuceso"].confidence < settings.LOCAL_NEWS_CONFIDENCE


def test_cada_cuenta_declara_por_que_merece_su_banda():
    """Sumar una cuenta tiene que ser una decisión escrita, no un handle suelto."""
    for handle, cuenta in svc.HANDLES.items():
        assert len(cuenta.motivo.strip()) > 40, handle


# =============================================================================
#  3. La ruta
# =============================================================================


@pytest.fixture
def cliente(monkeypatch):
    encoladas: list = []

    async def falsa(dataset_id, payload):
        encoladas.append(dataset_id)

    monkeypatch.setattr(
        "app.api.v1.endpoints.apify.process_press_dataset", falsa
    )
    with TestClient(app) as client:
        yield client, encoladas


def payload(dataset_id="ds1", task_id="taskPrensa"):
    return {
        "eventType": "ACTOR.RUN.SUCCEEDED",
        "eventData": {"actorId": "actorX", "actorTaskId": task_id},
        "resource": {
            "actId": "actorX",
            "actorTaskId": task_id,
            "defaultDatasetId": dataset_id,
        },
    }


def test_una_entrega_del_task_de_prensa_se_encola(cliente):
    client, encoladas = cliente

    respuesta = client.post(RUTA, json=payload())

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "accepted"
    assert encoladas == ["ds1"]


def test_el_task_de_bomberos_no_entra_por_la_puerta_de_prensa(cliente, monkeypatch):
    """Los dos Tasks comparten `actId`: sólo el `actorTaskId` los separa.

    Sin esta distinción, autorizar una puerta autorizaría la otra y los
    despachos de la central entrarían con la banda de prensa —o al revés, que es
    peor.
    """
    client, encoladas = cliente
    monkeypatch.setattr(settings, "APIFY_PRENSA_ACTOR_IDS", ["taskPrensa"])

    respuesta = client.post(RUTA, json=payload(task_id="taskBomberos"))

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ignored"
    assert encoladas == [], "un Task ajeno no puede hacer que se lea su dataset"


def test_el_rechazo_no_es_un_4xx(cliente, monkeypatch):
    """Un 403 haría que Apify reintentara once veces y deshabilitara el webhook."""
    client, _ = cliente
    monkeypatch.setattr(settings, "APIFY_PRENSA_ACTOR_IDS", ["taskPrensa"])

    assert client.post(RUTA, json=payload(task_id="otro")).status_code == 200


def test_sin_dataset_no_provoca_un_4xx(cliente):
    client, _ = cliente
    cuerpo = payload()
    del cuerpo["resource"]["defaultDatasetId"]

    respuesta = client.post(RUTA, json=cuerpo)

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ignored"


def test_con_secreto_configurado_una_llamada_sin_el_es_401(cliente, monkeypatch):
    client, _ = cliente
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_SECRET", "secreto")

    assert client.post(RUTA, json=payload()).status_code == 401


def test_el_secreto_es_el_mismo_de_la_otra_puerta(cliente, monkeypatch):
    """En Apify el secreto es de cuenta, no de webhook.

    Por eso NO distingue las dos puertas, y por eso hace falta la lista de
    Tasks: es el único mecanismo que las separa.
    """
    client, _ = cliente
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_SECRET", "secreto")

    respuesta = client.post(
        RUTA, json=payload(), headers={"X-AlertaV-Apify-Secret": "secreto"}
    )

    assert respuesta.status_code == 200


# =============================================================================
#  4. Clasificación
# =============================================================================


def test_clasifica_con_el_lexico_de_prensa_y_no_con_claves():
    """Estas cuentas escriben en prosa. Buscarles claves 10-x sería el error que
    esta ruta existe para evitar."""
    from app.collectors.vocabulary import clasificar_noticia, es_emergencia

    texto = "Accidente de tránsito en Ruta 68 a la altura de Curacaví deja dos lesionados"

    assert es_emergencia(texto) is True
    assert clasificar_noticia(texto) is EventType.ACCIDENT


def test_un_tuit_viejo_se_descarta():
    viejo = svc.parse_tweet(tuit("Accidente en Av. España.", minutos=400))

    assert svc.is_fresh(viejo, now=AHORA, max_age_minutes=180) is False


def test_un_tuit_sin_fecha_se_considera_fresco():
    """Misma decisión que en Instagram: descartarlo perdería un accidente por un
    campo que el Actor no llenó, y el `external_id` atrapa el reproceso."""
    sin_fecha = svc.parse_tweet({"id": "9", "full_text": "Choque.", "author": {"userName": "sitiodelsuceso"}})

    assert svc.is_fresh(sin_fecha, now=AHORA, max_age_minutes=180) is True
