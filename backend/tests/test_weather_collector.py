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
from app.collectors.weather.region import consolidar
from app.collectors.weather.umbrales import (
    AMENAZA_CALOR,
    AMENAZA_INCENDIO,
    AMENAZA_REMOCION,
    AMENAZA_UV,
    AMENAZA_VIENTO,
    NIVEL_LLUVIA,
    NIVEL_RIESGO,
    NIVEL_RIESGO_ALTO,
    NIVEL_SECO,
    SEVERIDAD_AVISO,
    SEVERIDAD_CRITICA,
    SEVERIDAD_NINGUNA,
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


#: Ambiente por defecto de las series de prueba: **una tarde en que no pasa
#: nada**. 15 °C, 70 % de humedad, 8 km/h de viento con ráfagas de 15 y un
#: índice UV de 2.
#:
#: No es relleno. Los tests de la política de lluvia tienen que poder disparar
#: SÓLO su regla, y desde la v2 una serie sin variables tácticas dispararía otra
#: cosa: si `humedad` llegara ausente en las 36 comunas, `_faltantes_globales`
#: levantaría una advertencia y dejaría la corrida en `partial`, rompiendo tests
#: que no hablan de eso. Un ambiente explícito y benigno mantiene cada test
#: probando una cosa.
AMBIENTE_CALMO = {
    "temp": 15.0,
    "humedad": 70.0,
    "viento": 8.0,
    "rafaga": 15.0,
    "uv": 2.0,
}


def _por_hora(valor, largo: int) -> list:
    """Acepta un escalar (constante en la serie) o una lista ya por hora."""
    if isinstance(valor, list | tuple):
        return list(valor) + [None] * (largo - len(valor))
    return [valor] * largo


def serie(
    comuna: Comuna,
    mms,
    *,
    probabilidad=None,
    desde=AHORA,
    temp=None,
    humedad=None,
    viento=None,
    rafaga=None,
    uv=None,
) -> SerieComunal:
    inicio = piso_horario(desde)
    largo = len(mms)
    temps = _por_hora(AMBIENTE_CALMO["temp"] if temp is None else temp, largo)
    humedades = _por_hora(AMBIENTE_CALMO["humedad"] if humedad is None else humedad, largo)
    vientos = _por_hora(AMBIENTE_CALMO["viento"] if viento is None else viento, largo)
    rafagas = _por_hora(AMBIENTE_CALMO["rafaga"] if rafaga is None else rafaga, largo)
    uvs = _por_hora(AMBIENTE_CALMO["uv"] if uv is None else uv, largo)

    puntos = tuple(
        PuntoHorario(
            momento=inicio + timedelta(hours=indice),
            mm=mm,
            probabilidad=probabilidad,
            temp_c=temps[indice],
            humedad=humedades[indice],
            viento_kmh=vientos[indice],
            rafaga_kmh=rafagas[indice],
            uv=uvs[indice],
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
    """Reconstruye la forma en que Open-Meteo publica varias ubicaciones.

    Emite las siete variables de `HOURLY_VARIABLES`, no sólo las dos de la v1:
    el fixture tiene que parecerse a lo que la API devuelve ahora que se le
    piden siete, o los tests de integración estarían probando un contrato que ya
    no se pide.
    """
    return [
        {
            "latitude": item.lat_grilla,
            "longitude": item.lon_grilla,
            "generationtime_ms": 0.4,
            "utc_offset_seconds": 0,
            "timezone": "GMT",
            "hourly_units": {
                "precipitation": "mm",
                "precipitation_probability": "%",
                "temperature_2m": "°C",
                "relative_humidity_2m": "%",
                "wind_speed_10m": "km/h",
                "wind_gusts_10m": "km/h",
                "uv_index": "",
            },
            "hourly": {
                "time": [f"{punto.momento:%Y-%m-%dT%H:%M}" for punto in item.puntos],
                "precipitation": [punto.mm for punto in item.puntos],
                "precipitation_probability": [
                    punto.probabilidad for punto in item.puntos
                ],
                "temperature_2m": [punto.temp_c for punto in item.puntos],
                "relative_humidity_2m": [punto.humedad for punto in item.puntos],
                "wind_speed_10m": [punto.viento_kmh for punto in item.puntos],
                "wind_gusts_10m": [punto.rafaga_kmh for punto in item.puntos],
                "uv_index": [punto.uv for punto in item.puntos],
            },
        }
        for item in series
    ]


def eventos_comunales(instancia) -> list:
    """Los eventos de comuna que ingirió una corrida, sin el agregado regional.

    Casi todos los tests de `run()` hablan de comunas. La fila regional se emite
    siempre —es lo que mantiene vivo el widget en un día tranquilo— y contarla
    como una comuna más convertiría cada aserción de conteo en una trampa.
    """
    return [
        evento
        for evento in instancia.service.eventos
        if evento.raw_data.get(WEATHER_KEY, {}).get("ambito") != "region"
    ]


def evento_regional(instancia):
    """El agregado de la corrida, o `None`."""
    return next(
        (
            evento
            for evento in instancia.service.eventos
            if evento.raw_data.get(WEATHER_KEY, {}).get("ambito") == "region"
        ),
        None,
    )


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

    # El motivo nombra el umbral MÁS ALTO que se cruzó, no el primero. 12 mm/h
    # está por encima del tramo crítico (10 mm/h), y decir "≥ 5.0" dejaría a
    # alguien preguntándose por qué el widget está en rojo con un umbral de 5.
    assert pronostico.motivos == ("intensidad 12.0 mm/h ≥ 10.0 mm/h",)
    assert pronostico.nivel == NIVEL_RIESGO_ALTO
    assert pronostico.severidad == SEVERIDAD_CRITICA


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


# --- 4 bis. La política táctica: incendio, viento, calor y UV ----------------
#
# Una regla por test y una sola. Con seis amenazas y dos severidades, un test que
# construya "un día feo" y compruebe que sale rojo no distingue cuál de las seis
# reglas funcionó — que es exactamente el agujero por el que un umbral mal
# escrito sobrevive a la suite.


def pronostico(comuna=VALPO, mms=None, **ambiente):
    """Evalúa una serie con el ambiente indicado, anclada en `AHORA`."""
    return evaluar(comuna, serie(comuna, mms or [0.0] * 4, **ambiente).puntos, ahora=AHORA)


def disparo_de(resultado, amenaza):
    return next((item for item in resultado.disparos if item.amenaza == amenaza), None)


def test_el_30_30_30_exige_las_tres_condiciones_en_la_misma_hora():
    """El test que justifica cómo está implementada la regla.

    Éste es el escenario de la implementación perezosa: 31 °C a mediodía, 25 %
    de humedad por la tarde y una ráfaga de 40 km/h de madrugada, cuando hacía
    11 °C y había rocío. Comparando máximos independientes, el 30-30-30 se
    cumpliría. En el terreno no se cumplió nunca: los tres números no coincidieron
    ni una hora.
    """
    resultado = pronostico(
        temp=[11.0, 31.0, 20.0, 14.0],
        humedad=[95.0, 60.0, 25.0, 90.0],
        rafaga=[40.0, 10.0, 8.0, 12.0],
    )

    assert disparo_de(resultado, AMENAZA_INCENDIO) is None
    assert resultado.severidad == SEVERIDAD_NINGUNA


def test_el_30_30_30_dispara_critico_cuando_las_tres_coinciden():
    resultado = pronostico(
        temp=[20.0, 32.0, 20.0, 14.0],
        humedad=[60.0, 22.0, 60.0, 90.0],
        rafaga=[10.0, 38.0, 10.0, 12.0],
    )

    disparo = disparo_de(resultado, AMENAZA_INCENDIO)
    assert disparo is not None
    assert disparo.severidad == SEVERIDAD_CRITICA
    assert disparo.valor == 32.0
    # La hora importa: es lo que una brigada necesita para planificar el turno.
    assert disparo.momento == piso_horario(AHORA) + timedelta(hours=1)


def test_el_tramo_costero_cubre_el_regimen_en_que_esta_region_se_quema():
    """25/40/25 no cumple el 30-30-30 y sigue siendo una tarde de riesgo.

    Es la corrección explícita al Factor 30-30-30: en la costa de Valparaíso casi
    nunca se cumple y los incendios ocurren igual. El tramo de aviso existe para
    eso y por eso es ámbar y no rojo.
    """
    resultado = pronostico(temp=27.0, humedad=35.0, rafaga=28.0)

    disparo = disparo_de(resultado, AMENAZA_INCENDIO)
    assert disparo is not None
    assert disparo.severidad == SEVERIDAD_AVISO


def test_una_tarde_calurosa_y_humeda_no_es_condicion_de_propagacion():
    """30 °C con 65 % de humedad es un domingo de enero, no un escenario."""
    resultado = pronostico(temp=31.0, humedad=65.0, rafaga=35.0)

    assert disparo_de(resultado, AMENAZA_INCENDIO) is None


def test_el_viento_dispara_solo_sin_pedirle_nada_al_termometro():
    """Un temporal invernal de 70 km/h y 12 °C importa igual.

    A 60 km/h se suspende el combate aéreo y empiezan a caer ramas sobre el
    tendido — la capa de cortes de luz de este mismo sistema.
    """
    resultado = pronostico(temp=12.0, humedad=90.0, rafaga=70.0)

    disparo = disparo_de(resultado, AMENAZA_VIENTO)
    assert disparo is not None
    assert disparo.severidad == SEVERIDAD_AVISO
    assert disparo_de(resultado, AMENAZA_INCENDIO) is None


def test_una_rafaga_de_85_es_critica():
    resultado = pronostico(temp=12.0, humedad=90.0, rafaga=85.0)

    assert disparo_de(resultado, AMENAZA_VIENTO).severidad == SEVERIDAD_CRITICA


#: Un día con noche. Las series de este bloque necesitan una mínima real, o la
#: regla de noche tropical se cumple sola: con una temperatura constante, el
#: mínimo de la ventana es igual al máximo.
def dia_con_noche(maxima: float, minima: float = 14.0) -> list[float]:
    return [maxima, maxima - 3.0, minima + 2.0, minima]


def test_el_calor_tiene_dos_tramos():
    aviso = pronostico(temp=dia_con_noche(33.0))
    critico = pronostico(temp=dia_con_noche(37.0))
    templado = pronostico(temp=dia_con_noche(30.0))

    assert disparo_de(aviso, AMENAZA_CALOR).severidad == SEVERIDAD_AVISO
    assert disparo_de(critico, AMENAZA_CALOR).severidad == SEVERIDAD_CRITICA
    assert disparo_de(templado, AMENAZA_CALOR) is None


def test_la_noche_tropical_agrava_pero_no_dispara_sola():
    """La carga del calor la produce la falta de alivio nocturno, no el pico.

    Dos comprobaciones en una: 33 °C con una mínima de 22 °C sube a crítico, y
    una mínima de 22 °C con una máxima templada no dispara nada — no hay un
    disparo de "noche tropical" compitiendo por el sitio del widget.
    """
    con_alivio = pronostico(temp=[33.0, 28.0, 18.0, 15.0])
    sin_alivio = pronostico(temp=[33.0, 28.0, 24.0, 22.0])

    assert disparo_de(con_alivio, AMENAZA_CALOR).severidad == SEVERIDAD_AVISO
    assert disparo_de(sin_alivio, AMENAZA_CALOR).severidad == SEVERIDAD_CRITICA

    templado = pronostico(temp=[24.0, 23.0, 22.0, 21.0])
    assert disparo_de(templado, AMENAZA_CALOR) is None


def test_el_uv_usa_las_bandas_de_la_oms():
    """8 es «muy alto» y 11 es «extremo». Son las únicas cifras no negociables."""
    assert disparo_de(pronostico(uv=9.0), AMENAZA_UV).severidad == SEVERIDAD_AVISO
    assert disparo_de(pronostico(uv=12.0), AMENAZA_UV).severidad == SEVERIDAD_CRITICA
    assert "extremo" in disparo_de(pronostico(uv=12.0), AMENAZA_UV).texto
    assert disparo_de(pronostico(uv=6.0), AMENAZA_UV) is None


def test_la_ventana_del_uv_es_de_seis_horas_y_eso_se_nota():
    """Un UV de 12 a veinte horas vista no puede encender el widget de noche.

    Es toda la razón de ser de las ventanas por amenaza: el UV describe lo que
    le pasa a la piel de alguien que está afuera *ahora*, no mañana al mediodía.
    """
    manana = [2.0] * 20 + [12.0, 12.0, 4.0, 1.0]
    resultado = pronostico(mms=[0.0] * 24, uv=manana)

    assert disparo_de(resultado, AMENAZA_UV) is None
    assert resultado.severidad == SEVERIDAD_NINGUNA


def test_una_variable_ausente_nunca_dispara():
    """`None` es "no sabemos", y con un "no sabemos" no se decide nada.

    El modo de fallo que esto previene es concreto y caro: una humedad ausente
    leída como 0 % pondría la región entera en 30-30-30 crítico para siempre.
    """
    resultado = pronostico(temp=35.0, humedad=[None] * 4, rafaga=45.0)

    assert disparo_de(resultado, AMENAZA_INCENDIO) is None
    # El calor sí dispara: no depende de la humedad. Una variable rota degrada
    # una amenaza, no las cuatro.
    assert disparo_de(resultado, AMENAZA_CALOR) is not None


def test_una_comuna_seca_con_uv_extremo_genera_evento():
    """El caso que la v1 no habría guardado: 0,0 mm y todo lo demás en rojo.

    Es la razón por la que `hay_senal` reemplazó a `hay_lluvia` como criterio de
    emisión. Una tarde de febrero en Petorca no tiene una gota de agua y es
    exactamente lo que esta capa existe para describir.
    """
    resultado = pronostico(mms=[0.0] * 4, temp=38.0, humedad=18.0, rafaga=45.0, uv=12.0)

    assert resultado.hay_lluvia is False
    assert resultado.hay_senal is True
    assert resultado.severidad == SEVERIDAD_CRITICA


def test_el_desempate_entre_amenazas_es_estable_y_no_alfabetico():
    """Con dos amenazas críticas, manda la que mata gente en esta región.

    Sin `PRIORIDAD_AMENAZA`, la métrica que el widget expande dependería del
    orden en que se evaluaron las reglas — que es un detalle de implementación,
    no una decisión de producto.
    """
    resultado = pronostico(mms=[30.0, 30.0, 5.0, 0.0], uv=12.0)

    assert resultado.severidad == SEVERIDAD_CRITICA
    assert resultado.amenaza == AMENAZA_REMOCION
    assert resultado.disparo_principal.amenaza == AMENAZA_REMOCION


def test_el_estado_tactico_no_contamina_el_contrato_de_lluvia():
    """`riesgo_inundacion` y `nivel` siguen hablando SÓLO de agua.

    Es lo que mantiene viva la capa de MapLibre sin tocarla: un día de calor
    extremo no puede encender el anillo azul de riesgo de inundación.
    """
    resultado = pronostico(mms=[0.0] * 4, temp=38.0, uv=12.0)

    assert resultado.severidad == SEVERIDAD_CRITICA
    assert resultado.riesgo_inundacion is False
    assert resultado.nivel == NIVEL_SECO
    assert resultado.motivos == ()


def test_el_texto_nombra_la_amenaza_sin_declarar_una_alerta():
    """Ninguno de los rótulos nuevos puede sonar a un decreto de SENAPRED."""
    texto = describir(pronostico(mms=[0.0] * 4, temp=38.0, humedad=18.0, rafaga=45.0))

    assert "CALOR EXTREMO" in texto
    assert "ola de calor" not in texto.lower(), "es un término con definición oficial"
    assert "no es una alerta oficial" in texto
    assert "SENAPRED" in texto


# --- 4 ter. La consolidación regional ----------------------------------------


def test_la_region_toma_el_peor_caso_y_no_el_promedio():
    """Promediar 38 °C con 17 °C da 26 °C: un número que no describe a nadie."""
    caluroso = evaluar(
        VALPO, serie(VALPO, [0.0] * 4, temp=38.0).puntos, ahora=AHORA
    )
    templado = evaluar(
        QUILPUE, serie(QUILPUE, [0.0] * 4, temp=17.0).puntos, ahora=AHORA
    )

    estado = consolidar(
        [templado, caluroso], inicio=caluroso.inicio, fin=caluroso.fin
    )

    assert estado.severidad == SEVERIDAD_CRITICA
    assert estado.amenaza == AMENAZA_CALOR
    assert estado.comuna_origen == "Valparaíso"
    assert estado.temp_max_c == 38.0
    # Y en el ambiente, la mediana: 27,5 y no 38. Es lo que ve el widget cuando
    # alguien lo mira sin que haya nada encendido.
    assert estado.temp_c == 27.5


def test_una_region_en_calma_no_es_una_region_sin_datos():
    """Los dos ceros que el widget no puede pintar igual."""
    tranquilas = [
        evaluar(comuna, serie(comuna, [0.0] * 4).puntos, ahora=AHORA)
        for comuna in (VALPO, QUILPUE)
    ]
    estado = consolidar(tranquilas, inicio=AHORA, fin=AHORA + timedelta(hours=24))

    assert estado.severidad == SEVERIDAD_NINGUNA
    assert estado.comunas == 2
    assert estado.disparo is None

    # Y sin comunas no hay estado: emitir "todo tranquilo" con cero comunas
    # detrás sería el fallo silencioso de siempre, pintado de verde.
    assert consolidar([], inicio=AHORA, fin=AHORA) is None


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
def test_una_corrida_normal_ingiere_solo_las_comunas_con_senal():
    """34 comunas tranquilas no son 34 rechazos: son un día de verano.

    Si el filtro viviera en `normalize()`, `BaseCollector` contaría cada comuna
    tranquila como rechazo y la corrida quedaría en `partial` para siempre.
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
    assert resultado.rejected == 0
    assert [evento.raw_data["comuna"] for evento in eventos_comunales(instancia)] == [
        "Valparaíso"
    ], "sólo Valparaíso tenía señal"
    # Valparaíso + el agregado regional. El agregado se emite SIEMPRE: es lo que
    # distingue "se consultó y no pasaba nada" de "la fuente está caída".
    assert resultado.fetched == 2


@respx.mock
def test_un_dia_sin_nada_igual_deja_la_fila_regional():
    """El estado normal de un verano. No es una degradación, y no es silencio.

    Antes de la v2 esta corrida terminaba con cero filas, indistinguible de una
    fuente muerta. Ahora deja el agregado, que dice con hora y con datos que se
    consultaron dos comunas y ninguna cruzó nada. Es lo que mantiene encendido
    el widget el 95 % de los días del año.
    """
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            200,
            json=payload_de(serie_viva(VALPO, [0.0, 0.0]), serie_viva(QUILPUE, [0.0, 0.0])),
        )
    )

    instancia = collector()
    resultado = correr(instancia)

    assert resultado.status is CollectorStatus.SUCCESS
    assert eventos_comunales(instancia) == []

    regional = evento_regional(instancia)
    assert regional is not None
    payload = regional.raw_data[WEATHER_KEY]
    assert payload["severidad"] == "ninguna"
    assert payload["comunas"] == 2
    assert payload["disparo_principal"] is None
    # Y NO lleva el alias `comuna` al nivel de arriba: no pertenece a ninguna, y
    # ese alias es lo que `communes.extract_commune` usa para atribuir señales.
    assert "comuna" not in regional.raw_data


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
