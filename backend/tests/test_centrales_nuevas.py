"""Los Andes, Quilpué y Quillota: tablas de claves, formatos y cuarteles.

Lo que estos tests fijan, en orden de gravedad:

1. **En Los Andes el `0` del medio no se colapsa.** `10-0-4` es un incendio en
   sector de alto riesgo y `10-4` un rescate vehicular. Colapsarlos, como en la
   costa, mandaría incendios a la familia de tránsito con peso 1.00.
2. **La radio de Los Andes no es una emergencia.** Sus familias 0 a 9 son
   procedimiento: `6-3` es «material mayor en el lugar», no un rescate.
3. **Las tablas provisionales de Quilpué y Quillota** sólo ingieren las
   familias 1–6 y nombran sus internas (17-x, 15).
4. Los seguimientos «SALE … A …» producen la misma huella que su original.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.collectors import vocabulary as v
from app.collectors.cuarteles import cuartel_nombrado
from app.collectors.traffic import gemini
from app.collectors.traffic.bomberos_10_4_worker import Dispatch, geocode_dispatches
from app.core.config import settings
from app.models.enums import EventSource, EventType
from app.services import apify_webhook_service as svc
from app.services import collector_health
from app.services.source_links import unidades_for

# --- Los Andes ---------------------------------------------------------------

LA_ESTRUCTURAL = "10-0-1 (LLAMADO ESTRUCTURAL), MEMBRILLAR /CHACABUCO, QB-2,RB-3,BT-5"
LA_RESCATE = "10-4-1 (RESCATE VEHICULAR), AUTOPISTA LIBERTADORES , R-1,H-2"
LA_GAS = "10-6-1 (ESCAPE DE GAS), JARDINES DE LOS ANDES ALTURA 491, H-2"
LA_SALE = "SALE RB-3, A 10-6-1 EN JARDINES DE LOS ANDES ALTURA 491"
LA_PESADO = (
    "EMERGENCIA: 12:00 10-4-2 RESCATE VEHICULAR PESADO), ACCESO AUTOPISTA LOS "
    "LIBERTADORES A SANTIAGO / AUTOPISTA LOS LIBERTADORES, R-1 H-2 B-"
)


def test_en_los_andes_el_cero_del_medio_es_parte_de_la_clave():
    assert v.normalise_code("10-0-4") == (10, 4)
    assert v.normalise_code("10-0-4", colapsar_cero=False) == (10, 0, 4)
    assert v.dispatch_event_type("10-0-4 (INCENDIO), CALLE X", v.CBLA) is EventType.STRUCTURAL_FIRE
    assert v.dispatch_event_type(LA_RESCATE, v.CBLA) is EventType.ACCIDENT


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        (LA_ESTRUCTURAL, EventType.STRUCTURAL_FIRE),
        (LA_RESCATE, EventType.ACCIDENT),
        (LA_GAS, EventType.OTHER),
        ("10-2-4 (INTERFAZ), CERRO LA VIRGEN, B-1", EventType.WILDFIRE),
        ("10-3-10 (RESCATE DE PERSONAS), SAN VICENTE/SAN VICENTE, RH-3", EventType.RESCUE),
        ("10-8-2 (CAMILLAJE), HOSPITAL SAN JUAN DE DIOS, RH-1", EventType.RESCUE),
        ("10-16 (TUNEL), TUNEL CRISTO REDENTOR, B-1", EventType.STRUCTURAL_FIRE),
    ],
)
def test_los_andes_tipifica_la_familia_10(texto, tipo):
    assert v.dispatch_event_type(texto, v.CBLA) is tipo


def test_la_radio_y_los_servicios_de_los_andes_son_internos():
    for codigo in [(6, 3), (1, 1), (5, 1), (0, 10), (10, 9, 2), (10, 10), (10, 5, 5), (10, 8, 9)]:
        assert v.CBLA.es_interna(codigo), codigo
    for codigo in [(10, 0, 1), (10, 5, 1), (10, 8, 4), (10, 3, 10)]:
        assert not v.CBLA.es_interna(codigo), codigo


def test_matches_key_excluye_el_subtipo_interno_pero_no_la_clave_configurada():
    claves = settings.BOMBEROS_CBLA_KEYS
    def pasa(texto: str) -> str | None:
        return v.matches_key(texto, claves, colapsar_cero=False, excluir=v.CBLA.es_interna)

    assert pasa(LA_ESTRUCTURAL) == "10-0"
    assert pasa("10-5-1 (DERRAME), RUTA 60, B-1") == "10-5"
    assert pasa("10-5-5 (HIGIENIZACION), ESMERALDA 130, B-1") is None
    assert pasa("6-3 MATERIAL MAYOR EN EL LUGAR") is None
    # En la costa, la clave configurada tal cual manda sobre la exclusión.
    assert v.matches_key("22 * GUACOLDA * CLAVE 12", ["12"], excluir=v.CBV.es_interna) == "12"


@pytest.mark.parametrize(
    ("texto", "calles"),
    [
        (LA_ESTRUCTURAL, ("MEMBRILLAR", "CHACABUCO")),
        (LA_RESCATE, ("AUTOPISTA LIBERTADORES", None)),
        (LA_GAS, ("JARDINES DE LOS ANDES 491", None)),
        (LA_SALE, ("JARDINES DE LOS ANDES 491", None)),
        (LA_PESADO, ("ACCESO AUTOPISTA LOS LIBERTADORES A SANTIAGO", "AUTOPISTA LOS LIBERTADORES")),
    ],
)
def test_las_reglas_leen_el_formato_de_los_andes(texto, calles):
    salida = gemini.dispatch_summary_heuristic(texto, source_handle="@DESPACHOSCBLA", sistema=v.CBLA)
    assert salida is not None
    assert (salida["street_1"], salida["street_2"]) == calles
    assert salida["significado"] is not None


def test_el_prompt_de_los_andes_no_dice_que_el_cero_se_colapsa():
    prompt = gemini.dispatch_instruction(v.CBLA)
    assert "10-0-4 y 10-4 son claves distintas" in prompt
    assert "5-0-1 es 5-1" not in prompt
    assert "5-0-1 es 5-1" in gemini.dispatch_instruction(v.CBV)
    assert "provisional" in gemini.dispatch_instruction(v.CBQUILPUE)


# --- Quilpué y Quillota --------------------------------------------------------


def test_quilpue_y_quillota_tienen_su_cuenta_y_su_comuna():
    assert v.sistema_de_cuenta("@CBQuilpue") is v.CBQUILPUE
    assert v.sistema_de_cuenta("cbquillota") is v.CBQUILLOTA
    assert v.sistema_de_cuenta("@despachoscbla") is v.CBLA
    assert v.CBQUILPUE.provisional and v.CBQUILLOTA.provisional
    assert not v.CBLA.provisional


def test_las_internas_provisionales_no_se_ingieren():
    assert v.CBQUILPUE.es_interna((17, 4))
    assert v.CBQUILPUE.es_interna((17, 2))
    assert v.CBQUILLOTA.es_interna((15,))
    assert v.matches_key(
        "Clave 17-4 Copec Marga-Marga / M-13",
        settings.BOMBEROS_QUILPUE_KEYS,
        excluir=v.CBQUILPUE.es_interna,
    ) is None
    assert v.matches_key(
        "CLAVE 3 AVENIDA CONDELL M-21", settings.BOMBEROS_QUILLOTA_KEYS
    ) == "3"


def test_todas_las_centrales_tienen_claves_de_ingesta():
    for sistema in v.SISTEMAS_CLAVES:
        assert svc.claves_de_ingesta(sistema), sistema.slug
        # Cada clave configurada tiene tipo en su tabla: sin tipo, el despacho
        # entraría como `OTHER` y quedaría fuera de la familia que lo busca.
        tablas = {**sistema.code_types, **sistema.support_codes}
        for clave in svc.claves_de_ingesta(sistema):
            codigo = v.parse_key(clave, colapsar_cero=sistema.colapsa_cero)
            assert codigo is not None
            assert any(codigo[: len(t)] == t for t in tablas), (sistema.slug, clave)


def test_las_cuentas_esperadas_incluyen_las_cinco_centrales():
    esperadas = {c.lower() for c in settings.APIFY_X_CUENTAS_ESPERADAS}
    for sistema in v.SISTEMAS_CLAVES:
        assert set(sistema.cuentas) <= esperadas, sistema.slug


# --- Seguimientos ---------------------------------------------------------------


def test_el_seguimiento_y_su_original_tienen_la_misma_huella():
    assert gemini.es_seguimiento(LA_SALE)
    assert not gemini.es_seguimiento(LA_GAS)
    assert svc.huella_de_despacho(LA_SALE, v.CBLA) == svc.huella_de_despacho(LA_GAS, v.CBLA)

    original = "CLAVE 5-1 AVENIDA LAS ARAUCARIAS / ALMIRANTE LATORRE M-32"
    sale = "SALE M-34 A CLAVE 5-1 ALMIRANTE LATORRE / AVENIDA LAS ARAUCARIAS"
    assert svc.huella_de_despacho(original, v.CBQUILLOTA) == svc.huella_de_despacho(sale, v.CBQUILLOTA)


def test_las_unidades_se_leen_segun_el_formato():
    assert gemini.unidades_del_aviso(LA_ESTRUCTURAL, v.CBLA) == ["QB-2", "RB-3", "BT-5"]
    assert gemini.unidades_del_aviso("SALE M-34 A CLAVE 15 AV X", v.CBQUILLOTA) == ["M-34"]
    assert gemini.unidades_del_aviso("91, 31 * IQUIQUE / CAVANCHA * CLAVE 1-1", v.CBV) == ["91", "31"]
    assert gemini.unidades_del_aviso("Clave 5-1 RUTA F-30-E U-63", v.CBVM) == ["U-63"]


def test_la_ficha_recibe_las_unidades_solo_de_bomberos():
    datos = {"_bomberos": {"unidades": ["M-32", "M-34"]}}
    assert unidades_for(EventSource.BOMBEROS, datos) == ["M-32", "M-34"]
    assert unidades_for(EventSource.MEDIA, datos) == []
    assert unidades_for(EventSource.BOMBEROS, {}) == []


# --- Cuarteles ------------------------------------------------------------------


def test_un_destino_que_es_un_cuartel_se_ubica_en_su_cuerpo():
    quinta = cuartel_nombrado("Quinta Compañía de Bomberos Quilpue", v.CBQUILPUE)
    assert quinta is not None and quinta.nombre == "Quinta de Quilpué"
    comandancia = cuartel_nombrado("Comandancia Cuerpo de Bomberos Quilpué", v.CBQUILPUE)
    assert comandancia is not None and comandancia.tipo == "cuerpo"
    primera = cuartel_nombrado("PRIMERA COMPAÑIA", v.CBQUILLOTA)
    assert primera is not None and primera.cuerpo == "quillota"
    assert cuartel_nombrado("AVENIDA LIBERTAD / 5 NORTE", v.CBVM) is None


def test_el_cuartel_se_ubica_sin_gastar_nominatim(monkeypatch):
    async def nunca(*_a, **_k):
        raise AssertionError("no debía consultar Nominatim")

    monkeypatch.setattr("app.collectors.traffic.bomberos_10_4_worker.geocode", nunca)
    despacho = Dispatch(
        key="1-1",
        address="Clave 1-1 Dirección General Cuerpo de Bomberos Quilpué / M-21, M-11",
        occurred_at=None,
        commune=None,
        raw_text="Clave 1-1 Dirección General Cuerpo de Bomberos Quilpué / M-21, M-11",
        cuenta="@CBQuilpue",
        decoded={"street_1": "Dirección General Cuerpo de Bomberos Quilpué", "street_2": None},
    )
    salida, resueltos = asyncio.run(geocode_dispatches([despacho], max_geocodes=5))
    assert resueltos == 1
    assert salida[0].point is not None
    assert salida[0].point.osm_type == "sig_bomberos"


def test_la_instantanea_del_sig_esta_publicada_y_es_de_la_region():
    from app.collectors.cuarteles import CUARTELES_PATH

    datos = json.loads(CUARTELES_PATH.read_text(encoding="utf-8"))
    assert datos["type"] == "FeatureCollection"
    cuerpos = {f["properties"]["cuerpo"] for f in datos["features"]}
    for sistema in v.SISTEMAS_CLAVES:
        assert sistema.comunas[0] in cuerpos, sistema.slug
    for feature in datos["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        assert -34.0 < lat < -32.0 and -72.0 < lon < -69.9


def test_el_endpoint_sirve_los_cuarteles_con_etag():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as cliente:
        primera = cliente.get("/api/v1/events/cuarteles")
        assert primera.status_code == 200
        assert primera.json()["type"] == "FeatureCollection"
        etag = primera.headers["etag"]
        segunda = cliente.get("/api/v1/events/cuarteles", headers={"If-None-Match": etag})
        assert segunda.status_code == 304


# --- Salud del canario -------------------------------------------------------------


def test_el_canario_solo_cuenta_cuando_esta_configurado(monkeypatch):
    monkeypatch.setattr(settings, "APIFY_X_CANARIO_IDS", [])
    assert "bomberos_apify_canario" not in collector_health.active_roles()
    monkeypatch.setattr(settings, "APIFY_X_CANARIO_IDS", ["canarioTask0001"])
    assert "bomberos_apify_canario" in collector_health.active_roles()
    monkeypatch.setattr(settings, "APIFY_X_CANARIO_HORAS", 24)
    assert collector_health._intervalo("bomberos_apify_canario") == 24 * 3600
