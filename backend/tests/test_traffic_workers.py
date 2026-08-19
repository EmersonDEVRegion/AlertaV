"""Normalización de los tres workers de accidentes viales.

Se testea `normalize()` —función pura por contrato del proyecto— sobre
instancias creadas con `__new__`, sin pasar por `__init__` y por lo tanto sin
sesión ni configuración. Es la convención que ya usan los tests de CONAF, FIRMS
y SENAPRED.

El rate limiter de Nominatim tiene sus propios tests acá porque no es un detalle
de implementación: es la pieza que evita que la IP del backend termine bloqueada
por un servicio donado, y ese fallo no se recupera con un reintento.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.collectors.nominatim import RateLimiter, build_query
from app.collectors.traffic.bomberos_10_4_worker import (
    Bomberos104Collector,
    Dispatch,
    build_external_id,
    matches_key,
    parse_dispatches,
)
from app.collectors.traffic.transporteinforma_worker import (
    TrafficNotice,
    TransporteInformaCollector,
    extract_streets_via_llm,
    parse_notice,
)
from app.collectors.traffic.waze_worker import (
    WazeCollector,
    parse_alert,
    parse_commune,
)
from app.models.enums import EventSource, EventType, family_of_event

AHORA = datetime.now(UTC)


def waze_collector(**overrides) -> WazeCollector:
    """Instancia sin `__init__`: no toca sesión ni settings."""
    collector = WazeCollector.__new__(WazeCollector)
    collector.wanted_types = overrides.get("wanted_types", {"ACCIDENT"})
    return collector


# --- Waze --------------------------------------------------------------------


def alerta_waze(**overrides) -> dict:
    base = {
        "uuid": "abc-123",
        "type": "ACCIDENT",
        "subtype": "ACCIDENT_MAJOR",
        # x = LONGITUD, y = LATITUD.
        "location": {"x": -71.6197, "y": -33.0458},
        "street": "Ruta 68",
        "city": "Valparaíso, Valparaíso",
        "reliability": 7,
        "pubMillis": int(AHORA.timestamp() * 1000),
    }
    base.update(overrides)
    return base


def test_waze_lee_x_como_longitud_e_y_como_latitud():
    """El error clásico del feed de Waze.

    Invertir los ejes deposita todos los accidentes de Valparaíso en el Índico, y
    lo hace sin error: son coordenadas válidas.
    """
    alerta = parse_alert(alerta_waze())

    assert alerta is not None
    assert alerta.lat == pytest.approx(-33.0458)
    assert alerta.lon == pytest.approx(-71.6197)
    # Si se invirtieran, la latitud caería fuera de Chile.
    assert -57.0 < alerta.lat < -17.0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"uuid": "x"},
        {"uuid": "x", "type": "ACCIDENT"},
        {"uuid": "x", "type": "ACCIDENT", "location": {}},
        {"uuid": "x", "type": "ACCIDENT", "location": {"x": None, "y": None}},
        {"uuid": "x", "type": "ACCIDENT", "location": {"x": -71.6, "y": -200.0}},
        "no soy un objeto",
    ],
)
def test_waze_descarta_alertas_inservibles_sin_reventar(payload):
    """Un feed comunitario trae filas incompletas de rutina.

    Perder la corrida entera por una alerta rota sería cambiar un dato faltante
    por doscientos.
    """
    assert parse_alert(payload) is None


def test_waze_solo_emite_accidentes():
    collector = waze_collector()
    registros = [
        parse_alert(alerta_waze(uuid="a", type="ACCIDENT")),
        parse_alert(alerta_waze(uuid="b", type="JAM")),
        parse_alert(alerta_waze(uuid="c", type="ROAD_CLOSED")),
        parse_alert(alerta_waze(uuid="d", type="POLICE")),
    ]

    eventos = collector.normalize([r for r in registros if r])

    assert len(eventos) == 1
    assert eventos[0].external_id == "waze:a"
    assert eventos[0].type is EventType.ACCIDENT
    assert eventos[0].source is EventSource.WAZE
    assert family_of_event(eventos[0].type) == "traffic"


def test_waze_descarta_reportes_viejos():
    """Waze mantiene vivas las alertas mientras se las confirme.

    Una de hace horas ya no describe el tránsito de ahora; meterla al motor la
    haría corroborar un accidente probablemente ya despejado.
    """
    collector = waze_collector()
    viejo = AHORA - timedelta(hours=6)
    registros = [
        parse_alert(alerta_waze(uuid="viejo", pubMillis=int(viejo.timestamp() * 1000))),
        parse_alert(alerta_waze(uuid="nuevo")),
    ]

    eventos = collector.normalize([r for r in registros if r])

    assert [evento.external_id for evento in eventos] == ["waze:nuevo"]


def test_waze_usa_la_confianza_de_su_capa():
    eventos = waze_collector().normalize([parse_alert(alerta_waze())])
    assert eventos[0].confidence == pytest.approx(0.40)


def test_waze_deja_la_comuna_donde_el_motor_la_busca():
    """`EventCreate` no tiene campo `commune`: va por `raw_data`.

    Y va sólo la comuna, no "Comuna, Región": el Paso B compara nombres
    normalizados y "valparaiso, valparaiso" no coincide con ninguno.
    """
    eventos = waze_collector().normalize([parse_alert(alerta_waze())])
    assert eventos[0].raw_data["comuna"] == "Valparaíso"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Valparaíso, Valparaíso", "Valparaíso"),
        ("Viña del Mar", "Viña del Mar"),
        (None, None),
        ("", None),
    ],
)
def test_waze_parse_commune(entrada, esperado):
    assert parse_commune(entrada) == esperado


def test_waze_el_id_externo_es_estable():
    """Idempotencia: releer el feed cada 5 min no puede duplicar el accidente."""
    primera = waze_collector().normalize([parse_alert(alerta_waze())])
    segunda = waze_collector().normalize([parse_alert(alerta_waze())])
    assert primera[0].external_id == segunda[0].external_id


# --- Bomberos 10-4 -----------------------------------------------------------

HTML_PORTAL = """
<table>
  <tr><th>Clave</th><th>Dirección</th><th>Fecha</th></tr>
  <tr><td>10-4</td><td>Ruta 68 km 42</td><td>19-08-2026 14:30</td></tr>
  <tr><td>10-1</td><td>Cerro Barón s/n</td><td>19-08-2026 14:35</td></tr>
  <tr><td>10-40</td><td>Av. Brasil 1200</td><td>19-08-2026 14:40</td></tr>
