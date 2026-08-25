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


@respx.mock
def test_el_filtro_espacial_actua_en_la_extraccion():
    """Lo de fuera se descarta ANTES de mapear: no llega a `normalize`.

    Por el camino real de Chilquinta —GET con cabeceras—: el filtro tiene que
    actuar sobre lo que la red devuelve, no sólo sobre registros armados a mano.
    """
    url = "https://feed.test/cortes"
    respx.get(url).mock(
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
    GET con cabeceras, CGE un KMZ que descomprime en memoria— y esa diferencia está aislada
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
    # `route` y no `get`: lo que este test comprueba —el filtro espacial, el
    # parseo— es común a las dos distribuidoras, sea cual sea su transporte.
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
    # `route` y no `get`: lo que este test comprueba —el filtro espacial, el
    # parseo— es común a las dos distribuidoras, sea cual sea su transporte.
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


# --- El transporte: un GET con cabeceras y un parámetro obligatorio ----------
#
# El endpoint de Chilquinta no es un feed abierto: es la ruta XHR del visor. Se
# consulta por GET, sin cuerpo, y la consulta se reparte entre tres cabeceras
# —API key, código de filial y orden buscada— y un único parámetro de query,
# `?orderId=0`.
#
# Ese parámetro es obligatorio y **con valor**: sin él el servidor responde
# `400 {"error":"Missing required request parameters: [orderId]"}`, incluso con
# la cabecera `X-Orden-Buscada` presente. Y `?orderId=` vacío recibe ese mismo
# 400, porque el API Gateway que valida los obligatorios cuenta un valor de
# longitud cero como una ausencia. De ahí que el centinela de "todas las
# órdenes" sea `"0"`. Que el mismo dato tenga que ir dos veces —cabecera y
# query— es redundancia del backend de Chilquinta, y se replica tal cual.
#
# Nada de esto se puede verificar contra el servidor real desde la suite, así
# que lo que se prueba acá es que la petición **sale armada como se descubrió**,
# que es lo único que depende de nosotros. Si mañana el visor cambia de esquema,
# estos tests seguirán pasando y el fallo aparecerá en la primera corrida real —
# por eso importa tanto que `describe_shape` diga qué llegó.

CHILQUINTA_URL = "https://mapainterrupciones.chilquinta.cl/obtieneImage"


def responde_vacio(url: str = CHILQUINTA_URL):
    """Mock del endpoint. `respx.get` porque el método también está bajo prueba."""
    return respx.get(url).mock(return_value=httpx.Response(200, json={"data": []}))


@respx.mock
def test_chilquinta_consulta_por_get():
    """Un GET, no un POST: el mock por método es la aserción.

    `respx.get` sólo empareja peticiones GET, así que si el collector volviera a
    POST este test no encontraría ruta y fallaría por «no mock».
    """
    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert ruta.called
    assert ruta.calls[0].request.method == "GET"


@respx.mock
def test_las_tres_cabeceras_de_la_consulta_van_completas():
    """Acá las cabeceras son los parámetros: si falta una, la consulta es otra."""
    from app.collectors.power.chilquinta_worker import (
        ALL_ORDERS,
        API_KEY_HEADER,
        COMPANY_HEADER,
        ORDER_HEADER,
    )

    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    cabeceras = ruta.calls[0].request.headers
    assert cabeceras[API_KEY_HEADER] == API_KEY_FALSA
    assert cabeceras[COMPANY_HEADER] == "006"
    assert cabeceras[ORDER_HEADER] == ALL_ORDERS


@respx.mock
def test_la_cabecera_de_orden_dice_lo_mismo_que_la_url():
    """`X-Orden-Buscada` y `?orderId=` tienen que coincidir.

    No sabemos cuál de las dos lee el backend —el 400 sólo delató la de la URL—,
    así que la única postura defendible es que digan lo mismo. Si divergieran, la
    consulta significaría una cosa para el gateway y otra para la aplicación, y
    el día que eso importe el síntoma sería una lista vacía, no un error.

    Se comprueba contra la URL saliente y no contra la constante para que el test
    siga sirviendo si mañana el centinela vuelve a cambiar de valor.
    """
    from app.collectors.power.chilquinta_worker import ORDER_HEADER, ORDER_PARAM

    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    peticion = ruta.calls[0].request
    assert ORDER_HEADER in peticion.headers, "omitir la cabecera no es mandarla"
    assert peticion.headers[ORDER_HEADER] == peticion.url.params[ORDER_PARAM]


def test_el_centinela_de_todas_las_ordenes_no_puede_ser_vacio():
    """`ALL_ORDERS = ""` es exactamente el bug que produjo el 400 del gateway.

    El validador del API Gateway comprueba los parámetros obligatorios antes de
    enrutar y trata un valor de longitud cero como un parámetro inexistente:
    `?orderId=` recibe el mismo `Missing required request parameters: [orderId]`
    que no mandarlo. Como `""` es lo que uno escribiría intuitivamente para "sin
    filtro", este test fija la decisión donde se toma.
    """
    from app.collectors.power.chilquinta_worker import ALL_ORDERS

    assert ALL_ORDERS == "0"
    assert len(ALL_ORDERS) > 0, "un valor vacío es, para el gateway, un parámetro ausente"


@respx.mock
def test_el_orderid_viaja_en_la_url_y_con_valor():
    """`?orderId=0` es obligatorio, y el `0` es tan obligatorio como la clave.

    Sin el parámetro, el endpoint responde
    `400 {"error":"Missing required request parameters: [orderId]"}` — y lo hace
    aunque `X-Orden-Buscada` esté presente, así que la cabecera no lo sustituye.
    Con el parámetro pero vacío responde exactamente lo mismo: el validador del
    API Gateway cuenta un valor de longitud cero como una ausencia.

    Se comprueba sobre la URL cruda y no sobre `params` porque el riesgo real
    está en el camino: entre `request_url()` y el socket hay tres capas que
    pueden comerse una query, y el fallo aparecería como un 400 en producción y
    no acá — que es justo lo que pasó dos veces.

    La aserción es sobre la URL saliente **entera** y no sólo sobre la query: lo
    que falló en producción fue la dirección completa, y así este test se entera
    también si mañana el `?orderId=0` sobrevive pero cambian el host o la ruta.
    """
    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert str(ruta.calls[0].request.url) == (
        "https://mapainterrupciones.chilquinta.cl/obtieneImage?orderId=0"
    )
    assert ruta.calls[0].request.url.query == b"orderId=0"


@respx.mock
def test_la_peticion_no_lleva_cuerpo():
    """Es un GET: los filtros van en cabeceras y query string, nunca en el cuerpo.

    Un GET con cuerpo no rompería nada visible —httpx lo manda igual— pero varios
    proxies y CDN lo descartan sin avisar, y sería una pista falsa para quien
    depure esto dentro de seis meses.
    """
    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert ruta.calls[0].request.content == b""


def test_el_orderid_lo_garantiza_la_url_y_no_el_payload():
    """Quién sostiene el parámetro obligatorio, y por qué no es el payload.

    Estuvo en `request_payload()` y no sobrevivió a producción: el camino del
    payload tiene dos coladores —`load_records()` descarta un diccionario falsy,
    y `request_response` sólo pasa `params` a httpx cuando tiene claves, porque
    httpx *reemplaza* la query de la URL con lo que reciba—. La URL, en cambio,
    viaja tal cual.

    De ahí las dos aserciones: el payload tiene que estar vacío (para que httpx
    no pise la query) y la URL efectiva tiene que traer el parámetro.
    """
    instancia = collector()
    instancia.url = CHILQUINTA_URL

    assert instancia.request_payload() == {}, (
        "un payload con claves hace que httpx reemplace la query de la URL"
    )
    assert instancia.request_url() == f"{CHILQUINTA_URL}?orderId=0"


def test_la_url_efectiva_es_exactamente_la_del_endpoint():
    """La dirección literal que espera Chilquinta, carácter por carácter.

    Se fija así de duro a propósito: los dos 400 de producción fueron exactamente
    la diferencia entre esta cadena y dos variantes suyas —sin `?orderId=` la
    primera vez, con el parámetro vacío la segunda—.
    """
    instancia = collector()
    # La URL configurada, no la constante del test: así lo que se comprueba es la
    # cadena que saldría en producción, `.env` incluido.
    instancia.url = settings.CHILQUINTA_API_URL

    assert instancia.request_url() == (
        "https://mapainterrupciones.chilquinta.cl/obtieneImage?orderId=0"
    )


def test_un_orderid_ya_presente_y_con_valor_se_respeta():
    """Idempotencia: el `.env` puede traerlo y el worker lo añade igual.

    Si las dos capas escribieran, saldría `?orderId=0&orderId=0` y no hay motivo
    para averiguar cómo interpreta el backend un parámetro repetido.

    Un valor distinto del centinela también se respeta: es alguien consultando
    una orden concreta a propósito, y este helper no está para opinar sobre eso.
    """
    from app.collectors.power.chilquinta_worker import con_order_id

    assert con_order_id(f"{CHILQUINTA_URL}?orderId=0") == f"{CHILQUINTA_URL}?orderId=0"
    assert (
        con_order_id(f"{CHILQUINTA_URL}?orderId=88231")
        == f"{CHILQUINTA_URL}?orderId=88231"
    )


def test_un_orderid_vacio_en_la_url_se_corrige_en_vez_de_conservarse():
    """El caso que importa: la clave está, pero con el valor que el gateway rechaza.

    Puede llegar de un `.env` "ya arreglado" a mano durante el incidente anterior.
    Conservarlo por parecer intencional reproduce el mismo
    `Missing required request parameters: [orderId]` y el parche parecería no
    haber servido: para el validador, un valor de longitud cero es una ausencia.
    """
    from app.collectors.power.chilquinta_worker import con_order_id

    assert con_order_id(f"{CHILQUINTA_URL}?orderId=") == f"{CHILQUINTA_URL}?orderId=0"


def test_el_orderid_se_suma_a_una_query_existente():
    """Concatenar `"?orderId=0"` a ciegas rompería una URL que ya tenga query.

    `…/obtieneImage?v=2` + `"?orderId=0"` da `…?v=2?orderId=0`, que no es una URL
    válida y que provocaría el mismo 400 que se está evitando. Por eso la URL se
    arma con `urlsplit`/`urlencode` y no pegando cadenas.
    """
    from app.collectors.power.chilquinta_worker import con_order_id

    resultado = con_order_id(f"{CHILQUINTA_URL}?v=2")

    assert resultado == f"{CHILQUINTA_URL}?v=2&orderId=0"


@respx.mock
def test_la_llave_no_viaja_en_la_url():
    """Una credencial en el query string queda escrita en cada log intermedio."""
    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert API_KEY_FALSA not in str(ruta.calls[0].request.url)


@respx.mock
def test_el_user_agent_del_proyecto_sigue_yendo():
    """Consultamos un servidor ajeno: quien lo opere tiene derecho a saber quién es.

    Las cabeceras propias se añaden *sobre* las de la familia, no en lugar de
    ellas.
    """
    ruta = responde_vacio()

    instancia = collector()
    instancia.url = CHILQUINTA_URL
    asyncio.run(instancia.fetch())

    assert (
        ruta.calls[0].request.headers["User-Agent"] == settings.NOMINATIM_USER_AGENT
    )


@respx.mock
def test_el_filtro_espacial_sigue_actuando_por_el_camino_real():
    """Cambiar el transporte no puede aflojar el recorte territorial.

    Es el mismo invariante de siempre —`BoundingBox.contains` sobre
    `settings.region_bbox`, antes de mapear al dominio— comprobado por el camino
    que se usa en producción: GET con cabeceras.
    """
    respx.get(CHILQUINTA_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [registro(), *outside_records()]}
        )
    )

    instancia = collector()
    instancia.url = CHILQUINTA_URL
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
    instancia.url = CHILQUINTA_URL

    assert API_KEY_FALSA not in str(instancia.run_params())
    assert instancia.run_params()["method"] == "GET"


# La guarda del query string —httpx reemplaza la query de la URL cuando recibe
# `params`, aunque venga vacío— dejó de tener dueño en esta capa: Chilquinta
# manda sus filtros en cabeceras y CGE descarga un archivo. Se comprueba donde
# vive, sobre `request_response`, en tests/test_geoservices.py.


def test_las_urls_definitivas_estan_configuradas():
    """La de Chilquinta es la ruta XHR, no la del visor.

    `obtieneImage` devuelve JSON pese al nombre: es ofuscación del frontend. Si
    alguien "corrige" esta URL hacia `/mapas`, llega HTML y el collector falla.
    """
    assert settings.CHILQUINTA_API_URL == (
        "https://mapainterrupciones.chilquinta.cl/obtieneImage"
    )
    assert settings.CHILQUINTA_COD_EMP == "006"
