"""Capa meteorológica: política de umbrales, emparejamiento y aislamiento.

Tres cosas se prueban acá, en orden de importancia:

1. **Que un pronóstico no se convierta en un siniestro.** Es el riesgo real de
   esta capa. El test que más vale de este archivo es el que comprueba que el
   evento sale con `weather_observation` y que ese tipo sigue estando FUERA de
   `CORRELATABLE_EVENT_TYPES`: el día que alguien lo mueva —o cambie el tipo del
   collector a `flood` porque "es más descriptivo"— aparecerán incidentes de
   inundación en el mapa por comunas donde sólo hay un modelo diciendo que va a
   llover.

2. **Que los umbrales digan lo que el docstring promete.** Cada regla se prueba
   aislada: se construye una serie que dispare exactamente una y ninguna más.
   Sin eso, tres reglas con OR son indistinguibles de una.

3. **Que un cambio de forma de la API falle de manera legible** en vez de
   devolver cero comunas con lluvia en silencio, que es el modo de fallo que este
   proyecto persigue en todas sus fuentes.

Las series se arman a mano: `umbrales.py` es puro y `parse_payload` también, así
que casi nada de este archivo necesita red.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.collectors.weather.comunas import (
    COMUNAS_V_REGION,
    Comuna,
    parse_comunas,
    slug,
)
from app.collectors.weather.openmeteo_client import (
    HOURLY_VARIABLES,
    OpenMeteoClient,
    SerieComunal,
    parse_payload,
    parse_serie,
    raise_if_openmeteo_error,
)
from app.collectors.weather.openmeteo_worker import (
    WEATHER_CONFIDENCE,
    WEATHER_KEY,
    OpenMeteoCollector,
    build_external_id,
)
from app.collectors.weather.umbrales import (
    NIVEL_LLUVIA,
    NIVEL_RIESGO,
    NIVEL_RIESGO_ALTO,
    NIVEL_SECO,
    PuntoHorario,
    Umbrales,
    acumulado_maximo,
    describir,
    evaluar,
    piso_horario,
    recortar_ventana,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    SOURCE_BASE_CONFIDENCE,
    CollectorStatus,
    EventSource,
    EventType,
)

API_URL = "https://meteo.test/v1/forecast"

VALPO = Comuna("Valparaíso", -33.0472, -71.6127)
QUILPUE = Comuna("Quilpué", -33.0472, -71.4425)

#: Hora fija de referencia. Todo el módulo trunca a la hora en curso, así que un
#: instante en punto evita que el test dependa del minuto en que corra.
AHORA = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    """Anula el backoff entre reintentos. Ver `test_traffic_resilience`."""
    import app.collectors.geoservices as geoservices

    async def sin_dormir(_seconds: float) -> None:
        return None

    monkeypatch.setattr(geoservices.asyncio, "sleep", sin_dormir)


# --- Dobles de prueba --------------------------------------------------------


class FakeIngestService:
    """Sustituye a `IngestService` para no necesitar base de datos."""

    def __init__(self) -> None:
        self.status: CollectorStatus | None = None
        self.error: str | None = None
        self.eventos: list = []

    async def start_run(self, **_kwargs) -> object:
        return object()

    async def finish_run(
        self, _run, *, status, fetched=0, inserted=0, duplicate=0, error=None
    ) -> None:
        self.status = status
        self.error = error

    async def ingest_batch(self, events):
        self.eventos = list(events)
        return type("Ingest", (), {"inserted": len(events), "duplicated": 0})()


def collector(
    *, comunas=None, umbrales: Umbrales | None = None, url: str = API_URL
) -> OpenMeteoCollector:
    """Collector sin `__init__`, como el resto de la suite."""
    instancia = OpenMeteoCollector.__new__(OpenMeteoCollector)
    instancia.client = OpenMeteoClient(
        comunas=comunas or [VALPO, QUILPUE], url=url, timeout=5.0, model="best_match"
    )
    instancia._umbrales = umbrales or Umbrales()
    instancia.service = FakeIngestService()
    return instancia


def serie(comuna: Comuna, mms, *, probabilidad=None, desde=AHORA) -> SerieComunal:
    inicio = piso_horario(desde)
    puntos = tuple(
        PuntoHorario(
            momento=inicio + timedelta(hours=indice),
            mm=mm,
            probabilidad=probabilidad,
        )
        for indice, mm in enumerate(mms)
    )
    return SerieComunal(
        comuna=comuna,
        puntos=puntos,
        lat_grilla=comuna.lat,
        lon_grilla=comuna.lon,
        unidades={"precipitation": "mm"},
    )


def serie_viva(comuna: Comuna, mms) -> SerieComunal:
    """Serie anclada en la hora real.

    Los tests de la política fijan `ahora=AHORA` y pueden usar cualquier fecha.
    Los que ejercitan `run()` completo no pueden: `fetch()` llama a `evaluar()`
    sin fecha, así que la ventana se calcula contra el reloj y una serie de junio
    de 2026 quedaría íntegramente en el pasado — el collector diría, con razón,
    que no va a llover.
    """
    return serie(comuna, mms, desde=datetime.now(UTC))


def payload_de(*series: SerieComunal) -> list[dict]:
    """Reconstruye la forma en que Open-Meteo publica varias ubicaciones."""
    return [
        {
            "latitude": item.lat_grilla,
            "longitude": item.lon_grilla,
            "generationtime_ms": 0.4,
            "utc_offset_seconds": 0,
            "timezone": "GMT",
            "hourly_units": {"precipitation": "mm", "precipitation_probability": "%"},
            "hourly": {
                "time": [f"{punto.momento:%Y-%m-%dT%H:%M}" for punto in item.puntos],
                "precipitation": [punto.mm for punto in item.puntos],
                "precipitation_probability": [
                    punto.probabilidad for punto in item.puntos
                ],
            },
        }
        for item in series
    ]


def correr(instancia):
    return asyncio.run(instancia.run())


# --- 1. Lo que esta capa NO puede hacer --------------------------------------


def test_el_pronostico_no_es_un_siniestro():
    """El evento es `weather_observation`, jamás `flood`.

    `EventType.FLOOD` correlaciona y mapea a `IncidentType.FLOOD`, que el mapa
    rotula "Inundación". Emitirlo desde un modelo meteorológico crearía
    incidentes de inundación con folio y confianza en comunas donde no se ha
    inundado nada. Es el mismo error que `thermal_anomaly` evita con los
    incendios.
    """
    eventos = collector().normalize(
        [evaluar(VALPO, serie(VALPO, [12.0] * 6).puntos, ahora=AHORA)]
    )

    assert len(eventos) == 1
    assert eventos[0].type is EventType.WEATHER_OBSERVATION
    assert eventos[0].type is not EventType.FLOOD
    assert eventos[0].source is EventSource.WEATHER


def test_weather_observation_sigue_fuera_de_la_correlacion():
    """Guarda de contrato: si esto cae, el motor empieza a fundir pronósticos.

    Con `weather_observation` dentro de `CORRELATABLE_EVENT_TYPES`, 36 señales
    horarias con coordenada entrarían al Paso A y DBSCAN las agruparía con los
    accidentes y los incendios que caigan en el mismo radio.
    """
    assert EventType.WEATHER_OBSERVATION not in CORRELATABLE_EVENT_TYPES


def test_la_confianza_sale_del_catalogo_de_fuentes():
    """0.10 en un solo sitio. Dos literales serían dos políticas."""
    del_catalogo = SOURCE_BASE_CONFIDENCE[EventSource.WEATHER]
    assert del_catalogo == pytest.approx(WEATHER_CONFIDENCE)
    assert del_catalogo == pytest.approx(0.10)

    evento = collector().normalize(
        [evaluar(VALPO, serie(VALPO, [9.0] * 4).puntos, ahora=AHORA)]
    )[0]
    assert evento.confidence == pytest.approx(WEATHER_CONFIDENCE)


# --- 2. Los umbrales, uno por uno --------------------------------------------


def test_la_intensidad_horaria_levanta_el_flag_sola():
    """5 mm/h en una sola hora: chubasco fuerte, sin acumulado que lo respalde."""
    pronostico = evaluar(VALPO, serie(VALPO, [0.0, 6.0, 0.0]).puntos, ahora=AHORA)

    assert pronostico.riesgo_inundacion is True
    assert pronostico.mm_hora_max == pytest.approx(6.0)
    assert len(pronostico.motivos) == 1
    assert "intensidad" in pronostico.motivos[0]
    assert pronostico.nivel == NIVEL_RIESGO


def test_el_acumulado_de_3h_levanta_el_flag_sin_intensidad():
    """Lluvia sostenida y moderada: ninguna hora destaca, el suelo se satura."""
    reglas = Umbrales(intensidad_mm_h=8.0, acumulado_3h_mm=15.0, acumulado_24h_mm=100.0)
    pronostico = evaluar(
        VALPO, serie(VALPO, [6.0, 6.0, 6.0]).puntos, ahora=AHORA, umbrales=reglas
    )

    assert pronostico.riesgo_inundacion is True
    assert pronostico.mm_3h_max == pytest.approx(18.0)
    assert pronostico.motivos == (
        "acumulado en 3 h 18.0 mm ≥ 15.0 mm",
    ), "sólo la regla de 3 h debía disparar"


def test_el_acumulado_de_24h_levanta_el_flag_sin_las_otras_dos():
    """Temporal largo: 1.8 mm/h durante un día entero."""
    pronostico = evaluar(VALPO, serie(VALPO, [1.8] * 24).puntos, ahora=AHORA)

    assert pronostico.riesgo_inundacion is True
    assert pronostico.mm_hora_max == pytest.approx(1.8)
    assert pronostico.mm_3h_max == pytest.approx(5.4)
    assert len(pronostico.motivos) == 1
    assert "acumulado en 24 h" in pronostico.motivos[0]


def test_lluvia_normal_no_levanta_nada():
    """El caso más frecuente de un invierno de Valparaíso."""
    pronostico = evaluar(VALPO, serie(VALPO, [0.4, 0.9, 0.3, 0.0]).puntos, ahora=AHORA)

    assert pronostico.riesgo_inundacion is False
    assert pronostico.motivos == ()
    assert pronostico.nivel == NIVEL_LLUVIA
    assert pronostico.hay_lluvia is True


def test_la_llovizna_de_modelo_no_genera_evento():
    """0.1 mm en 24 h no es una señal: es el redondeo del modelo."""
    pronostico = evaluar(VALPO, serie(VALPO, [0.1, 0.0, 0.0]).puntos, ahora=AHORA)

    assert pronostico.nivel == NIVEL_SECO
    assert pronostico.hay_lluvia is False


def test_dos_reglas_juntas_son_riesgo_alto():
    pronostico = evaluar(VALPO, serie(VALPO, [12.0, 12.0, 12.0]).puntos, ahora=AHORA)

    assert len(pronostico.motivos) >= 2
    assert pronostico.nivel == NIVEL_RIESGO_ALTO


def test_una_intensidad_extrema_sola_ya_es_riesgo_alto():
    """12 mm en una hora y nada más no necesita corroboración de las otras reglas.

    Ojo con el diseño de este caso: por encima de 15 mm/h la regla de 3 h dispara
    sola —15 mm en una hora son 15 mm en tres— y ya no se estaría probando el
    factor de intensidad extrema, sino dos reglas a la vez.
    """
    pronostico = evaluar(VALPO, serie(VALPO, [12.0, 0.0, 0.0]).puntos, ahora=AHORA)

    assert pronostico.motivos == ("intensidad 12.0 mm/h ≥ 5.0 mm/h",)
    assert pronostico.nivel == NIVEL_RIESGO_ALTO


def test_la_probabilidad_no_veta_el_flag():
    """Un escenario grave y poco probable es justo el que hay que mostrar.

    Si la probabilidad filtrara, 20 mm/h con 10 % de probabilidad saldrían del
    mapa por ser el escenario menos probable — y es el único que importa.
    """
    pronostico = evaluar(
        VALPO, serie(VALPO, [20.0, 0.0], probabilidad=10).puntos, ahora=AHORA
    )

    assert pronostico.riesgo_inundacion is True
    assert pronostico.probabilidad_max == 10


def test_sin_probabilidad_no_se_cae():
    """No todos los modelos publican `precipitation_probability`."""
    pronostico = evaluar(VALPO, serie(VALPO, [7.0, 1.0]).puntos, ahora=AHORA)

    assert pronostico.probabilidad_max is None
    assert pronostico.riesgo_inundacion is True


# --- 3. La ventana y el acumulado móvil --------------------------------------


def test_la_ventana_descarta_el_pasado():
    """Open-Meteo devuelve el día completo desde las 00:00: la mitad es pasado.

    Si no se recortara, la lluvia de esta mañana contaría como pronóstico y a las
    23:00 el flag hablaría de un temporal que ya pasó.
    """
    inicio_del_dia = AHORA.replace(hour=0)
    puntos = [
        PuntoHorario(inicio_del_dia + timedelta(hours=hora), 30.0)
        for hora in range(12)
    ] + [PuntoHorario(AHORA + timedelta(hours=hora), 0.0) for hora in range(3)]

    ventana = recortar_ventana(puntos, desde=piso_horario(AHORA), horas=24)

    assert len(ventana) == 3
    assert all(punto.momento >= AHORA for punto in ventana)
    assert evaluar(VALPO, puntos, ahora=AHORA).riesgo_inundacion is False


def test_el_acumulado_movil_suma_por_tiempo_y_no_por_posicion():
    """Con un hueco en la serie, contar posiciones sumaría horas que no existen.

    Cuatro pasos de 10 mm en dos pares separados por tres horas: por tiempo, la
    mejor ventana de 3 h son 20 mm. Contando posiciones darían 40 y el flag
    saltaría por una lluvia que nunca fue continua.
    """
    base = piso_horario(AHORA)
    puntos = [
        PuntoHorario(base, 10.0),
        PuntoHorario(base + timedelta(hours=1), 10.0),
        PuntoHorario(base + timedelta(hours=5), 10.0),
        PuntoHorario(base + timedelta(hours=6), 10.0),
    ]

    total, arranque = acumulado_maximo(puntos, horas=3)

    assert total == pytest.approx(20.0)
    assert arranque in (base, base + timedelta(hours=5))


def test_el_timestamp_nunca_queda_en_el_futuro():
    """`EventCreate` rechaza más de 5 minutos de futuro (ver INGEST_FUTURE_...).

    La ventana arranca en la hora truncada hacia abajo justamente por esto: si
    se redondeara hacia arriba, el evento nacería hasta 59 minutos adelante y la
    validación lo tumbaría entero.
    """
    pronostico = evaluar(VALPO, serie(VALPO, [8.0] * 4).puntos, ahora=AHORA)

    assert pronostico.inicio <= AHORA
    assert pronostico.inicio.minute == 0
    assert pronostico.fin == pronostico.inicio + timedelta(hours=24)

    evento = collector().normalize([pronostico])[0]
    assert evento.timestamp == pronostico.inicio


def test_un_nulo_no_es_un_cero():
    """`mm=None` significa "no llegó el dato", no "no va a llover"."""
    con_nulos = serie(VALPO, [None, None, 3.0])

    assert con_nulos.datos_validos == 1
    assert evaluar(VALPO, con_nulos.puntos, ahora=AHORA).mm_total == pytest.approx(3.0)
    assert serie(VALPO, [None, None]).datos_validos == 0


# --- 4. El contrato de salida ------------------------------------------------


def test_el_payload_es_json_serializable():
    """Viaja en una columna JSONB: un `datetime` suelto reventaría al insertar.

    Es la trampa que ya se pagó una vez con `seismic_row`, así que se comprueba
    con un `json.dumps` real y no leyendo el código.
    """
    pronostico = evaluar(VALPO, serie(VALPO, [7.0, 2.0]).puntos, ahora=AHORA)

    plano = json.loads(json.dumps(pronostico.to_dict()))

    assert plano["comuna"] == "Valparaíso"
    assert plano["riesgo_inundacion"] is True
    assert plano["lat"] == pytest.approx(VALPO.lat)
    assert plano["lon"] == pytest.approx(VALPO.lon)
    assert plano["probabilidad_max"] is None
    assert plano["fuente"] == "open-meteo"
    assert plano["inicio"].endswith("+00:00")


def test_el_flag_llega_al_raw_data_donde_el_frontend_lo_busca():
    pronostico = evaluar(VALPO, serie(VALPO, [9.0, 9.0, 9.0]).puntos, ahora=AHORA)

    evento = collector().normalize([pronostico])[0]

    assert evento.raw_data[WEATHER_KEY]["riesgo_inundacion"] is True
    assert evento.raw_data[WEATHER_KEY]["mm_3h_max"] == pytest.approx(27.0)
    # Alias que `communes.extract_commune` sabe leer.
    assert evento.raw_data["comuna"] == "Valparaíso"


def test_el_external_id_colapsa_dos_corridas_de_la_misma_hora():
    """Es lo que hace que subir la frecuencia no cueste filas.

    Dos consultas en la misma hora describen la misma hora de pronóstico: el
    upsert las une y la segunda sólo refresca el payload.
    """
    primera = evaluar(VALPO, serie(VALPO, [6.0]).puntos, ahora=AHORA)
    segunda = evaluar(
        VALPO, serie(VALPO, [9.0]).puntos, ahora=AHORA + timedelta(minutes=35)
    )
    hora_siguiente = evaluar(
        VALPO, serie(VALPO, [9.0]).puntos, ahora=AHORA + timedelta(hours=1)
    )

    assert build_external_id(primera) == build_external_id(segunda)
    assert build_external_id(primera) != build_external_id(hora_siguiente)
    assert build_external_id(primera) == "openmeteo:valparaiso:20260615T12"


def test_el_texto_no_se_disfraza_de_alerta_oficial():
    """La cautela del USGS con el tsunami, aplicada a la lluvia."""
    texto = describir(evaluar(VALPO, serie(VALPO, [11.0] * 3).puntos, ahora=AHORA))

    assert "RIESGO DE INUNDACIÓN" in texto
    assert "no es una alerta oficial" in texto
    assert "SENAPRED" in texto
    assert "Comuna: Valparaíso." in texto


# --- 5. Emparejamiento por posición y forma de la respuesta ------------------


def test_un_conteo_distinto_al_pedido_no_se_empareja_a_medias():
    """Sin identificador en la respuesta, el orden es todo lo que hay."""
    with pytest.raises(CollectorError) as error:
        parse_payload(
            payload_de(serie(VALPO, [1.0])),
            [VALPO, QUILPUE],
            origin="test",
        )

    assert "se pidieron 2" in error.value.message
    assert "llegaron 1" in error.value.message


def test_una_celda_lejana_avisa_sin_tumbar_la_corrida():
    """Es la guarda contra que la respuesta llegue en otro orden."""
    desordenada = payload_de(serie(VALPO, [1.0]), serie(QUILPUE, [1.0]))
    desordenada[0]["latitude"] = -30.0  # tres grados al norte de Valparaíso

    series, advertencias = parse_payload(
        desordenada, [VALPO, QUILPUE], origin="test", max_drift=0.5
    )

    assert len(series) == 2
    assert any("Valparaíso" in aviso and "revisar el orden" in aviso for aviso in advertencias)


def test_una_sola_comuna_devuelve_objeto_y_no_lista():
    """La API cambia de forma según el número de coordenadas."""
    unico = payload_de(serie(VALPO, [2.0, 2.0]))[0]

    series, advertencias = parse_payload(unico, [VALPO], origin="test")

    assert len(series) == 1
    assert series[0].datos_validos == 2
    assert advertencias == []


@pytest.mark.parametrize(
    ("item", "esperado"),
    [
        ({"latitude": -33.0, "longitude": -71.6}, "no trae bloque 'hourly'"),
        ({"hourly": {"time": ["2026-06-15T12:00"]}}, "sin series"),
        ({"hourly": {"precipitation": [1.0]}}, "sin series"),
    ],
)
def test_una_respuesta_sin_la_estructura_minima_falla_con_diagnostico(item, esperado):
    """Falla, no devuelve una serie vacía: eso se leería como "no va a llover"."""
    with pytest.raises(CollectorError) as error:
        parse_serie(item, VALPO, origin="test")

    assert esperado in error.value.message


def test_las_series_de_largo_distinto_se_recortan_al_minimo():
    """Perder las últimas horas es una degradación, no un fallo."""
    item = payload_de(serie(VALPO, [1.0, 2.0, 3.0]))[0]
    item["hourly"]["precipitation"] = [1.0, 2.0]

    resultado = parse_serie(item, VALPO, origin="test")

    assert len(resultado.puntos) == 2


def test_el_error_de_la_api_conserva_el_motivo():
    """Open-Meteo manda `error: true` y el motivo aparte.

    `geoservices.raise_if_service_error` produciría "devolvió un error: True" y
    perdería la única parte útil del mensaje. Por eso hay un helper propio.
    """
    with pytest.raises(CollectorError) as error:
        raise_if_openmeteo_error(
            {
                "error": True,
                "reason": "Cannot initialize WeatherVariable from invalid String value lluvia",
            },
            origin="test",
        )

    assert "invalid String value lluvia" in error.value.message
    assert "True" not in error.value.message


# --- 6. La corrida completa, contra un mock HTTP ----------------------------


@respx.mock
def test_una_corrida_normal_ingiere_solo_las_comunas_con_lluvia():
    """34 comunas secas no son 34 rechazos: son un día de verano.

    Si el filtro viviera en `normalize()`, `BaseCollector` contaría cada comuna
    seca como rechazo y la corrida quedaría en `partial` para siempre.
    """
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            json=payload_de(
                serie_viva(VALPO, [8.0, 8.0, 0.0]),
                serie_viva(QUILPUE, [0.0, 0.0, 0.0]),
            ),
        )
    )

    instancia = collector()
    resultado = correr(instancia)

    assert resultado.status is CollectorStatus.SUCCESS
    assert resultado.fetched == 1, "sólo Valparaíso tenía lluvia"
    assert resultado.rejected == 0
    assert [evento.raw_data["comuna"] for evento in instancia.service.eventos] == [
        "Valparaíso"
    ]


@respx.mock
def test_ninguna_comuna_con_lluvia_es_un_exito_con_cero():
    """El estado normal de un verano. No es una degradación."""
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            json=payload_de(serie_viva(VALPO, [0.0, 0.0]), serie_viva(QUILPUE, [0.0, 0.0])),
        )
    )

    resultado = correr(collector())

    assert resultado.status is CollectorStatus.SUCCESS
    assert resultado.fetched == 0


@respx.mock
def test_la_variable_vacia_en_todas_las_comunas_no_es_un_dia_seco():
    """El fallo silencioso que este proyecto persigue en todas las fuentes.

    Estructura correcta, variable en nulos: si eso pasara por "no llovió", la
    capa quedaría apagada durante un temporal con la corrida en verde.
    """
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            json=payload_de(
                serie(VALPO, [None, None]), serie(QUILPUE, [None, None])
            ),
        )
    )

    resultado = correr(collector())

    assert resultado.status is CollectorStatus.FAILED
    assert "cambio en la API" in (resultado.error or "")


@respx.mock
@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_un_5xx_no_escapa_como_excepcion(status_code):
    ruta = respx.get(API_URL).mock(return_value=httpx.Response(status_code))

    resultado = correr(collector())

    assert resultado.status is CollectorStatus.FAILED
    assert str(status_code) in (resultado.error or "")
    assert ruta.call_count == 3, "un 5xx se reintenta dos veces"


@respx.mock
def test_un_400_no_se_reintenta():
    """Un 400 de Open-Meteo es un parámetro mal escrito: reintentar no arregla."""
    ruta = respx.get(API_URL).mock(
        return_value=httpx.Response(400, json={"error": True, "reason": "Latitude must be in range"})
    )

    resultado = correr(collector())

    assert resultado.status is CollectorStatus.FAILED
    assert ruta.call_count == 1
    assert "Latitude must be in range" in (resultado.error or "")


@respx.mock
def test_un_timeout_no_escapa_como_excepcion():
    respx.get(API_URL).mock(side_effect=httpx.ReadTimeout("agotado"))

    resultado = correr(collector())

    assert resultado.status is CollectorStatus.FAILED
    assert "ReadTimeout" in (resultado.error or "")


@respx.mock
def test_la_consulta_pide_una_sola_peticion_para_todas_las_comunas():
    """El ahorro que hace irrelevante el presupuesto del nivel abierto."""
    ruta = respx.get(API_URL).mock(
        return_value=httpx.Response(
            200, json=payload_de(serie(VALPO, [1.0]), serie(QUILPUE, [1.0]))
        )
    )

    correr(collector())

    assert ruta.call_count == 1
    consulta = ruta.calls[0].request.url.params
    assert consulta["latitude"] == "-33.0472,-33.0472"
    assert consulta["longitude"] == "-71.6127,-71.4425"
    assert consulta["hourly"] == ",".join(HOURLY_VARIABLES)
    # UTC para no reconstruir desfases, y `land` porque media región es costera.
    assert consulta["timezone"] == "UTC"
    assert consulta["cell_selection"] == "land"


# --- 7. La tabla de comunas --------------------------------------------------


def test_las_comunas_por_defecto_caen_dentro_de_la_region():
    """Un punto fuera del bbox entraría marcado —o rechazado— sin avisar."""
    bbox = settings.region_bbox
    fuera = [
        comuna.nombre
        for comuna in COMUNAS_V_REGION
        if not bbox.contains(comuna.lat, comuna.lon)
    ]

    assert fuera == [], f"comunas fuera de region_bbox: {fuera}"
    assert len(COMUNAS_V_REGION) == 36, "las 36 continentales de la V Región"


def test_las_dos_comunas_insulares_no_estan():
    """Isla de Pascua y Juan Fernández quedan fuera a propósito: otro clima y
    fuera del bbox. Se agregan por `.env` si alguna vez hacen falta."""
    nombres = {comuna.nombre for comuna in COMUNAS_V_REGION}

    assert "Isla de Pascua" not in nombres
    assert "Juan Fernández" not in nombres


def test_el_slug_aguanta_tildes_y_espacios():
    assert slug("Viña del Mar") == "vina-del-mar"
    assert slug("Concón") == "concon"
    assert slug("  Quilpué ") == "quilpue"


def test_la_lista_del_env_se_parsea_o_revienta_al_construir():
    """Una configuración mala tiene que fallar en el arranque, no a medias."""
    assert parse_comunas("Valparaíso|-33.05|-71.61") == [
        Comuna("Valparaíso", -33.05, -71.61)
    ]
    assert parse_comunas(None) == list(COMUNAS_V_REGION)
    assert parse_comunas("") == list(COMUNAS_V_REGION)

    with pytest.raises(ValueError, match="Formato esperado"):
        parse_comunas("Valparaíso|-33.05")
    with pytest.raises(ValueError, match="no numéricas"):
        parse_comunas("Valparaíso|sur|-71.61")
    with pytest.raises(ValueError, match="fuera de rango"):
        parse_comunas("Valparaíso|-933.05|-71.61")


# --- 8. Cadencia -------------------------------------------------------------


def test_la_cadencia_es_mucho_mayor_que_la_de_un_siniestro():
    """Un modelo global se recalcula cada 3-6 h: preguntar cada 5 min es ruido."""
    assert OpenMeteoCollector.poll_interval_seconds() >= 900
    assert OpenMeteoCollector.default_interval_seconds == 1800