</table>
"""


def test_bomberos_extrae_solo_la_clave_pedida():
    despachos = parse_dispatches(HTML_PORTAL, ["10-4"])

    assert len(despachos) == 1
    assert despachos[0].key == "10-4"
    assert "Ruta 68" in (despachos[0].address or "")


def test_bomberos_no_confunde_10_4_con_10_40():
    """El bug que la comparación ingenua por substring produce.

    "10-40" contiene "10-4"; sin la guarda de dígitos, un despacho por emanación
    de gas entraría al sistema como rescate vehicular.
    """
    assert matches_key("clave 10-40 en Av. Brasil", ["10-4"]) is None
    assert matches_key("clave 10-4 en Ruta 68", ["10-4"]) == "10-4"


def test_bomberos_extrae_la_fecha_declarada():
    despachos = parse_dispatches(HTML_PORTAL, ["10-4"])
    assert despachos[0].occurred_at is not None
    assert despachos[0].occurred_at.day == 19
    assert despachos[0].occurred_at.month == 8


def test_bomberos_emite_accidente_confirmado_sin_coordenadas():
    """Una 10-4 aporta certeza, no ubicación. Ver el docstring del worker."""
    collector = Bomberos104Collector.__new__(Bomberos104Collector)
    eventos = collector.normalize(parse_dispatches(HTML_PORTAL, ["10-4"]))

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.type is EventType.ACCIDENT
    assert evento.source is EventSource.BOMBEROS
    assert evento.confidence == pytest.approx(1.0)
    assert evento.lat is None and evento.lon is None
    assert evento.raw_data["_bomberos"]["direccion"] == "Ruta 68 km 42"


def test_bomberos_el_id_externo_es_determinista_y_discrimina():
    a = Dispatch(key="10-4", address="Ruta 68 km 42", occurred_at=AHORA, commune=None, raw_text="x")
    b = Dispatch(key="10-4", address="Ruta 68 km 42", occurred_at=AHORA, commune=None, raw_text="OTRO texto")
    c = Dispatch(key="10-4", address="Ruta 68 km 90", occurred_at=AHORA, commune=None, raw_text="x")

    # El texto de la fila cambia entre lecturas (contadores de unidades en
    # camino); incluirlo convertiría cada refresco en un despacho nuevo.
    assert build_external_id(a) == build_external_id(b)
    assert build_external_id(a) != build_external_id(c)


def test_bomberos_html_sin_filas_no_produce_despachos():
    assert parse_dispatches("<html><body>Sin novedades</body></html>", ["10-4"]) == []


# --- Transporte Informa: Paso A (extracción) ---------------------------------


@pytest.mark.parametrize(
    ("aviso", "calle", "transversal", "ciudad"),
    [
        (
            "Accidente vehicular en Av. España con Uno Norte, Viña del Mar. "
            "Tránsito lento hacia el poniente.",
            "Av. España",
            "Uno Norte",
            "Viña del Mar",
        ),
        (
            "Colisión en Avenida Argentina esquina Pedro Montt, Valparaíso.",
            "Avenida Argentina",
            "Pedro Montt",
            "Valparaíso",
        ),
        (
            "Choque múltiple en Ruta 68. Se recomienda precaución.",
            "Ruta 68",
            None,
            None,
        ),
    ],
)
def test_extraccion_devuelve_diccionario_limpio(aviso, calle, transversal, ciudad):
    resultado = extract_streets_via_llm(aviso)

    assert resultado["street"] == calle
    assert resultado["cross_street"] == transversal
    assert resultado["city"] == ciudad
    assert resultado["is_accident"] is True
    assert resultado["mode"] == "mock"


@pytest.mark.parametrize(
    ("aviso", "calle"),
    [
        ("Accidente en Av. España, Valparaíso.", "Av. España"),
        ("Choque en Avda. Argentina, Valparaíso.", "Avda. Argentina"),
        ("Colisión en Gral. Velásquez, Quilpué.", "Gral. Velásquez"),
        ("Volcamiento en Pje. Los Aromos, Limache.", "Pje. Los Aromos"),
    ],
)
def test_extraccion_no_corta_en_el_punto_de_las_abreviaturas(aviso, calle):
    """Regresión: `_PLACE_STOP` cortaba "Av. España" en "Av".

    Casi toda calle chilena se escribe abreviada, así que el defecto truncaba la
    mayoría de los avisos — y de la peor manera: la extracción "funcionaba",
    devolvía una calle, y Nominatim geocodificaba esa calle inexistente a
    cualquier cosa o a nada. El fallo no se veía en ninguna métrica.
    """
    assert extract_streets_via_llm(aviso)["street"] == calle


def test_extraccion_sigue_cortando_en_el_fin_de_frase_real():
    """La protección de abreviaturas no puede desactivar el corte narrativo."""
    resultado = extract_streets_via_llm(
        "Accidente en Ruta 68. Tránsito lento hacia Casablanca."
    )
    assert resultado["street"] == "Ruta 68"


def test_extraccion_distingue_avisos_que_no_son_siniestros():
    """El MTT publica cortes programados y desvíos: no son accidentes."""
    resultado = extract_streets_via_llm(
        "Corte programado en Av. Alemania por obras, Valparaíso."
    )
    assert resultado["is_accident"] is False


def test_extraccion_prefiere_none_antes_que_adivinar():
    """Una calle inventada produce un punto plausible y falso.

    Es peor que un aviso sin ubicación: el segundo se ve, el primero no.
    """
    for aviso in ("", "   ", "Accidente vehicular con dos lesionados."):
        resultado = extract_streets_via_llm(aviso)
        assert resultado["street"] is None


def test_extraccion_nunca_devuelve_coordenadas():
    """Contrato con el LLB: inferir lat/lon es trabajo de Nominatim, que es auditable."""
    resultado = extract_streets_via_llm(
        "Accidente en Av. España con Uno Norte, Viña del Mar."
    )
    assert "lat" not in resultado and "lon" not in resultado


def test_extraccion_respeta_el_contrato_de_claves():
    esperadas = {"street", "cross_street", "city", "region", "is_accident", "mode"}
    assert set(extract_streets_via_llm("Accidente en Ruta 68.")) == esperadas


# --- Transporte Informa: Paso B (consulta a Nominatim) -----------------------


def test_build_query_arma_la_interseccion():
    consulta = build_query(
        {
            "street": "Av. España",
            "cross_street": "Uno Norte",
            "city": "Viña del Mar",
            "region": "Región de Valparaíso",
        }
    )
    assert consulta == "Av. España y Uno Norte, Viña del Mar, Región de Valparaíso"


def test_build_query_sin_calle_no_consulta():
    """Buscar sólo por ciudad devolvería el centroide comunal.

    Como ubicación de un accidente eso es peor que no tener ubicación: parece un
    dato y no lo es.
    """
    assert build_query({"street": None, "city": "Valparaíso"}) is None


# --- Transporte Informa: normalización ---------------------------------------


def test_mtt_normaliza_con_y_sin_geocodificacion():
    from app.collectors.nominatim import GeocodeResult

    collector = TransporteInformaCollector.__new__(TransporteInformaCollector)
    aviso = TrafficNotice(
        notice_id="42",
        text="Accidente en Av. España con Uno Norte, Viña del Mar.",
        published_at=AHORA,
        raw={"id": "42"},
    )
    extraccion = extract_streets_via_llm(aviso.text)
    punto = GeocodeResult(
        lat=-33.0245, lon=-71.5518, display_name="Av. España, Viña del Mar",
        importance=0.42, query="Av. España y Uno Norte, Viña del Mar",
    )

    con_punto, sin_punto = collector.normalize(
        [(aviso, extraccion, punto), (aviso, extraccion, None)]
    )

    assert con_punto.lat == pytest.approx(-33.0245)
    assert con_punto.raw_data["_geocoding"]["importance"] == pytest.approx(0.42)
    assert con_punto.confidence == pytest.approx(0.80)
    assert con_punto.type is EventType.ACCIDENT

    # Sin coordenadas la señal se registra igual: no entra al Paso A, pero un
    # accidente confirmado por el MTT no se descarta porque OSM no conozca la
    # esquina.
    assert sin_punto.lat is None
    assert sin_punto.raw_data["_geocoding"] is None


def test_mtt_deja_los_dos_pasos_separados_y_auditables():
    """Si mañana un punto está mal, esto dice cuál de los dos pasos falló."""
    from app.collectors.nominatim import GeocodeResult

    collector = TransporteInformaCollector.__new__(TransporteInformaCollector)
    aviso = TrafficNotice(notice_id="1", text="Choque en Ruta 68.", published_at=AHORA)
    evento = collector.normalize(
        [(aviso, extract_streets_via_llm(aviso.text), GeocodeResult(lat=-33.0, lon=-71.5))]
    )[0]

    assert evento.raw_data["_extraction"]["mode"] == "mock"
    assert evento.raw_data["_geocoding"]["provider"] == "nominatim"


def test_mtt_parse_notice_acepta_los_alias_del_feed():
    assert parse_notice({"titulo": "Accidente en Ruta 68", "id": "9"}).notice_id == "9"
    assert parse_notice({"descripcion": "Choque"}).notice_id  # hash de respaldo
    assert parse_notice({"id": "9"}) is None  # sin texto no hay aviso
    assert parse_notice("texto suelto") is None


# --- Rate limiter de Nominatim -----------------------------------------------


def test_rate_limiter_espacia_las_llamadas():
    """La garantía que evita que bloqueen la IP del backend."""

    async def escenario() -> float:
        limiter = RateLimiter(min_interval=0.20)
        inicio = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        return time.monotonic() - inicio

    transcurrido = asyncio.run(escenario())
    # Tres adquisiciones = dos esperas. La primera no espera.
    assert transcurrido >= 0.40


def test_rate_limiter_serializa_corrutinas_concurrentes():
    """Sin el lock, N tareas leerían el mismo `_last_call` y saldrían juntas.

    Es el modo de falla que importa: el limitador *parece* funcionar en pruebas
    secuenciales y deja pasar la ráfaga justo cuando hay concurrencia.
    """

    async def escenario() -> list[float]:
        limiter = RateLimiter(min_interval=0.15)
        inicio = time.monotonic()
        marcas: list[float] = []

        async def tarea() -> None:
            await limiter.acquire()
            marcas.append(time.monotonic() - inicio)

        await asyncio.gather(*(tarea() for _ in range(4)))
        return sorted(marcas)

    marcas = asyncio.run(escenario())
    for anterior, siguiente in pairwise(marcas):
        assert siguiente - anterior >= 0.14, f"llamadas demasiado juntas: {marcas}"


def test_rate_limiter_no_espera_la_primera_vez():
    async def escenario() -> float:
        return await RateLimiter(min_interval=5.0).acquire()

    assert asyncio.run(escenario()) == 0.0
