"""Capa de cortes eléctricos: parseo tolerante, metadata y aislamiento.

Chilquinta y CGE resultaron ser la misma clase de fuente: **ninguna de las dos
tiene API**. Las dos publican un archivo estático que su visor lee —un JSONP en
un caso, un KMZ en el otro— y lo que este proyecto consume es ese volcado.

Eso se supo tarde y de la peor manera. Durante seis iteraciones el collector de
Chilquinta apuntó a `/obtieneImage`, una ruta que devuelve 401 y que su visor
nunca llama, y se le fueron añadiendo capas —sesión, CSRF, cabeceras de
navegador, reintentos— que respondían cada vez mejor a una pregunta equivocada.
Los tests de acá abajo llevan la marca de eso: varios existen para que nadie
vuelva a inventar una explicación que la fuente no pide.

Del esquema **sí** hay ahora una captura real (2026-08-25), así que estos tests
dejaron de probar formas hipotéticas y prueban una observada. Lo que siguen
haciendo, y es su valor principal, es garantizar que un cambio de esquema
**falle de forma legible** en vez de devolver cero cortes en silencio.
"""

from __future__ import annotations

import asyncio
import json
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
from app.collectors.power.chilquinta_worker import (
    CACHE_BUSTER,
    JSONP_CALLBACK,
    SEGMENT_LAT,
    SEGMENT_LON,
    SEGMENTS_KEY,
    ChilquintaCollector,
    con_rompe_caches,
    punto_de_la_orden,
    quitar_jsonp,
)
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


#: Una orden tal como la publica el archivo, con los nombres y los tipos reales:
#: los conteos vienen como **string**, las coordenadas de la orden vienen
#: **vacías** y los puntos están en `segmentos`. Copiado de la captura del
#: 2026-08-25, no inventado.
def orden(**overrides) -> dict:
    base = {
        "orden": "10209025",
        "etr": "27-08-2026 17:00:00",
        "latitud": "",
        "longitud": "",
        "cant_clientes": "68",
        "tipo": "dx",
        "cant_seg": 3,
        "cant_trafos": 1,
        "comuna": "VIÑA DEL MAR",
        SEGMENTS_KEY: [
            [
                {SEGMENT_LAT: str(VINA[0] - 0.001), SEGMENT_LON: str(VINA[1] - 0.001)},
                {SEGMENT_LAT: str(VINA[0]), SEGMENT_LON: str(VINA[1])},
                {SEGMENT_LAT: str(VINA[0] + 0.001), SEGMENT_LON: str(VINA[1] + 0.001)},
            ]
        ],
    }
    base.update(overrides)
    return base


def volcado(*ordenes: dict, callback: str = JSONP_CALLBACK) -> str:
    """El cuerpo tal como sale del servidor: JSONP con el sobre de tres claves.

    Se reproduce el sobre entero —`headers`, `exception`, `original`— y no sólo
    la lista, porque el desenvoltorio automático de `extract_records` **no**
    atraviesa un sobre de tres claves: sólo desenvuelve los de una. Un mock que
    devolviera la lista pelada haría pasar el test sin ejercitar el camino real.
    """
    cuerpo = {
        "headers": {},
        "exception": None,
        "original": {"ordenes_totales": len(ordenes), "ordenes": list(ordenes)},
    }
    return f"{callback}({json.dumps(cuerpo, ensure_ascii=False)})"


def responde(url: str, *ordenes: dict):
    """Mock del archivo. `respx.get` porque el método también está bajo prueba."""
    return respx.get(url).mock(
        return_value=httpx.Response(200, text=volcado(*ordenes))
    )


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
    assert "cliente" not in texto, "sin conteo no se menciona"
    assert "reposición" not in texto, "sin ETA no se menciona"


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


def fuera_de_region() -> list[dict]:
    """Las mismas, con la forma real de una orden de Chilquinta."""
    return [
        orden(orden="X1", comuna="PUERTO MONTT",
              **{SEGMENTS_KEY: [[{SEGMENT_LAT: "-41.47", SEGMENT_LON: "-72.94"}]]}),
        orden(orden="X2", comuna="CONCEPCIÓN",
              **{SEGMENTS_KEY: [[{SEGMENT_LAT: "-36.83", SEGMENT_LON: "-73.05"}]]}),
        orden(orden="X3", comuna="PUNTA ARENAS",
              **{SEGMENTS_KEY: [[{SEGMENT_LAT: "-53.15", SEGMENT_LON: "-70.91"}]]}),
    ]


