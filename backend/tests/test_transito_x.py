"""@TTIValparaiso por el webhook de Apify (2026-10-06).

La cuenta del MTT viaja en el mismo Task que las centrales de Bomberos. Sus
tuits no tienen clave: hasta esto se descartaban como «cuenta sin tabla». Ahora
se separan antes de buscar tabla y pasan por la tubería del portal del MTT
(`resolver_avisos` y `eventos_de_avisos`), en una corrida propia.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
import respx

from app.collectors.nominatim import GeocodeResult
from app.collectors.traffic import transito_x as tx
from app.collectors.traffic import transporteinforma_worker as worker
from app.core.config import settings
from app.models.enums import CollectorStatus, EventSource, EventType
from app.services import apify_webhook_service as svc
from app.services.collector_health import COLLECTOR_ROLES, _intervalo
from tests.test_apify_webhook import (
    DATASET_ID,
    ITEMS_URL,
    RepoFalso,
    ServicioFalso,
    SesionFalsa,
    payload_apify,
)

AHORA = datetime.now(UTC)

CHOQUE = (
    "⚠️#Precaución | Accidente de tránsito en Av. España con Uno Norte, "
    "#ViñadelMar. Transite con precaución. https://t.co/abc123"
)
DESVIO = (
    "🚧 #DesvíoDeTránsito | Por trabajos, cierre de calzada en calle Valparaíso "
    "entre Ecuador y Quillota, #ViñadelMar. Desvío por Arlegui."
)
SALUDO = (
    "¡BUENOS DÍAS! #BuenMartes. Te recomendamos conducir a la defensiva para "
    "evitar incidentes viales."
)


def tuit_tti(texto: str, *, id_: str, minutos: int = 5) -> dict:
    momento = AHORA - timedelta(minutes=minutos)
    return {
        "id": id_,
        "full_text": texto,
        "createdAt": momento.isoformat(),
        "url": f"https://x.com/TTIValparaiso/status/{id_}",
        "author": {"userName": "TTIValparaiso"},
    }


def despacho(texto: str, *, id_: str) -> dict:
    return {
        "id": id_,
        "full_text": texto,
        "createdAt": (AHORA - timedelta(minutes=5)).isoformat(),
        "url": f"https://x.com/CGI_CBV/status/{id_}",
        "author": {"userName": "CGI_CBV"},
    }


# --- Piezas puras ------------------------------------------------------------


def test_la_cuenta_de_transito_se_reconoce_con_o_sin_arroba():
    assert tx.es_cuenta_transito("@TTIValparaiso")
    assert tx.es_cuenta_transito("ttivalparaiso")
    assert not tx.es_cuenta_transito("@CGI_CBV")
    assert not tx.es_cuenta_transito(None)


def test_sin_cuentas_configuradas_no_hay_transito(monkeypatch):
    monkeypatch.setattr(settings, "APIFY_X_TRANSITO_HANDLES", [])
    assert not tx.es_cuenta_transito("@TTIValparaiso")


def test_el_texto_pierde_enlaces_y_almohadillas():
    limpio = tx.limpiar_texto(CHOQUE)
    assert "https://" not in limpio
    assert "#" not in limpio
    assert "ViñadelMar" in limpio


def test_el_aviso_toma_el_id_del_tuit_y_guarda_el_enlace():
    aviso = tx.aviso_de_tuit(
        texto=CHOQUE,
        identificador="1890",
        publicado=AHORA,
        cuenta="@TTIValparaiso",
        url="https://x.com/TTIValparaiso/status/1890",
    )
    assert aviso is not None
    assert aviso.notice_id == "x:1890"
    assert worker.external_id_de(aviso, EventType.ACCIDENT) == "mtt:x:1890"
    assert worker.external_id_de(aviso, EventType.ROAD_CLOSURE) == "mtt:closure:x:1890"
    assert aviso.raw["url"].endswith("/1890")
    assert aviso.raw["_x"]["texto_original"] == CHOQUE


def test_un_tuit_vacio_no_es_aviso():
    assert (
        tx.aviso_de_tuit(
            texto="https://t.co/x", identificador="1", publicado=None, cuenta=None, url=None
        )
        is None
    )


def test_se_clasifica_con_el_lexico_del_mtt_y_los_choques_van_primero():
    avisos = [
        tx.aviso_de_tuit(texto=t, identificador=str(i), publicado=AHORA, cuenta=None, url=None)
        for i, t in enumerate([DESVIO, SALUDO, CHOQUE])
    ]
    clasificados, descartados = tx.clasificar([a for a in avisos if a])
    assert [tipo for _, tipo in clasificados] == [EventType.ACCIDENT, EventType.ROAD_CLOSURE]
    assert descartados == 1, "el saludo no es ni choque ni corte"


def test_un_tuit_viejo_no_describe_el_presente():
    viejo = tx.aviso_de_tuit(
        texto=CHOQUE, identificador="1", publicado=AHORA - timedelta(hours=7), cuenta=None, url=None
    )
    sin_fecha = tx.aviso_de_tuit(
        texto=CHOQUE, identificador="2", publicado=None, cuenta=None, url=None
    )
    assert viejo and sin_fecha
    assert not tx.es_fresco(viejo, ahora=AHORA, max_age_minutes=360)
    assert tx.es_fresco(sin_fecha, ahora=AHORA, max_age_minutes=360)


def test_la_salud_conoce_la_corrida_de_transito(monkeypatch):
    assert COLLECTOR_ROLES["transporte_informa_x"] == {"traffic": "apoyo"}
    monkeypatch.setattr(settings, "APIFY_X_CANARIO_IDS", ["canario"])
    assert _intervalo("transporte_informa_x") == settings.APIFY_X_CANARIO_HORAS * 3600
    monkeypatch.setattr(settings, "APIFY_X_CANARIO_IDS", [])
    assert _intervalo("transporte_informa_x") == settings.APIFY_X_SCHEDULE_MINUTES * 60


def test_ttivalparaiso_es_una_cuenta_esperada_del_canario():
    esperadas = {c.lower() for c in settings.APIFY_X_CUENTAS_ESPERADAS}
    assert "ttivalparaiso" in esperadas


# --- La entrega completa -----------------------------------------------------


class ServicioTransito(ServicioFalso):
    """Mismo falso que el de Bomberos, guardado aparte para no pisarse."""

    instancias: ClassVar[list[ServicioTransito]] = []

    def __init__(self, session) -> None:
        bomberos = ServicioFalso.ultimo
        super().__init__(session)
        # `ServicioFalso.ultimo` sigue apuntando a la corrida de Bomberos.
        ServicioFalso.ultimo = bomberos
        ServicioTransito.instancias.append(self)


@pytest.fixture
def entrega(monkeypatch):
    monkeypatch.setattr(svc, "AsyncSessionLocal", SesionFalsa)
    monkeypatch.setattr(svc, "IngestService", ServicioFalso)
    monkeypatch.setattr(tx, "AsyncSessionLocal", SesionFalsa)
    monkeypatch.setattr(tx, "IngestService", ServicioTransito)
    monkeypatch.setattr(RepoFalso, "conocidos", {})
    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 0)
    monkeypatch.setattr(settings, "BOMBEROS_ACCIDENT_KEYS", ["5-1", "12"])
    monkeypatch.setattr(settings, "BOMBEROS_MAX_LLM_CALLS", 0)
    monkeypatch.setattr(settings, "APIFY_TOKEN", "token-de-prueba")
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_SECRET", "")
    monkeypatch.setattr(settings, "APIFY_BOMBEROS_ACTOR_IDS", [])
    monkeypatch.setattr(settings, "APIFY_X_CUENTAS_ESPERADAS", [])
    monkeypatch.setattr(worker.gemini, "is_configured", lambda: False)
    ServicioTransito.instancias = []

    consultas: list[dict[str, Any]] = []

    async def geocode_falso(_client, streets):
        consultas.append(dict(streets))
        return GeocodeResult(lat=-33.0153, lon=-71.5500, query="falsa")

    monkeypatch.setattr(worker, "geocode", geocode_falso)
    return consultas


@respx.mock
def test_los_tuits_del_mtt_entran_como_transporte_informa_y_no_como_despacho(entrega):
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                despacho("81 * RUTA 68 KM 42 * CLAVE 5-1", id_="10"),
                tuit_tti(CHOQUE, id_="20"),
                tuit_tti(DESVIO, id_="21"),
                tuit_tti(SALUDO, id_="22"),
            ],
        )
    )

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    bomberos = ServicioFalso.ultimo
    assert bomberos is not None
    assert bomberos.source is EventSource.BOMBEROS
    assert len(bomberos.eventos) == 1, "sólo el despacho es de Bomberos"
    assert "sin tabla" not in (bomberos.error or ""), "TTI ya no es una cuenta sin tabla"
    assert "3 tuits de tránsito" in (bomberos.error or "")

    (transito,) = ServicioTransito.instancias
    assert transito.collector == "transporte_informa_x"
    assert transito.source is EventSource.TRANSPORTE_INFORMA
    assert transito.status is CollectorStatus.SUCCESS
    assert transito.fetched == 3

    por_tipo = {e.type: e for e in transito.eventos}
    assert set(por_tipo) == {EventType.ACCIDENT, EventType.ROAD_CLOSURE}
    choque = por_tipo[EventType.ACCIDENT]
    assert choque.source is EventSource.TRANSPORTE_INFORMA
    assert choque.external_id == "mtt:x:20"
    assert choque.confidence == pytest.approx(0.80)
    assert (choque.lat, choque.lon) == pytest.approx((-33.0153, -71.5500))
    assert choque.raw_data["_collector"] == "transporte_informa_x"
    assert choque.raw_data["url"] == "https://x.com/TTIValparaiso/status/20"
    assert por_tipo[EventType.ROAD_CLOSURE].external_id == "mtt:closure:x:21"
    assert entrega, "el choque se geocodificó con las calles extraídas"


@respx.mock
def test_una_entrega_sin_tuits_del_mtt_no_abre_corrida_de_transito(entrega):
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200, json=[despacho("22 * DIEGO COOK / GUACOLDA * CLAVE 12", id_="3")]
        )
    )
    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))
    assert ServicioTransito.instancias == []
    assert ServicioFalso.ultimo.status is CollectorStatus.SUCCESS


@respx.mock
def test_lo_ya_guardado_no_vuelve_a_pagar_modelo_ni_nominatim(entrega, monkeypatch):
    """El canario trae los dos últimos tuits cada día: el delta los reconoce."""
    guardado = type(
        "Punto",
        (),
        {
            "texto_md5": worker.texto_md5(tx.limpiar_texto(CHOQUE)),
            "lat": -33.0,
            "lon": -71.5,
            "raw_data": {
                "_extraction": {
                    "street_1": "Av. España",
                    "street_2": "Uno Norte",
                    "mode": "heuristic",
                },
                "_geocoding": {"lat": -33.0, "lon": -71.5},
            },
        },
    )()
    monkeypatch.setattr(RepoFalso, "conocidos", {"mtt:x:20": guardado})
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[tuit_tti(CHOQUE, id_="20")]))

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    assert entrega == [], "no se llamó a Nominatim"
    (transito,) = ServicioTransito.instancias
    (choque,) = transito.eventos
    assert (choque.lat, choque.lon) == pytest.approx((-33.0, -71.5))


@respx.mock
def test_un_fallo_de_transito_no_toca_la_corrida_de_bomberos(entrega, monkeypatch):
    async def revienta(*_a, **_k):
        raise RuntimeError("Gemini se cayó feo")

    monkeypatch.setattr(tx, "resolver_avisos", revienta)
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[despacho("81 * RUTA 68 KM 42 * CLAVE 5-1", id_="10"), tuit_tti(CHOQUE, id_="20")],
        )
    )

    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))

    assert ServicioFalso.ultimo.status is CollectorStatus.SUCCESS
    (transito,) = ServicioTransito.instancias
    assert transito.status is CollectorStatus.FAILED
    assert "Gemini se cayó feo" in (transito.error or "")


@respx.mock
def test_los_retuits_del_mtt_no_entran(entrega):
    rt = tuit_tti("RT @Carabineros: Accidente en Ruta 68 km 20", id_="30")
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[rt]))
    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))
    assert ServicioTransito.instancias == []
