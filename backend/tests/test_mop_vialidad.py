"""Emergencias viales del MOP.

El sesgo de estos tests: casi todos fijan una decisión que, de invertirse, NO
produciría un error visible. Un `f=geojson` devuelve cuerpo vacío y no un 400;
un `REGION LIKE '%Valpara%'` devuelve cero filas y no una excepción; un
`road_closure` que empezara a correlacionar produciría incidentes perfectamente
razonables. Todos esos fallos se ven, desde fuera, como «hoy no hay emergencias
viales» o como «el sistema funciona» — y por eso hay que sujetarlos con un test
en vez de con un comentario.

Los datos son la respuesta real del servicio, capturada el 2026-09-01.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.mop.vialidad_parser import (
    GRAVEDAD_ORDEN,
    TRANSITO_ORDEN,
    build_text,
    from_epoch_millis,
    parse_emergency,
    parse_features,
    severity_rank,
    summarise,
)
from app.collectors.mop.vialidad_worker import (
    MOP_CONFIDENCE,
    OUT_FIELDS,
    REGION_CODE,
    MopVialidadCollector,
)
from app.core.exceptions import CollectorError
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    SOURCE_BASE_CONFIDENCE,
    EventSource,
    EventType,
)

URL = (
    "https://rest-sit.mop.gob.cl/arcgis/rest/services" "/VIALIDAD/Emergencias_Vialidad/MapServer/0"
)

#: Registro literal del servicio. Socavación en la ruta a Catemu.
FILA_REAL = {
    "attributes": {
        "CORRELATIVO": 14928,
        "ESTADO": "INFORMADO",
        "TRANSITO": "No Operativo",
        "NIVEL_DE_GRAVEDAD": "Grave",
        "RESTRICCION": None,
        "NOMBRE_CAMINO": (
            "Cruce F-301-E (Santa Isabel) - Los Corrales - Catemu "
            "(Cruce Calle Ignacio Carrera Pinto)"
        ),
        "ROL": "E-317",
        "RESUMEN": "Socavación de Calzada",
        "FECHA_EMERGENCIA": 1784350800000,
        "REGION": "05",
    },
    # x = LONGITUD, y = LATITUD.
    "geometry": {"x": -70.979612233999944, "y": -32.766434473140819},
}


def respuesta(*filas) -> dict:
    return {
        "displayFieldName": "ESTADO",
        "geometryType": "esriGeometryPoint",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [{"name": "ESTADO", "type": "esriFieldTypeString"}],
        "features": list(filas),
    }


def fila(**overrides) -> dict:
    base = {
        "attributes": dict(FILA_REAL["attributes"]),
        "geometry": dict(FILA_REAL["geometry"]),
    }
    base["attributes"].update(overrides.pop("attributes", {}))
    base.update(overrides)
    return base


def collector() -> MopVialidadCollector:
    """Instancia sin `__init__`: no toca sesión ni settings."""
    instancia = MopVialidadCollector.__new__(MopVialidadCollector)
    instancia.url = f"{URL}/query"
    return instancia


# --- El filtro regional: el error que no produce error -----------------------


def test_el_filtro_usa_el_codigo_de_region_y_no_el_nombre():
    """`REGION` es '05', no 'Valparaíso'. Comprobado contra el servicio real.

    Filtrar por nombre devuelve `features: []` — sin error, sin aviso—, que se
    lee en el mapa como «no hay emergencias viales en la región». Es el fallo
    silencioso exacto que este proyecto persigue en todas sus fuentes, y por eso
    la constante está fijada acá y no sólo comentada.
    """
    assert REGION_CODE == "05"

    where = collector().query_params()["where"]
    assert where == "REGION='05'"
    assert "Valpara" not in where, "el nombre de la región no filtra nada"


def test_el_filtro_viaja_al_servidor_y_no_se_aplica_en_python():
    """Son 30 filas de la V Región contra más de mil del país.

    Traerse el país para descartarlo acá gastaría ancho de banda ajeno para
    hacer peor el mismo trabajo, y encima chocaría con el tope de 1000 del
    servicio, que no pagina.
    """
    assert "where" in collector().query_params()


# --- El formato: el otro error que devuelve vacío ----------------------------


def test_pide_json_y_no_geojson():
    """Este MapServer declara 'JSON, AMF'. Ante `f=geojson` responde SIN cuerpo.

    Es la razón por la que este collector no reutiliza `ArcGisFeatureClient`,
    que pide geojson a propósito. Si alguien «unifica» ambos caminos, esto falla
    acá y no en producción tres semanas después.
    """
    assert collector().query_params()["f"] == "json"


def test_un_cuerpo_vacio_menciona_el_formato():
    """El diagnóstico tiene que apuntar a la causa probable, no al síntoma."""
    with pytest.raises(CollectorError, match="vacío"):
        parse_features(None, origin="mop_vialidad")

    with pytest.raises(CollectorError, match="geojson"):
        parse_features("", origin="mop_vialidad")


def test_pide_la_reproyeccion_al_servidor():
    """`outSR=4326`: la capa está en SIRGAS-Chile (5360), que NO es idéntico.

    Se comprobó pidiendo la misma fila con y sin `outSR`: la latitud cambia en
    el sexto decimal. Es poco, y es precisamente el tipo de diferencia que nadie
    detecta mirando el mapa. El servidor sabe hacer la transformación; asumir
    que «es lo mismo» funciona hasta que alguien mide.
    """
    assert collector().query_params()["outSR"] == 4326


# --- Geometría ---------------------------------------------------------------


def test_lee_x_como_longitud_e_y_como_latitud():
    """La misma trampa de Waze, y con el mismo desenlace si se invierte.

    Con lat/lon cambiados, las emergencias de Valparaíso caen en el Índico. No
    revienta nada: el mapa simplemente queda vacío en la región.
    """
    emergencia = parse_emergency(FILA_REAL)

    assert emergencia is not None
    assert emergencia.lon == pytest.approx(-70.9796, abs=1e-3)
    assert emergencia.lat == pytest.approx(-32.7664, abs=1e-3)
    assert emergencia.lat < 0 and emergencia.lon < 0, "Chile, no el Índico"


@pytest.mark.parametrize(
    "payload",
    [
        {"attributes": {"CORRELATIVO": 1}},  # sin geometría
        {"geometry": {"x": -71.6, "y": -33.0}},  # sin atributos
        fila(geometry={"x": None, "y": -33.0}),
        fila(geometry={"x": -71.6, "y": 999.0}),  # fuera de rango
        fila(attributes={"CORRELATIVO": None}),  # sin identificador estable
        "no soy un feature",
    ],
)
def test_descarta_filas_inservibles_sin_reventar(payload):
    """Una fila mocha no puede costar las otras 29.

    Devuelve None y el collector las cuenta; lanzar acá cambiaría un dato
    faltante por treinta.
    """
    assert parse_emergency(payload) is None


def test_sin_correlativo_no_hay_idempotencia_posible():
    """Sin identificador estable cada corrida insertaría la misma emergencia.

    Con cadencia horaria eso son 24 filas duplicadas al día por emergencia.
    Mejor perder el registro.
    """
    assert parse_emergency(fila(attributes={"CORRELATIVO": None})) is None


def test_el_correlativo_se_normaliza_a_entero():
    """Llega como Double. `14928.0` y `14928` tienen que dar el MISMO id.

    Si el servidor cambiara de representación, un `str(14928.0)` produciría
    `mop:14928.0`, el upsert no reconocería la fila y duplicaría la emergencia.
    """
    desde_float = parse_emergency(fila(attributes={"CORRELATIVO": 14928.0}))
    desde_int = parse_emergency(fila(attributes={"CORRELATIVO": 14928}))

    assert desde_float is not None and desde_int is not None
    assert desde_float.correlativo == desde_int.correlativo == "14928"


# --- Fechas ------------------------------------------------------------------


def test_las_fechas_son_milisegundos_y_no_segundos():
    """`esriFieldTypeDate` viene en ms. Leerlo como s manda todo a 1970."""
    momento = from_epoch_millis(1784350800000)

    assert momento is not None
    assert momento.tzinfo is not None
    assert momento.year == 2026
    assert momento == datetime.fromtimestamp(1784350800, tz=UTC)


@pytest.mark.parametrize("valor", [None, 0, -1, "", "no es fecha", 17843508000000000])
def test_una_fecha_absurda_vale_menos_que_un_none(valor):
    """Un timestamp del año 58000 ordena mal el histórico y nadie lo mira.

    `None` es honesto y el collector lo sustituye por la hora de la corrida.
    """
    assert from_epoch_millis(valor) is None


def test_sin_fecha_de_emergencia_usa_la_hora_de_la_corrida():
    eventos = collector().normalize([parse_emergency(fila(attributes={"FECHA_EMERGENCIA": None}))])

    assert len(eventos) == 1
    assert (datetime.now(UTC) - eventos[0].timestamp).total_seconds() < 60


# --- La decisión central: contexto, no evidencia -----------------------------


def test_emite_road_closure_y_nunca_accident():
    """Si esto cambiara a `accident`, el sistema seguiría «funcionando».

    Produciría incidentes de tránsito perfectamente razonables a partir de
    socavaciones de hace tres semanas. Nadie lo notaría: por eso el test.
    """
    eventos = collector().normalize([parse_emergency(FILA_REAL)])

    assert eventos[0].type is EventType.ROAD_CLOSURE
    assert eventos[0].type is not EventType.ACCIDENT


def test_lo_que_emite_esta_fuera_del_motor_de_correlacion():
    """La garantía de aislamiento, verificada contra la tabla real y no supuesta."""
    assert EventType.ROAD_CLOSURE not in CORRELATABLE_EVENT_TYPES


def test_la_confianza_es_cero_en_las_dos_tablas():
    """Y son dos tablas distintas que tienen que coincidir.

    `SOURCE_BASE_CONFIDENCE` es la línea base con que entra la señal; el
    `confidence` del evento es lo que se escribe en `raw_events`. Un 0.0 en una
    y un valor por defecto en la otra dejaría a la capa aportando peso sin que
    nadie lo hubiera decidido.
    """
    assert MOP_CONFIDENCE == 0.0
    assert SOURCE_BASE_CONFIDENCE[EventSource.MOP] == 0.0

    eventos = collector().normalize([parse_emergency(FILA_REAL)])
    assert eventos[0].confidence == 0.0


def test_la_regla_de_correlacion_no_regala_corroboracion():
    """Hoy no se ejecuta —road_closure no correlaciona— y por eso importa.

    Está escrita para el día en que alguien decida emitir algo correlacionable
    desde el MOP: sin esta entrada heredaría `DEFAULT_RULE` (0.15–0.30) y una
    ruta dañada empezaría a confirmar choques en silencio.
    """
    from app.services.correlation.confidence import DEFAULT_RULE, RULES

    regla = RULES[EventSource.MOP]

    assert regla.max_weight == 0.0
    assert regla is not DEFAULT_RULE


def test_no_descarta_por_antiguedad():
    """Las emergencias arrastran SEMANAS y siguen vigentes.

    El servicio publica sólo las activas, así que aparecer en la respuesta *es*
    la señal de que la ruta sigue dañada. Aplicar el filtro de antigüedad de
    Waze o del MTT vaciaría la capa entera.
    """
    hace_tres_meses = int((datetime.now(UTC).timestamp() - 90 * 86400) * 1000)
    eventos = collector().normalize(
        [parse_emergency(fila(attributes={"FECHA_EMERGENCIA": hace_tres_meses}))]
    )

    assert len(eventos) == 1, "una emergencia de hace tres meses sigue vigente"


# --- Idempotencia ------------------------------------------------------------


def test_el_id_externo_es_estable_entre_corridas():
    primera = collector().normalize([parse_emergency(FILA_REAL)])
    segunda = collector().normalize([parse_emergency(FILA_REAL)])

    assert primera[0].external_id == segunda[0].external_id == "mop:14928"


# --- Sobre de error dentro de un HTTP 200 ------------------------------------


def test_un_error_de_arcgis_con_http_200_no_pasa_por_datos():
    """ArcGIS responde 200 con `{"error": …}` ante una consulta inválida.

    Sin esta comprobación, `features` faltaría y el mensaje hablaría de claves
    ausentes en vez de citar lo que el servidor dijo.

    Este payload es la razón por la que `detect_service_error` **no basta** acá:
    su guarda descarta todo cuerpo que contenga una lista —porque un volcado de
    datos siempre trae la suya— y el `details` de ArcGIS es precisamente una
    lista. Hizo falta anteponer `raise_if_service_error`, que lee la forma
    anidada. Se descubrió con este test, no en producción.
    """
    payload = {
        "error": {
            "code": 400,
            "message": "Unable to complete operation.",
            "details": ["Invalid field: REGIONN"],
        }
    }

    with pytest.raises(CollectorError, match="Unable to complete operation"):
        parse_features(payload, origin="mop_vialidad")


def test_el_detector_plano_por_si_solo_no_ve_el_error_de_arcgis():
    """Fija la razón de tener DOS detectores, para que nadie borre uno.

    Si alguien «simplifica» `parse_features` quitando
    `raise_if_service_error`, este test explica en una línea qué se rompe: el
    error anidado pasaría por datos y la corrida diría `success` con cero
    emergencias.
    """
    from app.collectors.geoservices import detect_service_error

    anidado = {"error": {"code": 400, "message": "Boom", "details": ["x"]}}

    assert detect_service_error(anidado) is None, "la guarda de listas lo deja pasar"


def test_features_ausente_no_es_lo_mismo_que_features_vacio():
    """Dos cosas que un solo camino confundiría.

    Vacío es «no hay emergencias», un estado normal. Ausente es «esto no es la
    respuesta de una consulta», y tiene que levantar.
    """
    assert parse_features(respuesta(), origin="mop") == []

    with pytest.raises(CollectorError, match="features"):
        parse_features({"displayFieldName": "ESTADO"}, origin="mop")


def test_features_de_otro_tipo_levanta():
    with pytest.raises(CollectorError, match="lista"):
        parse_features({"features": {"no": "soy una lista"}}, origin="mop")


# --- Escalas y texto ---------------------------------------------------------


def test_la_severidad_ordena_corte_por_encima_de_gravedad():
    """Una ruta cortada por algo leve estorba más que una abierta muy grave.

    Es la razón de combinar las dos escalas del MOP en un número: la UI no puede
    ordenar por «gravedad» sola sin poner arriba rutas por las que sí se pasa.
    """
    cortada_leve = parse_emergency(
        fila(attributes={"TRANSITO": "No Operativo", "NIVEL_DE_GRAVEDAD": "Leve"})
    )
    abierta_muy_grave = parse_emergency(
        fila(attributes={"TRANSITO": "Operativo", "NIVEL_DE_GRAVEDAD": "Muy Grave"})
    )

    assert severity_rank(cortada_leve) > severity_rank(abierta_muy_grave)


def test_las_escalas_son_las_que_publica_vialidad():
    """Verificadas contra la respuesta real: no son campos libres."""
    assert TRANSITO_ORDEN == ("Operativo", "Parcialmente Operativo", "No Operativo")
    assert GRAVEDAD_ORDEN == ("Leve", "Moderado", "Grave", "Muy Grave")


def test_un_transito_desconocido_no_revienta_el_orden():
    """Si el MOP añade un cuarto estado, la capa sigue dibujándose."""
    rara = parse_emergency(fila(attributes={"TRANSITO": "En Evaluación"}))

    assert severity_rank(rara) >= 0


def test_sin_dato_de_transito_se_asume_transitable():
    """Pintar de rojo una ruta por un campo vacío es peor que no pintarla."""
    sin_dato = parse_emergency(fila(attributes={"TRANSITO": None}))

    assert sin_dato.es_transitable is True


def test_los_nulos_no_llegan_como_la_cadena_literal():
    """`RESTRICCION` viene null en 15 de 30 registros reales.

    Un `str(None)` pondría literalmente "None" en la ficha del mapa.
    """
    emergencia = parse_emergency(FILA_REAL)

    assert emergencia.restriccion is None
    assert "None" not in build_text(emergencia)


def test_el_texto_antepone_el_estado_de_la_via():
    """Quien mira el mapa decide si puede pasar, no qué le pasó al pavimento."""
    texto = build_text(parse_emergency(FILA_REAL))

    assert texto.startswith("No Operativo")
    assert "Catemu" in texto
    assert "Socavación de Calzada" in texto
    assert texto.endswith("Vialidad (MOP)")


def test_el_resumen_por_transitabilidad_distingue_cortadas_de_abiertas():
    """«30 emergencias» no permite notar nada. «11 cortadas», sí."""
    conteo = summarise(
        [
            parse_emergency(fila(attributes={"TRANSITO": "No Operativo"})),
            parse_emergency(fila(attributes={"TRANSITO": "No Operativo"})),
            parse_emergency(fila(attributes={"TRANSITO": "Operativo"})),
        ]
    )

    assert conteo == {"No Operativo": 2, "Operativo": 1}


# --- Campos pedidos ----------------------------------------------------------


def test_no_pide_asterisco():
    """La capa trae 40 columnas, entre ellas dos GlobalID y `created_user`.

    Todo eso acabaría en `raw_data` y de ahí en el histórico sin que nadie lo
    lea nunca.
    """
    campos = collector().query_params()["outFields"]

    assert campos != "*"
    assert "created_user" not in campos
    assert "GlobalID" not in campos


@pytest.mark.parametrize(
    "campo", ["CORRELATIVO", "TRANSITO", "NIVEL_DE_GRAVEDAD", "FECHA_EMERGENCIA"]
)
def test_pide_los_campos_de_los_que_depende_el_parser(campo):
    """Quitar uno de estos de OUT_FIELDS no rompe nada: degrada en silencio."""
    assert campo in OUT_FIELDS


# --- El ciclo completo -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_una_corrida_real_produce_senales_de_contexto():
    respx.get(url__startswith=URL).mock(
        return_value=httpx.Response(200, json=respuesta(FILA_REAL, fila()))
    )

    instancia = collector()
    emergencias = await instancia.fetch()
    eventos = instancia.normalize(emergencias)

    assert len(eventos) == 2
    assert all(evento.source is EventSource.MOP for evento in eventos)
    assert all(evento.type is EventType.ROAD_CLOSURE for evento in eventos)
    assert all(evento.confidence == 0.0 for evento in eventos)


@pytest.mark.asyncio
@respx.mock
async def test_cero_emergencias_es_exito_y_no_fallo():
    """Estado normal: no hay rutas dañadas vigentes en la región."""
    respx.get(url__startswith=URL).mock(return_value=httpx.Response(200, json=respuesta()))

    instancia = collector()
    assert await instancia.fetch() == []
    assert instancia.warnings == []


@pytest.mark.asyncio
@respx.mock
async def test_las_filas_descartadas_quedan_contadas_en_un_aviso():
    respx.get(url__startswith=URL).mock(
        return_value=httpx.Response(
            200,
            json=respuesta(FILA_REAL, {"attributes": {"CORRELATIVO": 1}}),
        )
    )

    instancia = collector()
    emergencias = await instancia.fetch()

    assert len(emergencias) == 1
    assert any("1 emergencias sin coordenada" in aviso for aviso in instancia.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_el_error_de_arcgis_llega_al_operador_con_el_mensaje_del_servidor():
    respx.get(url__startswith=URL).mock(
        return_value=httpx.Response(200, json={"error": {"code": 498, "message": "Invalid token."}})
    )

    with pytest.raises(CollectorError, match="Invalid token"):
        await collector().fetch()


# --- Registro ----------------------------------------------------------------


def test_esta_en_rotacion_con_cadencia_horaria():
    """A 5 minutos serían 2016 peticiones por cada dato nuevo del MOP."""
    from app.collectors.registry import available_collectors, collector_class

    assert "mop_vialidad" in available_collectors()
    assert collector_class("mop_vialidad") is MopVialidadCollector
    assert MopVialidadCollector.default_interval_seconds == 3600