@respx.mock
def test_el_filtro_espacial_actua_en_la_extraccion():
    """Lo de fuera se descarta ANTES de mapear: no llega a `normalize`.

    Por el camino real de Chilquinta —descargar el volcado—: el filtro tiene que
    actuar sobre lo que la red devuelve, no sólo sobre registros armados a mano.
    """
    url = "https://feed.test/results.js"
    responde(url, orden(), *fuera_de_region())

    instancia = collector()
    instancia.url = url
    cortes = asyncio.run(instancia.fetch())

    assert len(cortes) == 1, "sólo el de Viña del Mar sobrevive a la extracción"
    assert cortes[0].commune == "VIÑA DEL MAR"


@pytest.mark.parametrize("clase", [ChilquintaCollector, CgeCollector])
def test_el_filtro_espacial_no_depende_del_transporte(clase):
    """El recorte vive en `fetch()`, así que da igual de dónde vengan los registros.

    Las dos distribuidoras adquieren de formas distintas —Chilquinta un JSONP que
    desenvuelve, CGE un KMZ que descomprime en memoria— y esa diferencia está aislada
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
    url = "https://feed.test/results.js"
    responde(url, *fuera_de_region())

    instancia = collector()
    instancia.url = url
    cortes = asyncio.run(instancia.fetch())

    assert cortes == []
    assert instancia.warnings == [], "descartar por región no debe ensuciar la corrida"


@respx.mock
def test_un_feed_ilegible_si_avisa():
    url = "https://feed.test/results.js"
    responde(url, {"sin": "coordenadas"})

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


# --- El transporte: un archivo estático, JSONP y coordenadas escondidas -------
#
# Chilquinta no tiene API. Su visor lee `dt/results_006.js`, un volcado que la
# empresa regenera; el `006` es el código de filial y forma parte del nombre.
#
# Antes de saber eso, este bloque probaba una sesión Laravel, un token CSRF,
# cabeceras de navegador y reintentos contra `/obtieneImage` — una ruta que
# devuelve 401 y que el visor nunca llama. Nada de aquello existe ya. Quedan, en
# cambio, tests que fijan las tres trampas **reales** del formato, que son las
# que devolverían cero cortes en silencio si alguien las simplificara:
#
#   1. el cuerpo es JSONP, no JSON;
#   2. las coordenadas de la orden vienen vacías y están en `segmentos`;
#   3. una orden es un corte, aunque tenga 64 segmentos.

CHILQUINTA_URL = "https://mapainterrupciones.chilquinta.cl/dt/results_006.js"


@respx.mock
def test_chilquinta_descarga_el_archivo_por_get():
    """Un GET a un estático: el mock por método es la aserción.

    `respx.get` sólo empareja peticiones GET, así que si el collector volviera a
    POST este test no encontraría ruta y fallaría por «no mock».
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert ruta.called
    assert ruta.calls[0].request.method == "GET"


@respx.mock
def test_una_sola_peticion_por_corrida():
    """Se acabaron los preámbulos. Un archivo, una descarga.

    Hubo una versión que hacía dos peticiones por corrida —un priming de sesión y
    la consulta—, y otra que llegaba a cuatro cuando reintentaba. Contra un
    estático no hay nada que negociar, y este test fija que no vuelva a haberlo.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert len(ruta.calls) == 1


@respx.mock
def test_la_peticion_no_lleva_credenciales_ni_filtros():
    """No hay API key, ni cookies, ni CSRF, ni `orderId`: el archivo es público.

    Se afirma en negativo a propósito. Las seis iteraciones anteriores dejaron
    documentación, mensajes de commit y logs hablando de `x-api-key`,
    `X-XSRF-TOKEN` y `?orderId=null`; si alguien los encuentra y decide
    "restaurarlos", este test lo detiene.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    peticion = ruta.calls[0].request
    cabeceras = {clave.lower() for clave in peticion.headers}

    assert "x-api-key" not in cabeceras
    assert "x-xsrf-token" not in cabeceras
    assert "x-csrf-token" not in cabeceras
    assert "x-orden-buscada" not in cabeceras
    assert "cookie" not in cabeceras
    assert "orderId" not in peticion.url.params
    assert peticion.content == b"", "un GET a un estático no lleva cuerpo"


