"""Capa de cortes eléctricos: parseo tolerante, metadata y aislamiento.

Estos tests cargan más peso del habitual porque **el esquema real no se pudo
verificar**: `mapainterrupciones.chilquinta.cl` es un visor de mapa renderizado
en el navegador y no expone su ruta de datos públicamente, y CGE todavía no
tiene URL. Todo lo que aquí se prueba son las formas *plausibles* de ese JSON,
no las confirmadas.

Eso cambia para qué sirven: no garantizan que el collector funcione contra el
endpoint real —eso sólo lo dirá el primer despliegue— sino que **falle de forma
legible** cuando no coincida, y que ningún campo ausente lo tumbe.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.power.base_worker import (
    OUTAGE_KEY,
    POWER_OUTAGE_CONFIDENCE,
    build_text,
)
from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.chilquinta_worker import ChilquintaCollector
from app.collectors.power.outage_parser import (
    PowerOutage,
    build_external_id,
    describe_shape,
    extract_records,
    parse_outage,
)
from app.core.config import settings
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EVENT_TO_INCIDENT_TYPE,
    EventSource,
    EventType,
    IncidentType,
    family_of_event,
)

#: Viña del Mar, dentro de la región.
VINA = (-33.0245, -71.5518)

#: Llave ficticia. La real vive en el `.env`, que está en .gitignore: si la
#: suite dependiera de ella, pasaría en la máquina de quien la configuró y
#: fallaría en CI por un motivo que no tiene nada que ver con el código.
API_KEY_FALSA = "clave-de-prueba-no-es-la-real"


@pytest.fixture(autouse=True)
def api_key_configurada(monkeypatch):
    """Chilquinta exige `x-api-key` y sin ella falla al construirse.

    Se inyecta una ficticia para toda la suite. El test que comprueba el fallo
    por llave ausente la vuelve a vaciar por su cuenta.
    """
    monkeypatch.setattr(settings, "CHILQUINTA_API_KEY", API_KEY_FALSA)


def collector(clase=ChilquintaCollector):
    """Instancia sin `__init__`: no toca sesión ni base de datos."""
    instancia = clase.__new__(clase)
    instancia.bbox = settings.region_bbox
    instancia.url = ""
    return instancia


def registro(**overrides) -> dict:
    base = {
        "id": "CQ-88231",
        "latitud": VINA[0],
        "longitud": VINA[1],
        "clientes_afectados": 842,
        "hora_reposicion": "2026-08-20 18:30:00",
        "hora_inicio": "2026-08-20 14:05:00",
        "comuna": "Viña del Mar",
        "sector": "Recreo Alto",
    }
    base.update(overrides)
    return base


# --- Formas del sobre --------------------------------------------------------


@pytest.mark.parametrize(
    "sobre",
    [
        [registro()],
        {"data": [registro()]},
        {"items": [registro()]},
        {"interrupciones": [registro()]},
        {"cortes": [registro()]},
        {"features": [registro()]},
        {"resultado": {"data": [registro()]}},  # envoltorio de un nivel más
    ],
)
def test_encuentra_la_lista_en_las_formas_plausibles(sobre):
    """No se sabe cómo envuelve la lista cada empresa; se prueban las candidatas."""
    registros = extract_records(sobre)
    assert registros is not None
    assert len(registros) == 1


def test_una_lista_vacia_no_es_lo_mismo_que_no_encontrar_la_lista():
    """La distinción que decide entre `success` y `failed`.

    Sin cortes ahora es una noche tranquila. No encontrar la lista significa que
    el esquema no es el esperado, y eso necesita a una persona.
    """
    assert extract_records({"data": []}) == []
    assert extract_records({"algo": "inesperado"}) is None
    assert extract_records("texto suelto") is None


def test_describe_shape_dice_que_llego():
    """El mensaje que hará depurable el primer despliegue contra el endpoint real."""
    assert "claves" in describe_shape({"total": 3, "payload": []})
    assert "lista de 1" in describe_shape([registro()])
    assert "vacía" in describe_shape([])


# --- Alias de campos ---------------------------------------------------------


@pytest.mark.parametrize(
    ("campos", "esperado_lat"),
    [
        ({"latitud": VINA[0], "longitud": VINA[1]}, VINA[0]),
        ({"lat": VINA[0], "lon": VINA[1]}, VINA[0]),
        ({"lat": VINA[0], "lng": VINA[1]}, VINA[0]),
        ({"latitude": VINA[0], "longitude": VINA[1]}, VINA[0]),
        ({"y": VINA[0], "x": VINA[1]}, VINA[0]),
        # Las empresas alternan mayúsculas y guiones sin criterio.
        ({"Latitud": VINA[0], "LONGITUD": VINA[1]}, VINA[0]),
    ],
)
def test_reconoce_los_alias_de_coordenadas(campos, esperado_lat):
    corte = parse_outage(campos)
    assert corte is not None
    assert corte.lat == pytest.approx(esperado_lat)


@pytest.mark.parametrize(
    "clave",
    ["clientes_afectados", "clientesAfectados", "clientes", "afectados", "customers"],
)
def test_reconoce_los_alias_de_clientes_afectados(clave):
    corte = parse_outage({"latitud": VINA[0], "longitud": VINA[1], clave: 842})
    assert corte.affected_clients == 842


@pytest.mark.parametrize(
    "clave",
    ["hora_reposicion", "horaReposicion", "fecha_reposicion", "estimated_restoration"],
)
def test_reconoce_los_alias_de_hora_de_reposicion(clave):
    corte = parse_outage(
        {"latitud": VINA[0], "longitud": VINA[1], clave: "2026-08-20 18:30:00"}
    )
    assert corte.restoration_at is not None


def test_lee_geojson_con_el_orden_de_la_rfc():
    """En GeoJSON el orden es [lon, lat]. Invertirlo manda todo al Índico.

    Y lo hace con coordenadas válidas, así que sin ningún error visible: es el
    modo de fallo silencioso clásico de estos parseos.
    """
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [VINA[1], VINA[0]]},
        "properties": {"clientes_afectados": 120, "comuna": "Viña del Mar"},
    }
    corte = parse_outage(feature)

    assert corte.lat == pytest.approx(VINA[0])
    assert corte.lon == pytest.approx(VINA[1])
    assert corte.affected_clients == 120
    # Si se invirtieran, la latitud caería fuera de Chile.
    assert -60.0 < corte.lat < -15.0


# --- Campos ausentes: la lectura segura que se pidió -------------------------


def test_un_corte_sin_clientes_afectados_no_revienta():
    """La empresa no siempre publica el conteo, y `None` no es cero.

    Al comienzo de un corte nadie sabe todavía a cuántos afecta. Poner 0 sería
    afirmar que no afecta a nadie.
    """
    corte = parse_outage({"latitud": VINA[0], "longitud": VINA[1]})
    assert corte is not None
    assert corte.affected_clients is None


def test_un_corte_sin_hora_de_reposicion_no_revienta():
    corte = parse_outage(registro(hora_reposicion=None))
    assert corte is not None
    assert corte.restoration_at is None


@pytest.mark.parametrize(
    "campos",
    [
        {},
        {"latitud": VINA[0]},  # falta longitud
        {"clientes_afectados": 100},  # sin coordenadas
        {"latitud": None, "longitud": None},
        {"latitud": "no es un número", "longitud": VINA[1]},
        {"latitud": 40.712, "longitud": -74.006},  # Nueva York: campo mal leído
        "no soy un objeto",
        None,
    ],
)
def test_un_registro_inservible_devuelve_none_sin_lanzar(campos):
    """La única ausencia fatal es la coordenada: sin ella no hay nada que pintar."""
    assert parse_outage(campos) is None


def test_los_valores_vacios_se_tratan_como_ausentes():
    """Las empresas escriben el vacío de varias formas y todas significan lo mismo."""
    corte = parse_outage(
        registro(clientes_afectados="", hora_reposicion="null", sector="   ")
    )
    assert corte.affected_clients is None
    assert corte.restoration_at is None
    assert corte.sector is None


def test_un_conteo_negativo_se_descarta():
    """Sólo puede ser un error de la fuente."""
    corte = parse_outage(registro(clientes_afectados=-5))
    assert corte.affected_clients is None


# --- Identidad e idempotencia ------------------------------------------------


def test_usa_el_identificador_de_la_empresa_cuando_existe():
    corte = parse_outage(registro())
    assert build_external_id("chilquinta", corte) == "chilquinta:CQ-88231"


def test_el_id_no_cambia_cuando_la_empresa_corrige_el_corte():
    """El caso que llenaría el mapa de duplicados durante el evento.

    Durante un corte largo la empresa corrige varias veces los clientes
    afectados y la hora estimada de reposición. Si esos campos entraran al hash,
    cada refresco crearía un corte nuevo — justo cuando más importa que el mapa
    esté limpio.
    """
    sin_id = {k: v for k, v in registro().items() if k != "id"}
    inicial = parse_outage(sin_id)
    corregido = parse_outage(
        {**sin_id, "clientes_afectados": 1210, "hora_reposicion": "2026-08-20 21:00:00"}
    )

    assert build_external_id("chilquinta", inicial) == build_external_id(
        "chilquinta", corregido
    )


def test_las_dos_empresas_no_comparten_espacio_de_ids():
    corte = parse_outage(registro())
    assert build_external_id("chilquinta", corte) != build_external_id("cge", corte)


# --- Mapeo al dominio --------------------------------------------------------


def test_emite_power_outage_con_confianza_uno():
    eventos = collector().normalize([parse_outage(registro())])

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.type is EventType.POWER_OUTAGE
    assert evento.source is EventSource.CHILQUINTA
    assert evento.confidence == pytest.approx(POWER_OUTAGE_CONFIDENCE) == 1.0


def test_la_metadata_del_corte_lleva_los_tres_campos_pedidos():
    evento = collector().normalize([parse_outage(registro())])[0]
    detalle = evento.raw_data[OUTAGE_KEY]

    assert detalle["company"] == "chilquinta"
    assert detalle["affected_clients"] == 842
    assert detalle["restoration_at"].startswith("2026-08-20T22:30")  # 18:30 Chile → UTC


def test_los_campos_del_mapa_van_tambien_planos():
    """El cliente no debería bajar a una clave con guion bajo.

    Mismo criterio que con magnitud y profundidad en la capa sísmica: lo que el
    mapa consume es contrato, y `_outage` es estructura interna nuestra.
    """
    evento = collector().normalize([parse_outage(registro())])[0]

    assert evento.raw_data["affected_clients"] == 842
    assert evento.raw_data["company"] == "chilquinta"
    assert evento.raw_data["restoration_at"] is not None


def test_la_hora_de_reposicion_se_convierte_desde_hora_chilena():
    """Las distribuidoras publican para el público local, no en UTC.

    Asumir UTC desplazaría cada corte cuatro horas: una reposición ya ocurrida
    figuraría como pendiente, o al revés.
    """
    corte = parse_outage(registro(hora_reposicion="2026-08-20 18:30:00"))
    # Agosto es invierno austral: UTC-4.
    assert corte.restoration_at == datetime(2026, 8, 20, 22, 30, tzinfo=UTC)


def test_la_comuna_va_donde_el_motor_la_busca():
    evento = collector().normalize([parse_outage(registro())])[0]
    assert evento.raw_data["comuna"] == "Viña del Mar"


def test_el_registro_original_se_conserva():
    """Para poder reprocesar sin volver a consultar cuando se entienda el esquema."""
    evento = collector().normalize([parse_outage(registro())])[0]
    assert evento.raw_data["_source_record"]["id"] == "CQ-88231"


def test_el_texto_es_legible():
    texto = build_text("chilquinta", parse_outage(registro()))

    assert "CHILQUINTA" in texto
    assert "842" in texto
    assert "Recreo Alto" in texto
    assert "reposición estimada" in texto


def test_el_texto_no_inventa_lo_que_la_fuente_no_dijo():
    corte = parse_outage(
        {"latitud": VINA[0], "longitud": VINA[1], "comuna": "Quilpué"}
    )
    texto = build_text("cge", corte)

    assert "CGE" in texto
    assert "cliente" in texto, "sin conteo no se menciona"
    assert "reposición" in texto, "sin ETA no se menciona"


def test_el_recorte_regional_descarta_lo_de_fuera():
    lejano = PowerOutage(
        outage_id="X", lat=-53.15, lon=-70.91,  # Punta Arenas
        affected_clients=10, restoration_at=None, started_at=None,
        commune="Magallanes", sector=None, raw={},
    )
    assert collector().normalize([lejano]) == []


def test_cge_emite_con_su_propia_fuente():
    eventos = collector(CgeCollector).normalize([parse_outage(registro())])
    assert eventos[0].source is EventSource.CGE
    assert eventos[0].raw_data["company"] == "cge"


# --- Aislamiento de familia --------------------------------------------------


def test_un_corte_cae_en_la_familia_power():
    assert family_of_event(EventType.POWER_OUTAGE) == "power"
    assert EVENT_TO_INCIDENT_TYPE[EventType.POWER_OUTAGE] is IncidentType.POWER_OUTAGE


def test_un_corte_no_puede_fundirse_con_un_incendio():
    """La coincidencia entre ambos es lo ESPERABLE, no la excepción.

    Un incendio derriba tendido y provoca un corte. Sin la partición por familia
    el motor leería el incendio y el corte como el mismo hecho, y el mapa
    mostraría un punto que no describe ninguno de los dos.
    """
    assert family_of_event(EventType.POWER_OUTAGE) != family_of_event(
        EventType.WILDFIRE
    )
    assert family_of_event(EventType.POWER_OUTAGE) != family_of_event(
        EventType.ACCIDENT
    )


def test_los_cortes_si_se_agrupan_entre_si():
    """La otra mitad: dos avisos del mismo corte deben unirse.

    Chilquinta publica un corte como varios polígonos cuando afecta a sectores
    contiguos; que se agrupen es lo que evita diez marcadores para un solo
    evento.
    """
    assert EventType.POWER_OUTAGE in CORRELATABLE_EVENT_TYPES


def test_una_distribuidora_confirma_su_propio_corte():
    """Pero sólo el corte, no su causa."""
    from app.services.correlation.confidence import rule_for

    for fuente in (EventSource.CHILQUINTA, EventSource.CGE):
        regla = rule_for(fuente)
        assert regla.confirming is True
        assert regla.max_weight == 1.0


def test_la_etiqueta_del_mapa_nombra_el_corte_y_no_una_emergencia_generica():
    from app.models.enums import ConfidenceLevel, style_for

    assert style_for(ConfidenceLevel.CONFIRMED, "power").label == (
        "Corte de suministro confirmado"
    )


def test_los_dos_collectors_corren_cada_cinco_minutos():
    assert ChilquintaCollector.poll_interval_seconds() == 300
    assert CgeCollector.poll_interval_seconds() == 300


def test_las_dos_electricas_estan_registradas():
    """La capa eléctrica vuelve a ser dos fuentes.

    CGE se desregistró mientras su URL devolvía el HTML del visor. Resultó que no
    tiene API: publica un KMZ, `CGE_API_URL` apunta a ese archivo y `cge_worker`
    lo lee. El detalle de ese camino se prueba en `test_cge_kmz.py`; acá sólo
    importa que el orquestador cargue las dos.
    """
    from app.collectors.registry import available_collectors

    disponibles = available_collectors()
    assert "chilquinta_cortes" in disponibles
    assert "cge_cortes" in disponibles


def test_sin_url_el_collector_falla_de_forma_accionable(monkeypatch):
    """El mensaje nombra la variable que falta: "define CGE_API_URL" es accionable."""
    from app.core.exceptions import CollectorError

    monkeypatch.setattr(settings, "CGE_API_URL", "")
    with pytest.raises(CollectorError, match="CGE_API_URL"):
        CgeCollector(None)


# --- Filtro espacial en el bucle de extracción -------------------------------
#
# Se movió de `normalize()` a `fetch()`: las dos distribuidoras publican su zona
# de concesión completa —CGE llega hasta Aysén— y arrastrar cortes de Chiloé por
# todo el pipeline para descartarlos al final es trabajo por un dato que nunca
# se va a usar.


def outside_records() -> list[dict]:
    """Cortes reales de la zona de concesión, fuera de la V Región."""
    return [
        registro(id="X1", latitud=-41.47, longitud=-72.94, comuna="Puerto Montt"),
        registro(id="X2", latitud=-36.83, longitud=-73.05, comuna="Concepción"),
        registro(id="X3", latitud=-53.15, longitud=-70.91, comuna="Punta Arenas"),
        registro(id="X4", latitud=-23.65, longitud=-70.40, comuna="Antofagasta"),
    ]


@respx.mock
def test_el_filtro_espacial_actua_en_la_extraccion():
    """Lo de fuera se descarta ANTES de mapear: no llega a `normalize`.

    Por el camino real de Chilquinta, con su POST: el filtro tiene que actuar
    sobre lo que la red devuelve, no sólo sobre registros armados a mano.
    """
    url = "https://feed.test/cortes"
    respx.post(url).mock(
        return_value=httpx.Response(
            200, json={"data": [registro(), *outside_records()]}
        )
    )

    instancia = collector()
    instancia.url = url
    cortes = asyncio.run(instancia.fetch())

    assert len(cortes) == 1, "sólo el de Viña del Mar sobrevive a la extracción"
    assert cortes[0].commune == "Viña del Mar"


@pytest.mark.parametrize("clase", [ChilquintaCollector, CgeCollector])
def test_el_filtro_espacial_no_depende_del_transporte(clase):
    """El recorte vive en `fetch()`, así que da igual de dónde vengan los registros.

    Las dos distribuidoras adquieren de formas distintas —Chilquinta un JSON por
    POST, CGE un KMZ que descomprime en memoria— y esa diferencia está aislada
    en `load_records()`. Sustituirlo por una lista fija comprueba el invariante
    que importa: **ninguna señal fuera de la V Región llega al dominio**, venga
    por el transporte que venga.

    Escrito así, un transporte nuevo hereda esta garantía sin escribir un test
    nuevo, que es justo la razón de que `load_records()` esté separado.
    """
    registros = [registro(), *outside_records()]

    instancia = collector(clase)

    async def load_records():
        return registros

    instancia.load_records = load_records  # type: ignore[method-assign]
    cortes = asyncio.run(instancia.fetch())

    assert [corte.commune for corte in cortes] == ["Viña del Mar"]


@respx.mock
def test_un_feed_entero_fuera_de_region_no_es_una_degradacion():
    """Que todo caiga fuera es el filtro funcionando, no un fallo del parseo.

    Se distingue de "llegaron registros y ninguno se entendió", que sí es una
    señal de que el esquema cambió y merece `partial`.
    """
    url = "https://feed.test/cortes"
    # `route` y no `get`: Chilquinta consulta por POST y CGE por GET, y lo que
    # este test comprueba —el filtro espacial, el parseo— es común a las dos.
    respx.route(url=url).mock(
        return_value=httpx.Response(200, json={"data": outside_records()})
    )

    instancia = collector()
    instancia.url = url
    cortes = asyncio.run(instancia.fetch())

    assert cortes == []
    assert instancia.warnings == [], "descartar por región no debe ensuciar la corrida"


@respx.mock
def test_un_feed_ilegible_si_avisa():
    url = "https://feed.test/cortes"
    # `route` y no `get`: Chilquinta consulta por POST y CGE por GET, y lo que
    # este test comprueba —el filtro espacial, el parseo— es común a las dos.
    respx.route(url=url).mock(
        return_value=httpx.Response(200, json={"data": [{"sin": "coordenadas"}]})
    )

    instancia = collector()
    instancia.url = url
    asyncio.run(instancia.fetch())

    assert any("esquema" in aviso for aviso in instancia.warnings)


def test_el_filtro_usa_el_bbox_regional_de_config():
    """Una sola definición del territorio para todas las capas.

    Es la misma primitiva que usa el collector del CSN: `BoundingBox.contains`
    sobre `settings.region_bbox`, o sea REGION_NORTH/SOUTH/EAST/WEST. Mover la
    caja mueve todas las capas a la vez.
    """
    instancia = collector()
    bbox = settings.region_bbox

    assert instancia.bbox is bbox or (
        instancia.bbox.north == bbox.north and instancia.bbox.south == bbox.south
    )
    assert instancia.bbox.contains(*VINA) is True
    assert instancia.bbox.contains(-41.47, -72.94) is False  # Puerto Montt


def test_normalize_mantiene_el_invariante_aunque_fetch_ya_filtre():
    """La segunda comprobación no es redundante.

    `normalize()` es pública: los tests y cualquier reproceso futuro la llaman
    con registros armados a mano. El invariante "ninguna señal fuera de la
    región llega al dominio" tiene que sostenerse por el camino que sea.
    """
    lejano = parse_outage(registro(latitud=-41.47, longitud=-72.94))
    assert lejano is not None, "el parser no filtra por región; eso es del collector"
    assert collector().normalize([lejano]) == []


# --- El transporte: método, cabeceras y cuerpo -------------------------------
#
# El endpoint de Chilquinta no es un feed abierto: es la ruta XHR del visor,
# se consulta por POST, exige una API key estática en `x-api-key` y espera el
# código de empresa en el cuerpo. Nada de eso se puede verificar contra el
# servidor real desde la suite, así que lo que se prueba acá es que la petición
# **sale armada como se descubrió** — que es lo único que depende de nosotros.


@respx.mock
def test_chilquinta_consulta_por_post_con_la_api_key():
    """La llave viaja en la cabecera, no en la URL ni en el cuerpo."""
    from app.collectors.power.chilquinta_worker import API_KEY_HEADER

    url = "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    ruta = respx.post(url).mock(return_value=httpx.Response(200, json={"data": []}))

    instancia = collector()
    instancia.url = url
    asyncio.run(instancia.fetch())

    assert ruta.called, "debe ser POST: un GET no lo habría emparejado"
    peticion = ruta.calls[0].request
    assert peticion.headers[API_KEY_HEADER] == API_KEY_FALSA
    assert API_KEY_FALSA in str(peticion.url), "la llave no va en la URL"


@respx.mock
def test_chilquinta_manda_el_codigo_de_empresa_en_el_cuerpo():
    """`codEmp` y `empresa` con el mismo valor: se replica lo que hace el visor.

    Que los dos campos lleven lo mismo no es redundancia nuestra. No sabemos
    cuál de los dos lee el backend y adivinar mal devolvería el catálogo de otra
    filial, o ninguno.
    """
    import json as _json

    url = "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    ruta = respx.post(url).mock(return_value=httpx.Response(200, json={"data": []}))

    instancia = collector()
    instancia.url = url
    asyncio.run(instancia.fetch())

    cuerpo = _json.loads(ruta.calls[0].request.content)
    assert cuerpo == {"codEmp": "006", "empresa": "006"}


@respx.mock
def test_el_user_agent_del_proyecto_sigue_yendo():
    """Consultamos un servidor ajeno: quien lo opere tiene derecho a saber quién es.

    La API key se añadió *sobre* las cabeceras de la familia, no en lugar de
    ellas.
    """
    url = "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    ruta = respx.post(url).mock(return_value=httpx.Response(200, json={"data": []}))

    instancia = collector()
    instancia.url = url
    asyncio.run(instancia.fetch())

    assert (
        ruta.calls[0].request.headers["User-Agent"] == settings.NOMINATIM_USER_AGENT
    )


@respx.mock
def test_el_filtro_espacial_sigue_actuando_sobre_la_respuesta_del_post():
    """Cambiar el transporte no puede aflojar el recorte territorial.

    Es el mismo invariante de siempre —`BoundingBox.contains` sobre
    `settings.region_bbox`, antes de mapear al dominio— comprobado por el camino
    nuevo: POST con cabeceras en vez de GET.
    """
    url = "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    respx.post(url).mock(
        return_value=httpx.Response(
            200, json={"data": [registro(), *outside_records()]}
        )
    )

    instancia = collector()
    instancia.url = url
    cortes = asyncio.run(instancia.fetch())

    assert len(cortes) == 1
    assert cortes[0].commune == "Viña del Mar"


def test_sin_api_key_el_collector_falla_de_forma_accionable(monkeypatch):
    """"Define CHILQUINTA_API_KEY" es accionable; un 401 en el log no lo es."""
    from app.core.exceptions import CollectorError

    monkeypatch.setattr(settings, "CHILQUINTA_API_KEY", "")
    with pytest.raises(CollectorError, match="CHILQUINTA_API_KEY"):
        ChilquintaCollector(None)


def test_la_api_key_no_queda_escrita_en_la_traza_de_la_corrida():
    """`run_params` va a `collector_runs`, que cualquiera puede consultar.

    Las cabeceras se construyen aparte justamente para que la credencial no
    termine en el historial de corridas.
    """
    instancia = collector()
    instancia.url = "https://mapainterrupciones.chilquinta.cl/obtieneImage"

    assert API_KEY_FALSA not in str(instancia.run_params())
    assert instancia.run_params()["method"] == "POST"


# La guarda del query string —httpx reemplaza la query de la URL cuando recibe
# `params`, aunque venga vacío— dejó de tener dueño en esta capa: Chilquinta
# manda su filtro en el cuerpo del POST y CGE descarga un archivo. Se comprueba
# donde vive, sobre `request_response`, en tests/test_geoservices.py.


def test_las_urls_definitivas_estan_configuradas():
    """La de Chilquinta es la ruta XHR, no la del visor.

    `obtieneImage` devuelve JSON pese al nombre: es ofuscación del frontend. Si
    alguien "corrige" esta URL hacia `/mapas`, llega HTML y el collector falla.
    """
    assert settings.CHILQUINTA_API_URL == (
        "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    )
    assert settings.CHILQUINTA_COD_EMP == "006"
