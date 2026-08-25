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
    feed_is_broken,
    find_codes,
    matches_key,
    normalise_code,
    parse_dispatches,
)
from app.collectors.traffic.transporteinforma_worker import (
    TrafficNotice,
    TransporteInformaCollector,
    extract_streets_heuristic,
    looks_like_accident,
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


# --- Bomberos 10-4: reconocimiento de la clave -------------------------------
#
# Es el núcleo del worker y donde un error es más caro: confundir una clave
# manda al mapa un accidente que no ocurrió, u oculta uno que sí.


@pytest.mark.parametrize(
    ("token", "esperado"),
    [
        # Variantes legítimas de la misma clave.
        ("10-4", (10, 4)),
        ("10-0-4", (10, 4)),  # el 0 intermedio es separador de familia
        ("10-4-1", (10, 4, 1)),  # sufijo de subtipo
        ("10.4", (10, 4)),
        ("10 – 4", (10, 4)),  # guion largo del autocorrector
        ("10/4", (10, 4)),
        # Claves distintas: tienen que normalizar distinto.
        ("10-40", (10, 40)),
        ("10-41", (10, 41)),
        ("10-0-1", (10, 1)),
        # No es una clave.
        ("10-4-2026", None),  # una fecha
    ],
)
def test_normalise_code(token, esperado):
    assert normalise_code(token) == esperado


@pytest.mark.parametrize(
    ("aviso", "coincide"),
    [
        ("Clave 10-4 en Ruta 68 km 42", True),
        ("Clave 10-0-4, Av. España con Uno Norte", True),
        ("10-4-1 rescate con víctima atrapada, Quilpué", True),
        ("Despacho 10.4 en Placilla", True),
        ("Se despacha 10 – 4 a Ruta 60 CH", True),
        # Las trampas.
        ("Clave 10-40 emanación de gas, Av. Brasil", False),
        ("Clave 10-41 en Playa Ancha", False),
        ("Clave 10-0-1 incendio estructural", False),
        ("Reporte del 10-4-2026 sin novedades", False),
        ("Carro 104 en tránsito", False),
        ("Unidad 110-4 disponible", False),
        ("Sin claves en este aviso", False),
    ],
)
def test_matches_key_distingue_la_clave_de_sus_impostoras(aviso, coincide):
    """`10-40` contiene `10-4` como substring y NO es la misma clave.

    Con comparación por texto, un despacho por emanación de gas entraría al
    sistema como rescate vehicular. La normalización a tuplas lo hace imposible:
    `(10, 40)` no tiene a `(10, 4)` por prefijo.
    """
    assert (matches_key(aviso, ["10-4"]) is not None) is coincide


def test_el_sufijo_de_subtipo_sigue_siendo_la_misma_clave():
    """`10-4-1` es un rescate con víctima atrapada: el caso más grave.

    Exigir coincidencia exacta lo descartaría justo por ser más específico.
    """
    assert matches_key("10-4-1 en Ruta 68", ["10-4"]) == "10-4"
    assert find_codes("10-4-1 en Ruta 68") == [(10, 4, 1)]


def test_find_codes_ve_todas_las_claves_de_un_aviso():
    codigos = find_codes("Despacho 10-4 y luego 10-0-1 en el mismo sector")
    assert (10, 4) in codigos
    assert (10, 1) in codigos


# --- Bomberos 10-4: lectura del feed RSS -------------------------------------

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Central CBV</title>
    <item>
      <title>Clave 10-4 en Ruta 68 km 42, se despachan unidades</title>
      <description>Rescate vehicular. Personal en el lugar.</description>
      <guid>https://ejemplo.cl/status/1</guid>
      <pubDate>Wed, 19 Aug 2026 14:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Clave 10-0-4 Av. Espa&#241;a con Uno Norte</title>
      <guid>https://ejemplo.cl/status/2</guid>
      <pubDate>Wed, 19 Aug 2026 14:35:00 GMT</pubDate>
    </item>
    <item>
      <title>Clave 10-40 emanaci&#243;n de gas en Av. Brasil</title>
      <guid>https://ejemplo.cl/status/3</guid>
      <pubDate>Wed, 19 Aug 2026 14:40:00 GMT</pubDate>
    </item>
    <item>
      <title>Clave 10-1 incendio estructural, Cerro Bar&#243;n</title>
      <guid>https://ejemplo.cl/status/4</guid>
      <pubDate>Wed, 19 Aug 2026 14:45:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def test_bomberos_extrae_del_rss_solo_la_clave_pedida():
    despachos = parse_dispatches(RSS_FEED, ["10-4"])

    assert len(despachos) == 2, "deben entrar 10-4 y 10-0-4, y sólo esas"
    assert {d.key for d in despachos} == {"10-4"}
    assert "Ruta 68" in (despachos[0].address or "")
    assert "España" in (despachos[1].address or ""), "las entidades XML se decodifican"


def test_bomberos_lee_la_fecha_del_pubdate():
    despacho = parse_dispatches(RSS_FEED, ["10-4"])[0]
    assert despacho.occurred_at is not None
    assert (despacho.occurred_at.day, despacho.occurred_at.month) == (19, 8)
    assert despacho.occurred_at.tzinfo is not None, "debe llegar con zona horaria"


def test_bomberos_mira_titulo_y_descripcion():
    """El puente no es consistente sobre dónde deja el texto completo."""
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item>
        <title>Despacho en curso</title>
        <description>Clave 10-4 en Av. Argentina</description>
        <guid>x1</guid>
      </item></channel></rss>"""
    despachos = parse_dispatches(feed, ["10-4"])
    assert len(despachos) == 1
    assert "Av. Argentina" in despachos[0].address


def test_bomberos_emite_accidente_confirmado_sin_coordenadas():
    """Una 10-4 aporta certeza, no ubicación. Ver el docstring del worker."""
    collector = Bomberos104Collector.__new__(Bomberos104Collector)
    eventos = collector.normalize(parse_dispatches(RSS_FEED, ["10-4"]))

    assert len(eventos) == 2
    evento = eventos[0]
    assert evento.type is EventType.ACCIDENT
    assert evento.source is EventSource.BOMBEROS
    assert evento.confidence == pytest.approx(1.0)
    assert evento.lat is None and evento.lon is None
    assert "Ruta 68" in evento.raw_data["_bomberos"]["direccion"]


def test_bomberos_el_id_externo_sale_del_guid():
    """Idempotencia: releer el feed cada 3 min no puede duplicar el despacho."""
    primera = parse_dispatches(RSS_FEED, ["10-4"])
    segunda = parse_dispatches(RSS_FEED, ["10-4"])
    assert build_external_id(primera[0]) == build_external_id(segunda[0])
    assert build_external_id(primera[0]) != build_external_id(primera[1])


def test_bomberos_sin_guid_cae_a_un_hash_determinista():
    a = Dispatch(key="10-4", address="Ruta 68", occurred_at=AHORA, commune=None,
                 raw_text="Clave 10-4 en Ruta 68", guid=None)
    b = Dispatch(key="10-4", address="Ruta 68", occurred_at=AHORA, commune=None,
                 raw_text="Clave 10-4 en Ruta 68", guid=None)
    c = Dispatch(key="10-4", address="Ruta 60", occurred_at=AHORA, commune=None,
                 raw_text="Clave 10-4 en Ruta 60", guid=None)

    assert build_external_id(a) == build_external_id(b)
    assert build_external_id(a) != build_external_id(c)


def test_bomberos_distingue_feed_vacio_de_feed_roto():
    """Un `len(entries) == 0` confunde dos cosas muy distintas.

    Un feed válido sin novedades es una noche tranquila. Un XML ilegible es
    RSSHub sirviendo una página de error con HTTP 200, y eso necesita a alguien.
    """
    vacio = """<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Central</title></channel></rss>"""
    roto, _ = feed_is_broken(vacio)
    assert roto is False, "un feed válido sin ítems no es un feed roto"

    roto, motivo = feed_is_broken("<html><body>429 Too Many Requests</body></html>")
    assert roto is True
    assert motivo


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
    """La heurística de respaldo, que es lo que corre sin GEMINI_API_KEY."""
    resultado = extract_streets_heuristic(aviso)

    assert resultado is not None
    assert resultado["street_1"] == calle
    assert resultado["street_2"] == transversal
    assert resultado["city"] == ciudad


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
    assert extract_streets_heuristic(aviso)["street_1"] == calle


def test_extraccion_sigue_cortando_en_el_fin_de_frase_real():
    """La protección de abreviaturas no puede desactivar el corte narrativo."""
    resultado = extract_streets_heuristic(
        "Accidente en Ruta 68. Tránsito lento hacia Casablanca."
    )
    assert resultado["street_1"] == "Ruta 68"


def test_extraccion_distingue_avisos_que_no_son_siniestros():
    """El MTT publica cortes programados y desvíos: no son accidentes.

    La clasificación es de `looks_like_accident`, NO del modelo: ver el
    docstring de esa función.
    """
    assert looks_like_accident("Corte programado en Av. Alemania por obras.") is False
    assert looks_like_accident("Accidente vehicular en Av. Alemania.") is True


def test_extraccion_prefiere_none_antes_que_adivinar():
    """Una calle inventada produce un punto plausible y falso.

    Es peor que un aviso sin ubicación: el segundo se ve, el primero no.
    """
    for aviso in ("", "   ", "Accidente vehicular con dos lesionados."):
        assert extract_streets_heuristic(aviso) is None


def test_extraccion_nunca_devuelve_coordenadas():
    """Inferir lat/lon es trabajo de Nominatim, que es verificable y auditable."""
    resultado = extract_streets_heuristic(
        "Accidente en Av. España con Uno Norte, Viña del Mar."
    )
    assert "lat" not in resultado and "lon" not in resultado


def test_extraccion_respeta_el_contrato_de_claves():
    """Las tres claves exactas, ni una más: es lo que `build_query` consume."""
    esperadas = {"street_1", "street_2", "city"}
    assert set(extract_streets_heuristic("Accidente en Ruta 68.")) == esperadas


# --- Transporte Informa: Paso B (consulta a Nominatim) -----------------------


def test_build_query_arma_la_interseccion():
    consulta = build_query(
        {"street_1": "Av. España", "street_2": "Uno Norte", "city": "Viña del Mar"}
    )
    assert consulta == "Av. España y Uno Norte, Viña del Mar, Región de Valparaíso"


def test_build_query_sin_calle_no_consulta():
    """Buscar sólo por ciudad devolvería el centroide comunal.

    Como ubicación de un accidente eso es peor que no tener ubicación: parece un
    dato y no lo es.
    """
    assert build_query({"street_1": None, "city": "Valparaíso"}) is None


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
    extraccion = extract_streets_heuristic(aviso.text)
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
        [(aviso, extract_streets_heuristic(aviso.text), GeocodeResult(lat=-33.0, lon=-71.5))]
    )[0]

    assert evento.raw_data["_extraction"]["mode"] == "heuristic"
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


# --- El rediseño del portal (2026) -------------------------------------------
#
# Transporte Informa se reconstruyó: la región pasó de ruta a subdominio y la
# maqueta de Elementor desapareció. El collector quedó devolviendo cero avisos
# durante días, diciéndolo correctamente —"la estructura del portal cambió"—
# pero sin que nadie lo mirara.
#
# Este HTML está copiado de la captura real del 2026-08-25, iconos incluidos.


PORTAL_NUEVO = """
<div class="card card--state">
  <div class="card--state__header">
    <p><i class="material-symbols-rounded">location_on</i> Av. España</p>
    <p><i class="material-symbols-rounded">explore</i> Zona</p>
  </div>
  <div class="card--state__text">
    <div class="d-lg-flex">
      <p><strong>Categoría:</strong> Incidentes</p>
      <p><strong>07 de agosto de 2026</strong></p>
    </div>
    <p><strong>Descripción</strong> Restricción de pistas en Avenida España
       desde Escuela Industrial hasta Club de Yates en dirección a Viña del Mar</p>
    <a class="btn" href="/estado-de-la-movilidad/restriccion-avenida-espana/">Ver más</a>
  </div>
</div>
"""


def test_los_avisos_del_portal_nuevo_se_encuentran():
    """`div.card--state` es donde vive hoy cada aviso.

    Antes eran `<article>` y `.elementor-widget-container`; el rediseño los hizo
    desaparecer y el collector dejó de encontrar nada.
    """
    from app.collectors.traffic.transporteinforma_worker import parse_notices

    avisos = parse_notices(PORTAL_NUEVO)

    assert len(avisos) == 1
    assert "Avenida España" in avisos[0].text


def test_los_iconos_no_se_cuelan_en_el_texto():
    """La trampa que sólo se ve mirando el DOM, no la página.

    El portal usa Material Symbols, que funcionan por **ligadura**: el nombre del
    icono va como texto dentro de la etiqueta y la fuente lo dibuja. En pantalla
    se ve un pin; en el HTML dice `location_on`, y un `get_text()` normal produce
    «location_on Av. España».

    No es cosmético: ese texto es el que se manda al LLM para extraer calles y el
    que queda archivado en `raw_data`. Un nombre de icono en medio de una
    dirección empeora la geocodificación y se guarda como si lo hubiera escrito
    la fuente.
    """
    from app.collectors.traffic.transporteinforma_worker import parse_notices

    texto = parse_notices(PORTAL_NUEVO)[0].text

    assert "location_on" not in texto
    assert "explore" not in texto
    assert "Av. España" in texto


def test_la_categoria_del_portal_cuenta_como_palabra_clave():
    """El portal nuevo **etiqueta** cada tarjeta, y eso vale más que adivinar.

    `Categoría: Incidentes` es la fuente diciendo de qué habla. No convierte el
    aviso en un siniestro —eso lo decide `is_accident` más adelante— pero sí lo
    distingue del menú de navegación, que es lo que hace esta etapa.
    """
    from app.collectors.traffic.transporteinforma_worker import matched_keywords

    assert "incidente" in matched_keywords("Categoría: Incidentes")
    assert "incidente" in matched_keywords("categoria: incidente")
    assert matched_keywords("Inicio Contacto Preguntas frecuentes") == []