@respx.mock
def test_el_user_agent_del_proyecto_va_en_la_peticion():
    """Consultamos un servidor ajeno: quien lo opere tiene derecho a saber quién es.

    Hubo una versión que se disfrazaba de Chrome para esquivar un bloqueo que
    resultó no existir. Contra un archivo público no hay nada que esquivar.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    agente = ruta.calls[0].request.headers["User-Agent"]
    assert agente == settings.NOMINATIM_USER_AGENT
    assert "Chrome/" not in agente


# --- Trampa 1: el cuerpo es JSONP --------------------------------------------


def test_el_envoltorio_jsonp_se_quita_por_los_parentesis():
    """Y no comparando contra el nombre de la función, que es prestado.

    `eqfeed_callback` está copiado del tutorial de terremotos de Google Maps, así
    que no hay ninguna garantía de que Chilquinta no lo renombre. Emparejar por
    nombre convertiría un cambio cosmético en una caída; los paréntesis, en
    cambio, son lo que define el formato.
    """
    assert quitar_jsonp('eqfeed_callback({"a": 1})') == '{"a": 1}'
    assert quitar_jsonp('otroNombre({"a": 1})') == '{"a": 1}'
    assert quitar_jsonp('  espacios( {"a": 1} )  ') == '{"a": 1}'


def test_un_cuerpo_ya_json_se_deja_intacto():
    """Si algún día dejan de envolverlo, esto sigue funcionando en vez de romperse."""
    assert quitar_jsonp('{"a": 1}') == '{"a": 1}'
    assert quitar_jsonp('[{"a": 1}]') == '[{"a": 1}]'


def test_el_cierre_es_el_ultimo_parentesis_y_no_el_primero():
    """El JSON de dentro trae los suyos; cortar por el primero parte el cuerpo.

    Un JSON con paréntesis dentro de un string es lo normal en direcciones
    chilenas —«Av. Argentina (frente al estadio)»— así que este caso no es
    rebuscado, es el martes.
    """
    crudo = 'cb({"sector": "Av. Argentina (frente al estadio)"})'
    assert json.loads(quitar_jsonp(crudo))["sector"] == (
        "Av. Argentina (frente al estadio)"
    )


@respx.mock
def test_un_cuerpo_que_no_es_json_falla_nombrando_lo_que_llego():
    """El HTML de un portal caído tiene que reconocerse de un vistazo.

    Es la diferencia entre depurar el próximo incidente en minutos o discutir con
    un «Expecting value: line 1 column 1».
    """
    from app.core.exceptions import CollectorError

    respx.get(CHILQUINTA_URL).mock(
        return_value=httpx.Response(200, text="<!doctype html><html>error 502</html>")
    )

    instancia = collector()
    instancia.url = CHILQUINTA_URL

    with pytest.raises(CollectorError, match="doctype html"):
        asyncio.run(instancia.fetch())


@respx.mock
def test_un_archivo_vacio_no_es_una_noche_tranquila():
    """Cero bytes es un fallo de la fuente, no «no hay cortes».

    Un volcado con `"ordenes": []` sí es una noche tranquila; un cuerpo vacío
    significa que algo se rompió aguas arriba, y tratarlos igual es cómo una capa
    se apaga sin que nadie se entere.
    """
    from app.core.exceptions import CollectorError

    respx.get(CHILQUINTA_URL).mock(return_value=httpx.Response(200, text="   "))

    instancia = collector()
    instancia.url = CHILQUINTA_URL

    with pytest.raises(CollectorError, match="vacío"):
        asyncio.run(instancia.fetch())


@respx.mock
def test_una_lista_de_ordenes_vacia_si_es_una_noche_tranquila():
    ruta = responde(CHILQUINTA_URL)

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    cortes = asyncio.run(instancia.fetch())

    assert ruta.called
    assert cortes == []


@respx.mock
def test_el_sobre_de_tres_claves_se_atraviesa():
    """`extract_records` sola no llega: sólo desenvuelve sobres de una clave.

    El archivo trae `headers`, `exception` y `original`, así que el worker tiene
    que bajar a `original` antes de buscar la lista. Sin ese paso, el collector
    fallaría diciendo que no encuentra las órdenes — con las órdenes delante.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    cortes = asyncio.run(instancia.fetch())

    assert ruta.called
    assert len(cortes) == 1


