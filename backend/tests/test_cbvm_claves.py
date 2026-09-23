"""@CBVM132: la central de Viña del Mar, con SU diccionario de claves.

Por qué un archivo aparte
-------------------------
Hasta el 2026-09-22 el backend leía una sola central (@CGI_CBV) y las tablas de
claves podían ser globales. Con dos centrales en el mismo Task de X, una tabla
global garantiza que una de las dos se lea mal: en Valparaíso `Clave 10` es
abastecer agua y en Viña es «otros servicios»; `Clave 3` es un incendio
vehicular en Viña y no existe en Valparaíso.

Estos tests fijan tres cosas:

1. **El diccionario del CBVM** es el publicado y es coherente con lo que se
   ingiere (mismas invariantes que el del CBV).
2. **El formato de la cuenta** —la clave abre el aviso, las unidades lo
   cierran— se decodifica bien SIN modelo, con los tuits reales.
3. **El webhook separa los dos Cuerpos** dentro de un mismo dataset.

Los textos de los tuits son reales, copiados de @CBVM132 (septiembre de 2026 y
anteriores). Las URLs `t.co` van tal cual porque la cuenta las publica.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.collectors import vocabulary
from app.collectors.traffic import gemini
from app.collectors.traffic.bomberos_10_4_worker import (
    Dispatch,
    comuna_de_handle,
    comunas_alternativas,
    dispatch_type,
    dispatches_to_events,
)
from app.core.config import settings
from app.models.enums import CollectorStatus, EventSource, EventType
from app.services import apify_webhook_service as svc
from app.services.source_links import source_label_for, source_url_for

CBVM = vocabulary.CBVM
CBV = vocabulary.CBV

DATASET_ID = "dsCentrales"
ITEMS_URL = f"https://api.apify.com/v2/datasets/{DATASET_ID}/items"
AHORA = datetime.now(UTC)


# =============================================================================
#  1. El diccionario
# =============================================================================


@pytest.mark.parametrize(
    ("texto", "clave", "significado", "tipo"),
    [
        ("Clave 1-1 ALGARROBAL / HERIBERTO ROJAS", "1-1", "Incendio estructural simple",
         EventType.STRUCTURAL_FIRE),
        ("Clave 2-3 CAMINO INTERNACIONAL", "2-3", "Incendio forestal cercano a vivienda",
         EventType.WILDFIRE),
        ("Clave 3 AVENIDA LIBERTAD", "Clave 3", "Incendio vehicular",
         EventType.STRUCTURAL_FIRE),
        ("Clave 5-1 LOS PELLINES / LOS GINKOS", "5-1", "Rescate vehicular simple",
         EventType.ACCIDENT),
        ("Clave 5-2 TRONCAL SUR", "5-2", "Rescate vehicular pesado", EventType.ACCIDENT),
        ("Clave 6-3 EDIFICIO", "6-3", "Rescate de persona en ascensor, casa o edificio",
         EventType.RESCUE),
        ("Clave 9 AVENIDA LIMACHE", "Clave 9", "Emergencia estructural industrial",
         EventType.STRUCTURAL_FIRE),
        ("Clave 10 ALVAREZ", "Clave 10", "Otros servicios", EventType.OTHER),
    ],
)
def test_el_diccionario_del_cbvm_decodifica_sus_claves(texto, clave, significado, tipo):
    assert vocabulary.resolve_clave(texto, CBVM) == (clave, significado)
    assert vocabulary.dispatch_event_type(texto, CBVM) is tipo


@pytest.mark.parametrize(
    ("texto", "cbv", "cbvm"),
    [
        # La tabla comparativa de `vocabulary`, clave por clave.
        ("Clave 10", "Abastecer o aspirar agua", "Otros servicios"),
        ("Clave 11", "Guardia preventiva", "Rebrote de incendio"),
        ("Clave 14", "Rebrote de incendio", "Llamado por accidente eléctrico"),
        ("Clave 15", "Otros servicios", "Llamado por accidente aéreo"),
        ("Clave 4-1", "Emergencia con materiales peligrosos", "Emergencia con emanación de gases"),
    ],
)
def test_la_misma_clave_significa_otra_cosa_en_cada_cuerpo(texto, cbv, cbvm):
    """La razón de tener dos tablas y no una."""
    assert vocabulary.resolve_clave(texto, CBV)[1] == cbv
    assert vocabulary.resolve_clave(texto, CBVM)[1] == cbvm


def test_la_clave_3_es_un_incendio_en_vina_y_nada_en_valparaiso():
    """El caso más caro del error: leído con la tabla del CBV, cada incendio
    vehicular de Viña entraba como «otros» y fuera de la familia `fire`."""
    assert vocabulary.dispatch_event_type("Clave 3 AV LIBERTAD", CBVM) is EventType.STRUCTURAL_FIRE
    assert vocabulary.dispatch_event_type("Clave 3 AV LIBERTAD", CBV) is EventType.OTHER


def test_sin_sistema_rige_el_del_cbv_como_siempre():
    """Todo lo que ya llamaba a estas funciones sin Cuerpo sigue igual."""
    assert vocabulary.resolve_clave("CLAVE 12") == ("Clave 12", "Academia de Cuerpo")
    assert vocabulary.clave_meaning((10,)) == "Abastecer o aspirar agua"


def test_toda_clave_ingerida_del_cbvm_tiene_significado_y_tipo():
    """La misma invariante que `test_toda_clave_ingerida_tiene_significado_y_tipo`
    del CBV. Una clave configurada sin tipo entra como `OTHER` y sale de la
    familia donde el mapa la busca."""
    for key in settings.BOMBEROS_CBVM_KEYS:
        code = vocabulary.parse_key(key)
        assert code is not None, f"{key} no es una clave parseable"
        assert code in CBVM.meanings, f"{key} se ingiere y no tiene significado"
        assert code in CBVM.code_types, f"{key} se ingiere y no tiene tipo"


def test_ninguna_clave_interna_del_cbvm_esta_configurada():
    configuradas = {vocabulary.parse_key(k) for k in settings.BOMBEROS_CBVM_KEYS}
    assert not (configuradas & CBVM.non_incident)
    assert (16,) in CBVM.non_incident, "servicios internos: la clave más frecuente"


def test_la_14_queda_sin_decidir_a_proposito():
    """Las dos versiones publicadas de la tabla no coinciden (accidente
    eléctrico / ejercicio de unidad). No se ingiere NI se declara interna, para
    que el webhook la avise como «clave no configurada» si aparece."""
    assert (14,) in CBVM.meanings
    assert (14,) not in CBVM.code_types
    assert not CBVM.es_interna((14,))
    assert "14" not in settings.BOMBEROS_CBVM_KEYS


def test_cada_cuenta_tiene_su_sistema():
    assert vocabulary.sistema_de_cuenta("@CBVM132") is CBVM
    assert vocabulary.sistema_de_cuenta("cbvm132") is CBVM
    assert vocabulary.sistema_de_cuenta("@CGI_CBV") is CBV
    assert vocabulary.sistema_de_cuenta("@VecinoCualquiera") is None
    assert vocabulary.sistema_de_cuenta(None) is None


def test_la_jurisdiccion_del_cbvm_incluye_concon():
    assert comuna_de_handle("@CBVM132") == "Viña del Mar"
    assert comunas_alternativas("@CBVM132") == ("Concón",)
    assert comunas_alternativas("@CGI_CBV") == ()


# =============================================================================
#  2. El formato «clave primero», sin modelo
# =============================================================================


@pytest.mark.parametrize(
    ("tuit", "clave", "calle_1", "calle_2", "comuna"),
    [
        ("Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82 https://t.co/jDQZmiT0Te",
         "5-1", "LOS PELLINES", "LOS GINKOS", None),
        ("Clave 1-1 ALGARROBAL / HERIBERTO ROJAS U-91, U-51, U-31 https://t.co/qoMVlJsboN",
         "1-1", "ALGARROBAL", "HERIBERTO ROJAS", None),
        ("Clave 5-1 AVENIDA PADRE ALBERTO HURTADO / PASAJE PADRE HURTADO U-33, U-102",
         "5-1", "AVENIDA PADRE ALBERTO HURTADO", "PASAJE PADRE HURTADO", None),
        # El significado pegado a la clave no es una calle.
        ("Clave 5-1 RESCATE VEHICULAR LIVIANO CONDOMINIO MILLED 2 / . U-33, U-92 "
         "https://t.co/PSMWp8Xfl0",
         "5-1", "CONDOMINIO MILLED 2", None, None),
        # Formato viejo: unidades antes de la dirección y la comuna al final.
        ("CLAVE 1-1 U-22 U-41 U-12 1 norte con 5 oriente , sector Centro Viña del Mar "
         "info -> https://t.co/LKenhGz9mW",
         "1-1", "1 norte", "5 oriente", "Viña del Mar"),
        ("Clave 16 PEDRO MONTT / FREIRE U-12", "Clave 16", "PEDRO MONTT", "FREIRE", None),
    ],
)
def test_la_heuristica_lee_los_tuits_reales_de_la_central(tuit, clave, calle_1, calle_2, comuna):
    decoded = gemini.dispatch_summary_heuristic(tuit, source_handle="@CBVM132", sistema=CBVM)

    assert decoded is not None
    assert decoded["clave"] == clave
    assert decoded["street_1"] == calle_1
    assert decoded["street_2"] == calle_2
    assert decoded["city"] == comuna


def test_sin_reordenar_las_calles_salian_con_la_clave_y_las_unidades_dentro():
    """El fallo que motivó `reordenar_clave_primero`, leído con el formato del CBV."""
    tuit = "Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82 https://t.co/jDQZmiT0Te"

    como_cbv = gemini.dispatch_summary_heuristic(tuit, source_handle="@CBVM132", sistema=CBV)
    assert como_cbv is not None
    assert "Clave" in (como_cbv["street_1"] or "")

    como_cbvm = gemini.dispatch_summary_heuristic(tuit, source_handle="@CBVM132", sistema=CBVM)
    assert como_cbvm["street_1"] == "LOS PELLINES"


def test_el_resumen_cita_a_la_central_de_vina():
    decoded = gemini.dispatch_summary_heuristic(
        "Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82",
        source_handle="@CBVM132",
        sistema=CBVM,
    )
    assert decoded["resumen"] == (
        "(5-1) (Rescate vehicular simple) en (Los Pellines con Los Ginkos) (Fuente: @CBVM132)"
    )


def test_una_ruta_con_guion_no_se_confunde_con_una_unidad():
    """«U-63» es un carro; «F-30-E» es el camino de Concón."""
    texto, comuna = gemini.reordenar_clave_primero(
        "Clave 3 RUTA F-30-E / LOS CANELOS, CONCON U-61", CBVM
    )
    assert texto == "RUTA F-30-E / LOS CANELOS * Clave 3"
    assert comuna == "Concón"


def test_sale_la_unidad_no_es_parte_de_la_direccion():
    texto, _ = gemini.reordenar_clave_primero(
        "SALE T-1 A Clave 16 3ra Compañía CBVM - Calle Limache #3001 /", CBVM
    )
    assert texto.endswith("* Clave 16")
    assert not texto.startswith("SALE")


def test_el_prompt_del_cbvm_lleva_su_diccionario_y_no_el_del_cbv():
    prompt = gemini.dispatch_instruction(CBVM)

    assert "Clave 10: Otros servicios" in prompt
    assert "Clave 16: Servicios internos [NO es una emergencia" in prompt
    assert "Abastecer o aspirar agua" not in prompt
    assert "U-63" in prompt, "las unidades del formato tienen que estar explicadas"
    assert "(Fuente: @CBVM132)" in prompt


def test_el_prompt_del_cbv_no_cambio_de_cuerpo():
    assert gemini.dispatch_instruction(CBV) == gemini.DISPATCH_SYSTEM_INSTRUCTION
    assert "Clave 10: Abastecer o aspirar agua" in gemini.DISPATCH_SYSTEM_INSTRUCTION


# =============================================================================
#  3. Del despacho al evento
# =============================================================================


def _despacho_cbvm(texto: str, key: str) -> Dispatch:
    decoded = gemini.dispatch_summary_heuristic(texto, source_handle="@CBVM132", sistema=CBVM)
    return Dispatch(
        key=key,
        address=texto,
        occurred_at=None,
        commune=None,
        raw_text=texto,
        guid="x:2102550828552692159",
        decoded=decoded,
        cuenta="@CBVM132",
        url="https://x.com/CBVM132/status/2102550828552692159",
    )


def test_el_tipo_del_evento_sale_del_diccionario_de_su_cuerpo():
    despacho = _despacho_cbvm("Clave 3 AVENIDA LIBERTAD U-12", "3")
    assert dispatch_type(despacho) is EventType.STRUCTURAL_FIRE


def test_el_evento_dice_quien_despacho_y_con_que_diccionario():
    despacho = _despacho_cbvm("Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82", "5-1")
    eventos, _ = dispatches_to_events([despacho], collector="test")

    bomberos = eventos[0].raw_data["_bomberos"]
    assert bomberos["cuenta"] == "@CBVM132"
    assert bomberos["cuerpo"] == "cbvm"
    assert eventos[0].type is EventType.ACCIDENT
    assert eventos[0].confidence == 1.0
    assert "(Fuente: @CBVM132)" in eventos[0].text


def test_el_panel_nombra_a_la_central_y_enlaza_el_tuit():
    despacho = _despacho_cbvm("Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82", "5-1")
    eventos, _ = dispatches_to_events([despacho], collector="test")
    raw = eventos[0].raw_data

    assert source_label_for(EventSource.BOMBEROS, raw) == "@CBVM132"
    assert source_url_for(EventSource.BOMBEROS, raw) == (
        "https://x.com/CBVM132/status/2102550828552692159"
    )


# =============================================================================
#  4. El webhook con las dos centrales en el mismo dataset
# =============================================================================


def tuit(texto: str, cuenta: str, *, id_: str, minutos: int = 5, **extra) -> dict:
    """Un item con la forma de Tweet Scraper V2: `author.userName` y `url`."""
    momento = AHORA - timedelta(minutes=minutos)
    return {
        "type": "tweet",
        "id": id_,
        "url": f"https://x.com/{cuenta}/status/{id_}",
        "text": texto,
        "createdAt": momento.isoformat(),
        "author": {"userName": cuenta, "name": "Central"},
        **extra,
    }


def payload_apify() -> dict:
    return {
        "eventType": "ACTOR.RUN.SUCCEEDED",
        "eventData": {"actorId": "actorX"},
        "resource": {"id": "run1", "actId": "actorX", "defaultDatasetId": DATASET_ID},
    }


class _Sesion:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None


class _Servicio:
    ultimo: _Servicio | None = None

    def __init__(self, _session) -> None:
        self.eventos: list = []
        self.status = None
        self.error = None
        _Servicio.ultimo = self

    async def start_run(self, *, source, collector, params):
        self.params = params
        return object()

    async def finish_run(self, _run, *, status, fetched=0, inserted=0, duplicate=0, error=None):
        self.status = status
        self.error = error

    async def ingest_batch(self, events):
        self.eventos = list(events)
        return type("R", (), {"inserted": len(events), "duplicated": 0})()


@pytest.fixture
def servicio(monkeypatch):
    monkeypatch.setattr(svc, "AsyncSessionLocal", _Sesion)
    monkeypatch.setattr(svc, "IngestService", _Servicio)
    monkeypatch.setattr(settings, "APIFY_TOKEN", "token-de-prueba")
    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 0)
    monkeypatch.setattr(settings, "BOMBEROS_MAX_LLM_CALLS", 0)
    monkeypatch.setattr(settings, "APIFY_WEBHOOK_MAX_AGE_MINUTES", 180)
    monkeypatch.setattr(settings, "BOMBEROS_SOURCE_HANDLE", "@CGI_CBV")
    return _Servicio


def _correr(items: list[dict]) -> _Servicio:
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=items))
    asyncio.run(svc.process_dataset(DATASET_ID, payload_apify()))
    hecho = _Servicio.ultimo
    assert hecho is not None
    return hecho


@respx.mock
def test_cada_tuit_se_lee_con_el_diccionario_de_su_central(servicio):
    hecho = _correr(
        [
            tuit("72 * PRIMERO DE MAYO / 12 DE OCTUBRE * CLAVE 4-1", "CGI_CBV", id_="1"),
            tuit("Clave 3 AVENIDA LIBERTAD U-12", "CBVM132", id_="2"),
            # `Clave 10`: en Viña es un servicio y se ingiere...
            tuit("Clave 10 ALVAREZ / QUILLOTA U-12", "CBVM132", id_="3"),
            # ...y en Valparaíso es abastecer agua y no.
            tuit("81 * AVENIDA ALEMANIA * CLAVE 10", "CGI_CBV", id_="4"),
        ]
    )

    por_id = {e.external_id: e for e in hecho.eventos}
    assert len(hecho.eventos) == 3
    cuentas = sorted(e.raw_data["_bomberos"]["cuenta"] for e in hecho.eventos)
    assert cuentas == ["@CBVM132", "@CBVM132", "@CGI_CBV"]

    tipos = {e.raw_data["_bomberos"]["aviso"]: e.type for e in por_id.values()}
    assert tipos["Clave 3 AVENIDA LIBERTAD U-12"] is EventType.STRUCTURAL_FIRE
    assert tipos["Clave 10 ALVAREZ / QUILLOTA U-12"] is EventType.OTHER
    assert hecho.status is CollectorStatus.SUCCESS


@respx.mock
def test_los_servicios_internos_no_ensucian_la_corrida(servicio):
    """La 16 es la clave más frecuente de @CBVM132: carros entre cuarteles.

    Tiene nombre y se descarta a propósito. Contarla como «clave no
    configurada» dejaría cada entrega en `partial` y el aviso dejaría de
    significar algo.
    """
    hecho = _correr(
        [
            tuit("Clave 16 PEDRO MONTT / FREIRE U-12", "CBVM132", id_="1"),
            tuit("SALE T-1 A Clave 16 3ra Compañía CBVM - Calle Limache #3001 /", "CBVM132",
                 id_="2"),
            tuit("Clave 11 QUILPUE / VIANA U-42", "CBVM132", id_="3"),
        ]
    )

    assert hecho.eventos == []
    assert hecho.status is CollectorStatus.SUCCESS
    assert not hecho.error


@respx.mock
def test_una_clave_del_cbvm_sin_decidir_se_avisa_con_su_cuerpo(servicio):
    hecho = _correr([tuit("Clave 14 AVENIDA VALPARAISO U-12", "CBVM132", id_="1")])

    assert hecho.eventos == []
    assert hecho.status is CollectorStatus.PARTIAL
    assert "CBVM Clave 14" in (hecho.error or "")


@respx.mock
def test_una_cuenta_sin_tabla_no_se_lee_con_la_de_otro_cuerpo(servicio):
    """Un `twitterHandles` mal puesto en el Task no puede meter a nadie con 1.00."""
    hecho = _correr([tuit("Clave 5-1 AVENIDA SIEMPRE VIVA", "VecinoAlerta", id_="1")])

    assert hecho.eventos == []
    assert "@VecinoAlerta" in (hecho.error or "")
    assert hecho.status is CollectorStatus.SUCCESS, "se anota, no se alarma"


@respx.mock
def test_un_retuit_no_es_un_despacho_propio(servicio):
    hecho = _correr(
        [
            tuit("RT @bomberosvina: Clave 5-1 AVENIDA LIBERTAD", "CBVM132", id_="1"),
            tuit("Clave 5-1 LOS PELLINES / LOS GINKOS", "CBVM132", id_="2", isRetweet=True),
        ]
    )

    assert hecho.eventos == []
    assert "retuits" in (hecho.error or "")


@respx.mock
def test_un_tuit_sin_autor_sigue_leyendose_como_antes(servicio):
    """Actors que no informan el autor: rige `BOMBEROS_SOURCE_HANDLE`."""
    item = {
        "id": "9",
        "full_text": "81 * RUTA 68 KM 42 * CLAVE 5-1",
        "createdAt": AHORA.isoformat(),
    }
    hecho = _correr([item])

    assert len(hecho.eventos) == 1
    assert hecho.eventos[0].raw_data["_bomberos"]["cuenta"] == "@CGI_CBV"
    assert hecho.eventos[0].raw_data["_bomberos"]["cuerpo"] == "cbv"


def test_el_autor_se_lee_del_objeto_o_de_la_url():
    assert svc.tweet_handle({"author": {"userName": "CBVM132"}}) == "@CBVM132"
    assert svc.tweet_handle({"user": {"screen_name": "CGI_CBV"}}) == "@CGI_CBV"
    assert svc.tweet_handle({"url": "https://x.com/CBVM132/status/1"}) == "@CBVM132"
    assert svc.tweet_handle({"twitterUrl": "https://twitter.com/CGI_CBV/status/2"}) == "@CGI_CBV"
    assert svc.tweet_handle({"url": "https://example.com/CBVM132/status/1"}) is None
    assert svc.tweet_handle({"id": "1"}) is None


# =============================================================================
#  5. Geocodificación: Concón también es jurisdicción del CBVM
# =============================================================================


@respx.mock
def test_una_esquina_de_concon_se_recupera_con_el_reintento(servicio, monkeypatch):
    """La central casi nunca escribe la comuna. Con la guarda puesta en Viña,
    un resultado de Concón se descarta; el reintento con Concón lo recupera."""
    from app.collectors import nominatim

    monkeypatch.setattr(settings, "BOMBEROS_MAX_GEOCODES", 10)
    monkeypatch.setattr(nominatim.settings, "NOMINATIM_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(nominatim, "_LIMITER", nominatim.RateLimiter(0.0))

    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "-32.93",
                    "lon": "-71.52",
                    "display_name": "Los Pellines, Concón",
                    "osm_type": "way",
                    "importance": 0.3,
                    "address": {"road": "Los Pellines", "city": "Concón"},
                }
            ],
        )
    )

    hecho = _correr([tuit("Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82", "CBVM132", id_="1")])

    evento = hecho.eventos[0]
    assert evento.lat == pytest.approx(-32.93)
    assert evento.lon == pytest.approx(-71.52)