@respx.mock
def test_un_esquema_desconocido_falla_diciendo_que_llego():
    from app.core.exceptions import CollectorError

    respx.get(CHILQUINTA_URL).mock(
        return_value=httpx.Response(200, text='cb({"original": {"otra_cosa": 1}})')
    )

    instancia = collector()
    instancia.url = CHILQUINTA_URL

    with pytest.raises(CollectorError, match="otra_cosa"):
        asyncio.run(instancia.fetch())


# --- Trampa 2: las coordenadas están en `segmentos` --------------------------


def test_la_orden_usa_su_propio_punto_cuando_lo_trae():
    """Cuando la empresa lo publica, está diciendo dónde considera ELLA que está.

    Eso vale más que cualquier centroide que calculemos nosotros.
    """
    punto = punto_de_la_orden(orden(latitud=str(VINA[0]), longitud=str(VINA[1])))

    assert punto == pytest.approx(VINA)


def test_sin_punto_propio_se_usa_el_centroide_de_los_segmentos():
    """El caso normal: 25 de 29 órdenes de la captura real venían así.

    Sin esto el collector devuelve cero cortes **en silencio**, que es el modo de
    fallo que más caro sale: la capa se apaga y el mapa sigue pareciendo correcto.
    """
    punto = punto_de_la_orden(orden())

    assert punto is not None
    # Los tres vértices están centrados en VINA, así que su promedio es VINA.
    assert punto == pytest.approx(VINA)


def test_el_centroide_promedia_todos_los_grupos_de_segmentos():
    """`segmentos` es una lista DE LISTAS y hay que recorrer los dos niveles.

    Quedarse con el primer grupo daría un punto plausible —y equivocado— cuando
    una orden abarca varios polígonos.
    """
    dos_grupos = orden(
        **{
            SEGMENTS_KEY: [
                [{SEGMENT_LAT: "-33.00", SEGMENT_LON: "-71.50"}],
                [{SEGMENT_LAT: "-33.02", SEGMENT_LON: "-71.54"}],
            ]
        }
    )
    punto = punto_de_la_orden(dos_grupos)

    assert punto == pytest.approx((-33.01, -71.52))


def test_una_lista_plana_de_puntos_tambien_vale():
    """El formato se conoce por una captura, no por documentación.

    Aceptar las dos formas cuesta dos líneas; equivocarse cuesta la capa entera.
    """
    plana = orden(
        **{SEGMENTS_KEY: [{SEGMENT_LAT: str(VINA[0]), SEGMENT_LON: str(VINA[1])}]}
    )

    assert punto_de_la_orden(plana) == pytest.approx(VINA)


@pytest.mark.parametrize(
    "segmentos",
    [[], None, "no soy una lista", [[]], [[{"otra": "clave"}]], [[{SEGMENT_LAT: ""}]]],
)
def test_una_orden_sin_vertices_utilizables_no_tiene_punto(segmentos):
    """`None` y no una excepción: lo descarta `parse_outage`, contándolo."""
    assert punto_de_la_orden(orden(**{SEGMENTS_KEY: segmentos})) is None


@respx.mock
def test_el_punto_derivado_llega_al_evento():
    """El camino completo: `segmentos` → centroide → `lat`/`lon` del evento."""
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    cortes = asyncio.run(instancia.fetch())

    assert ruta.called
    assert len(cortes) == 1
    assert (cortes[0].lat, cortes[0].lon) == pytest.approx(VINA)


@respx.mock
def test_los_segmentos_originales_sobreviven_en_raw_data():
    """Para poder dibujar el polígono el día que se quiera, sin volver a consultar.

    El punto es una derivación nuestra; los vértices son el dato. Perderlos al
    normalizar sería tirar información que la fuente sí publicó.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    eventos = instancia.normalize(asyncio.run(instancia.fetch()))

    assert ruta.called
    origen = eventos[0].raw_data["_source_record"]
    assert origen[SEGMENTS_KEY][0][0][SEGMENT_LAT] is not None
    assert origen["cant_seg"] == 3


def test_la_derivacion_no_ensucia_el_registro_de_una_orden_ilegible():
    """Si no hay punto, la orden pasa intacta y la descarta `parse_outage`.

    Hay un solo sitio donde se decide qué registro es inservible, y no es el
    worker: duplicar ese criterio es cómo dos capas acaban discrepando.
    """
    instancia = collector()
    sin_punto = orden(**{SEGMENTS_KEY: []})

    assert instancia.con_punto(sin_punto) == sin_punto
    assert parse_outage(instancia.con_punto(sin_punto)) is None


def test_una_orden_que_no_es_un_mapping_pasa_de_largo():
    instancia = collector()

    assert instancia.con_punto("no soy un objeto") == "no soy un objeto"
    assert instancia.con_punto(None) is None


# --- Trampa 3: una orden es un corte -----------------------------------------


@respx.mock
def test_una_orden_con_muchos_segmentos_emite_un_solo_corte():
    """64 segmentos no son 64 cortes, y `cant_clientes` está a nivel de orden.

    Emitir uno por segmento multiplicaría los clientes afectados por el número de
    segmentos —64 en el peor caso observado— y llenaría el mapa de marcadores
    para un solo evento. La orden es la unidad con la que trabaja la empresa y es
    también la unidad del `external_id`.
    """
    muchos = orden(
        cant_seg=64,
        **{
            SEGMENTS_KEY: [
                [
                    {
                        SEGMENT_LAT: str(VINA[0] + i * 0.0001),
                        SEGMENT_LON: str(VINA[1] + i * 0.0001),
                    }
                    for i in range(64)
                ]
            ]
        },
    )
    ruta = responde(CHILQUINTA_URL, muchos)

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    eventos = instancia.normalize(asyncio.run(instancia.fetch()))

    assert ruta.called
    assert len(eventos) == 1
    assert eventos[0].raw_data["affected_clients"] == 68, "no 68 × 64"


# --- Los nombres de campo del archivo ----------------------------------------


@respx.mock
def test_los_campos_reales_del_archivo_se_leen():
    """`orden`, `etr`, `cant_clientes`, `comuna`: los nombres de la captura real.

    Los conteos vienen como **string** (`"68"`), que es justo el tipo de detalle
    que un mock escrito de memoria «arregla» sin querer.
    """
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    eventos = instancia.normalize(asyncio.run(instancia.fetch()))

    assert ruta.called
    evento = eventos[0]
    assert evento.external_id == "chilquinta:10209025"
    assert evento.raw_data["affected_clients"] == 68
    assert evento.raw_data["comuna"] == "VIÑA DEL MAR"
    assert evento.raw_data[OUTAGE_KEY]["restoration_at"] is not None


def test_el_etr_se_interpreta_como_hora_chilena():
    """`dd-mm-yyyy HH:MM:SS` y en hora de pared local.

    Asumir UTC desplazaría cada reposición cuatro horas: una que aún no ocurrió
    figuraría como cumplida. Agosto es invierno austral, o sea UTC-4.
    """
    corte = parse_outage(orden(etr="27-08-2026 17:00:00", latitud=str(VINA[0]),
                               longitud=str(VINA[1])))

    assert corte is not None
    assert corte.restoration_at == datetime(2026, 8, 27, 21, 0, tzinfo=UTC)


@respx.mock
def test_el_tipo_se_conserva_pero_no_se_filtra():
    """`dx` e `inter` no están documentados, así que no se les inventa semántica.

    Si `inter` resultara ser «interrupción programada», habría que decidir si un
    corte anunciado con días de antelación debe emitirse como incidente activo.
    Hasta saberlo, se guarda y se emite todo: descartar por una corazonada pierde
    cortes reales sin dejar rastro.
    """
    ruta = responde(CHILQUINTA_URL, orden(tipo="dx"), orden(orden="999", tipo="inter"))

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    eventos = instancia.normalize(asyncio.run(instancia.fetch()))

    assert ruta.called
    assert len(eventos) == 2
    assert {e.raw_data["_source_record"]["tipo"] for e in eventos} == {"dx", "inter"}


# --- El rompe-cachés ---------------------------------------------------------


def test_la_url_lleva_un_rompe_caches_fresco():
    """El archivo es estático y se sirve con cabeceras de estático.

    Sin `?v=`, una CDN o un proxy corporativo pueden devolver el volcado de hace
    horas. En un mapa de cortes un dato viejo no se distingue de uno bueno, que
    es exactamente el modo de fallo silencioso contra el que está escrito el
    resto de este módulo.
    """
    primera = con_rompe_caches(CHILQUINTA_URL)
    segunda = con_rompe_caches(CHILQUINTA_URL)

    assert f"{CACHE_BUSTER}=" in primera
    assert primera.startswith(f"{CHILQUINTA_URL}?")
    # Epoch en milisegundos: 13 dígitos hasta bien entrado el siglo XXXIII.
    valor = primera.split(f"{CACHE_BUSTER}=")[1]
    assert valor.isdigit() and len(valor) >= 13
    assert segunda >= primera, "no se congela entre llamadas"


def test_un_rompe_caches_puesto_a_mano_se_respeta():
    """Para poder reproducir una descarga concreta mientras se depura."""
    fijo = f"{CHILQUINTA_URL}?{CACHE_BUSTER}=123"

    assert con_rompe_caches(fijo) == fijo


def test_el_rompe_caches_se_suma_a_una_query_existente():
    """Concatenar `"?v=…"` a ciegas rompería una URL que ya tenga query."""
    resultado = con_rompe_caches(f"{CHILQUINTA_URL}?emp=006")

    assert resultado.startswith(f"{CHILQUINTA_URL}?emp=006&{CACHE_BUSTER}=")


@respx.mock
def test_el_rompe_caches_llega_al_cable():
    ruta = responde(CHILQUINTA_URL, orden())

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert CACHE_BUSTER in ruta.calls[0].request.url.params


# --- El filtro espacial, por el camino real ----------------------------------


@respx.mock
def test_el_filtro_espacial_sigue_actuando_por_el_camino_real():
    """Cambiar el transporte no puede aflojar el recorte territorial.

    Es el mismo invariante de siempre —`BoundingBox.contains` sobre
    `settings.region_bbox`, antes de mapear al dominio— comprobado por el camino
    que se usa en producción.
    """
    lejana = orden(
        orden="X1",
        comuna="PUERTO MONTT",
        **{SEGMENTS_KEY: [[{SEGMENT_LAT: "-41.47", SEGMENT_LON: "-72.94"}]]},
    )
    responde(CHILQUINTA_URL, orden(), lejana)

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    cortes = asyncio.run(instancia.fetch())

    assert len(cortes) == 1
    assert cortes[0].commune == "VIÑA DEL MAR"


def test_las_urls_definitivas_estan_configuradas():
    """Las dos eléctricas apuntan a un archivo, no a una API. Ninguna la tiene.

    La de Chilquinta lleva el `006` —el código de filial— **dentro del nombre**,
    así que no es un parámetro que se pueda mover a una cabecera ni a un query
    string. Es el nombre del archivo.

    Este test existe sobre todo como marcador de camino: hubo seis intentos con
    esta variable apuntando a `…/obtieneImage`, una ruta que devuelve 401 y que
    el visor nunca llama. Aparece en logs y en mensajes de commit viejos, así que
    quien la encuentre y quiera "restaurarla" se topa primero con esto.
    """
    assert settings.CHILQUINTA_API_URL == (
        "https://mapainterrupciones.chilquinta.cl/dt/results_006.js"
    )
    assert "obtieneImage" not in settings.CHILQUINTA_API_URL
    assert settings.CGE_API_URL.endswith(".kmz")


def test_chilquinta_ya_no_necesita_credenciales():
    """El archivo es público: si vuelven a aparecer estas variables, algo se torció.

    `CHILQUINTA_API_KEY` y `CHILQUINTA_COD_EMP` se eliminaron con el pivote. La
    llave que hubo en el `.env` conviene rotarla igualmente — nadie recuerda de
    dónde salió, y estuvo en un fichero durante siete iteraciones.
    """
    assert not hasattr(settings, "CHILQUINTA_API_KEY")
    assert not hasattr(settings, "CHILQUINTA_COD_EMP")

def test_un_punto_derivado_queda_marcado_como_tal():
    """El registro que acaba en `_source_record` es el normalizado, no el crudo.

    Eso lo descubrió un smoke: `parse_outage` guarda en `raw` lo que se le pasa,
    así que las coordenadas que inyectamos viajan a `raw_data` como si las
    hubiera publicado la empresa. Sin la marca, un centroide de 64 vértices y una
    coordenada real son indistinguibles al reprocesar.
    """
    from app.collectors.power.chilquinta_worker import DERIVED_POINT_KEY

    instancia = collector()

    derivada = instancia.con_punto(orden())
    assert derivada[DERIVED_POINT_KEY] is True
    assert derivada["latitud"] == pytest.approx(VINA[0])
    # Los vértices siguen ahí: el punto es nuestro, el polígono es el dato.
    assert derivada[SEGMENTS_KEY] == orden()[SEGMENTS_KEY]


def test_una_orden_con_punto_propio_no_se_toca():
    """Si la empresa publicó la coordenada, se respeta tal como la publicó.

    Reescribirla —aunque fuera con el mismo número convertido a float— sería
    sustituir el dato de la fuente por una conversión nuestra sin ganar nada, y
    dejaría `_source_record` mintiendo sobre lo que llegó por la red.
    """
    from app.collectors.power.chilquinta_worker import DERIVED_POINT_KEY

    instancia = collector()
    propia = orden(latitud=str(VINA[0]), longitud=str(VINA[1]))

    intacta = instancia.con_punto(propia)
    assert intacta == propia
    assert DERIVED_POINT_KEY not in intacta
    assert intacta["latitud"] == str(VINA[0]), "sigue siendo el texto original"


def test_la_ruta_muerta_se_rechaza_al_construir(monkeypatch):
    """Desplegar el código nuevo con la variable vieja tiene que doler rápido.

    Pasó de verdad: el pivote se desplegó mientras el contenedor conservaba
    `CHILQUINTA_API_URL=…/obtieneImage`, y el síntoma fue un
    `400 Missing required request parameters: [companyCode, orderId]` — un error
    que habla de parámetros que este collector ya no manda, y que por tanto manda
    a buscar el problema al sitio equivocado.

    Fallar al construir deja una fila `failed` en `collector_runs` nombrando la
    variable, que es lo único accionable de toda esa cadena.
    """
    from app.collectors.power.chilquinta_worker import RUTA_MUERTA
    from app.core.exceptions import CollectorError

    monkeypatch.setattr(
        settings,
        "CHILQUINTA_API_URL",
        f"https://mapainterrupciones.chilquinta.cl/{RUTA_MUERTA}",
    )

    with pytest.raises(CollectorError, match="CHILQUINTA_API_URL"):
        ChilquintaCollector(None)


def test_la_url_buena_construye_sin_quejarse():
    """La guarda es estrecha a propósito: rechaza esa ruta, no valida el resto.

    Un patrón que exigiera que la URL se pareciera a `results_XXX.js` rompería el
    día que Chilquinta renombre el archivo — que es un cambio legítimo y
    esperable. Lo que se rechaza es una ruta que sabemos muerta, no todo lo que
    no reconozcamos.
    """
    instancia = ChilquintaCollector(None)

    assert instancia.url == settings.CHILQUINTA_API_URL
    assert "results_006.js" in instancia.url


@respx.mock
def test_la_descarga_insiste_mas_que_la_familia():
    """El host dropea conexiones de forma intermitente; 4,5 s no alcanzan.

    Con el collector ya funcionando y trayendo 29 órdenes, 3 de cada 7 corridas
    morían con `ConnectError: [SSL: WRONG_VERSION_NUMBER]` alternando con
    corridas perfectas contra la misma URL. Los valores por defecto de
    `request_response` reparten sus 3 intentos en t=0, t≈1.5 s y t≈4.5 s —el
    backoff es `backoff * 2**intento`— y la ventana del bloqueo dura más.

    Este test fija que una corrida sobrevive a dos drops seguidos, que es lo que
    no ocurría en producción.
    """
    ruta = respx.get(CHILQUINTA_URL).mock(
        side_effect=[
            httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
            httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
            httpx.Response(200, text=volcado(orden())),
        ]
    )

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    cortes = asyncio.run(instancia.fetch())

    assert len(ruta.calls) == 3, "insiste en vez de rendirse al tercer intento"
    assert [corte.commune for corte in cortes] == ["VIÑA DEL MAR"]
    assert instancia.warnings == [], "un drop absorbido no degrada la corrida"


def test_el_presupuesto_de_reintentos_cabe_en_la_cadencia():
    """Insistir no puede comerse la corrida siguiente.

    Cinco intentos repartidos en 2+4+8+16 = 30 s de espera. Aunque cada intento
    agotara el timeout completo, el total sigue por debajo de los 300 s de
    cadencia. Si alguien sube los reintentos sin mirar esto, salta acá.
    """
    from app.collectors.power.chilquinta_worker import (
        PRIMING_BACKOFF_SECONDS,
        PRIMING_RETRIES,
    )

    intentos = PRIMING_RETRIES + 1
    espera = sum(PRIMING_BACKOFF_SECONDS * (2**i) for i in range(PRIMING_RETRIES))
    peor_caso = intentos * settings.POWER_TIMEOUT_SECONDS + espera

    assert espera == 30.0
    assert peor_caso < settings.POWER_POLL_INTERVAL_SECONDS
