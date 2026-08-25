"""Contrato de salida de la capa meteorológica.

Lo que se cubre acá es la costura entre dos capas que se escribieron por
separado: el collector deja un diccionario en `raw_data['_weather']` y la API lo
vuelve a leer. Si esas dos ideas del payload se separan, nada falla —el JSONB
acepta cualquier forma— y el mapa se queda con una capa vacía. Por eso el test
más importante del archivo es el que hace el viaje completo: toma el evento que
produce `OpenMeteoCollector.normalize()` y lo pasa por el schema de lectura, sin
inventar el payload a mano en medio.

El resto son las tres cosas que pueden romperse en silencio: el orden de registro
de las rutas, la foto de una fila por comuna, y que `riesgo_inundacion` llegue a
MapLibre como booleano y no como texto.

No se toca la base: `EventRepository` se sustituye por un doble.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_weather_service
from app.api.v1.endpoints import events as events_endpoints
from app.collectors.weather.comunas import Comuna
from app.collectors.weather.openmeteo_worker import (
    WEATHER_KEY,
    OpenMeteoCollector,
)
from app.collectors.weather.umbrales import PuntoHorario, evaluar
from app.core.config import settings
from app.main import app
from app.models.enums import EventSource, EventType
from app.schemas.weather import WEATHER_PAYLOAD_KEY, WeatherForecastRead
from app.services.weather_service import WeatherService

VALPO = Comuna("Valparaíso", -33.0472, -71.6127)
QUILPUE = Comuna("Quilpué", -33.0472, -71.4425)

BASE = "/api/v1/events/weather"


# --- Fábricas ----------------------------------------------------------------


def pronostico_de(comuna: Comuna, mms, *, ahora: datetime) -> Any:
    """Un pronóstico real, calculado por el módulo de umbrales."""
    inicio = ahora.replace(minute=0, second=0, microsecond=0)
    puntos = [
        PuntoHorario(inicio + timedelta(hours=indice), mm)
        for indice, mm in enumerate(mms)
    ]
    return evaluar(comuna, puntos, ahora=ahora)


def fila(comuna: Comuna, mms, *, ahora: datetime | None = None) -> SimpleNamespace:
    """Una fila de `raw_events` tal como la escribe el collector.

    El `raw_data` NO se escribe a mano: sale de `normalize()`, que es lo que hace
    de esto una prueba de la costura y no de una copia del payload.
    """
    momento = ahora or datetime.now(UTC)
    evento = OpenMeteoCollector.normalize(
        OpenMeteoCollector.__new__(OpenMeteoCollector),
        [pronostico_de(comuna, mms, ahora=momento)],
    )[0]
    return SimpleNamespace(
        public_id=uuid4(),
        timestamp=evento.timestamp,
        source=EventSource.WEATHER,
        type=EventType.WEATHER_OBSERVATION,
        lat=evento.lat,
        lon=evento.lon,
        text=evento.text,
        raw_data=evento.raw_data,
        commune=comuna.nombre,
        province=None,
    )


class _FakeRepo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.kwargs: dict[str, Any] = {}

    async def list_events(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.rows


def servicio(rows: list[SimpleNamespace]) -> WeatherService:
    service = WeatherService.__new__(WeatherService)
    service.repo = _FakeRepo(rows)  # type: ignore[assignment]
    return service


@pytest.fixture
def cliente():
    """Devuelve `(hacer_cliente, )`: cada test decide sus filas."""

    creados: list[WeatherService] = []

    def construir(rows: list[SimpleNamespace]) -> tuple[TestClient, _FakeRepo]:
        service = servicio(rows)
        creados.append(service)
        app.dependency_overrides[get_weather_service] = lambda: service
        return TestClient(app), service.repo  # type: ignore[return-value]

    yield construir
    app.dependency_overrides.pop(get_weather_service, None)


# --- 1. La costura entre el collector y la API -------------------------------


def test_el_payload_del_collector_se_lee_entero_desde_la_api():
    """El viaje completo, sin escribir el payload a mano en medio.

    Si el collector renombrara `mm_3h_max` o dejara de escribir `motivos`, el
    JSONB lo aceptaría sin queja y la capa del mapa se quedaría muda. Esto es lo
    único que lo impide.
    """
    ahora = datetime.now(UTC)
    lectura = WeatherForecastRead.from_event(fila(VALPO, [9.0, 9.0, 9.0], ahora=ahora))

    assert lectura is not None
    assert lectura.comuna == "Valparaíso"
    assert lectura.riesgo_inundacion is True
    assert lectura.mm_total == pytest.approx(27.0)
    assert lectura.mm_3h_max == pytest.approx(27.0)
    assert lectura.ventana_horas == 24
    assert lectura.motivos, "el motivo del flag tiene que viajar"
    assert lectura.modelo == "best_match"
    assert lectura.es_pronostico is True
    assert lectura.texto and "SENAPRED" in lectura.texto


def test_la_clave_del_payload_es_la_misma_en_las_dos_capas():
    """`WEATHER_PAYLOAD_KEY` es espejo de `WEATHER_KEY`, no una copia suelta.

    El schema no importa el collector a propósito —la API no debe depender de la
    capa de recolección— y el precio de esa independencia es que hay dos
    constantes. Esta es la costura que las mantiene juntas.
    """
    assert WEATHER_PAYLOAD_KEY == WEATHER_KEY


# --- 2. Orden de registro de las rutas ---------------------------------------


class TestRuteo:
    """`/events/weather*` va antes que `/events/{public_id}`.

    Se inspecciona el `router` del módulo de endpoints y no `app.routes`: el
    router es donde el orden se decide —FastAPI resuelve por orden de registro—
    y así la comprobación no depende de cómo la versión instalada de Starlette
    aplane las rutas montadas.
    """

    @staticmethod
    def _indice(sufijo: str) -> int:
        rutas = [getattr(ruta, "path", "") for ruta in events_endpoints.router.routes]
        return next(i for i, path in enumerate(rutas) if path == sufijo)

    @pytest.mark.parametrize(
        "ruta",
        ["/events/weather", "/events/weather/geojson", "/events/weather/stats"],
    )
    def test_no_queda_capturada_por_la_ruta_de_detalle(self, ruta: str) -> None:
        assert self._indice(ruta) < self._indice("/events/{public_id}")


# --- 3. La foto: una fila por comuna ----------------------------------------


def test_solo_sobrevive_la_ventana_mas_reciente_de_cada_comuna(cliente):
    """La capa es una foto, no un histórico.

    `list_events` ordena por timestamp descendente, así que la primera aparición
    de cada comuna es su ventana más reciente. Sin esta reducción, el mapa
    pintaría cuatro puntos sobre Valparaíso.
    """
    ahora = datetime.now(UTC)
    filas = [
        fila(VALPO, [12.0], ahora=ahora),
        fila(VALPO, [1.0], ahora=ahora - timedelta(hours=1)),
        fila(VALPO, [1.0], ahora=ahora - timedelta(hours=2)),
        fila(QUILPUE, [3.0], ahora=ahora),
    ]
    client, _ = cliente(filas)

    cuerpo = client.get(BASE).json()

    assert [item["comuna"] for item in cuerpo] == ["Valparaíso", "Quilpué"]
    assert cuerpo[0]["mm_total"] == pytest.approx(12.0), "la más reciente, no la vieja"


def test_el_orden_es_por_acumulado_descendente(cliente):
    """Si algo se recorta aguas abajo, que se recorte lo irrelevante."""
    ahora = datetime.now(UTC)
    client, _ = cliente(
        [fila(QUILPUE, [2.0], ahora=ahora), fila(VALPO, [30.0], ahora=ahora)]
    )

    cuerpo = client.get(BASE).json()

    assert [item["comuna"] for item in cuerpo] == ["Valparaíso", "Quilpué"]


def test_solo_riesgo_filtra_sobre_la_foto_y_no_sobre_el_limite(cliente):
    """El filtro se aplica después de reducir a una fila por comuna.

    Filtrar antes —en SQL, después de un LIMIT sobre el histórico— podría
    devolver 3 comunas en riesgo habiendo 20.
    """
    ahora = datetime.now(UTC)
    client, _ = cliente(
        [
            fila(VALPO, [9.0, 9.0, 9.0], ahora=ahora),  # riesgo
            fila(QUILPUE, [0.5], ahora=ahora),  # lluvia sin riesgo
        ]
    )

    todas = client.get(BASE).json()
    con_riesgo = client.get(BASE, params={"solo_riesgo": "true"}).json()

    assert len(todas) == 2
    assert [item["comuna"] for item in con_riesgo] == ["Valparaíso"]
    assert all(item["riesgo_inundacion"] for item in con_riesgo)


def test_la_consulta_pide_solo_la_capa_meteorologica(cliente):
    ahora = datetime.now(UTC)
    client, repo = cliente([fila(VALPO, [5.0], ahora=ahora)])

    client.get(BASE, params={"hours": 6})

    assert repo.kwargs["sources"] == [EventSource.WEATHER]
    assert repo.kwargs["types"] == [EventType.WEATHER_OBSERVATION]
    esperado = datetime.now(UTC) - timedelta(hours=6)
    assert abs((repo.kwargs["since"] - esperado).total_seconds()) < 60


def test_el_recorte_es_el_regional_y_no_el_sismico(cliente):
    """Un sismo a 200 km se siente en Valparaíso; una lluvia a 200 km no moja."""
    client, repo = cliente([fila(VALPO, [5.0])])

    client.get(BASE)

    bbox = settings.region_bbox
    assert repo.kwargs["bbox"] == (bbox.west, bbox.south, bbox.east, bbox.north)
    assert bbox.west > settings.usgs_bbox.west


@pytest.mark.parametrize("horas", [0, 49])
def test_la_ventana_esta_acotada(cliente, horas):
    """`hours` es holgura, no histórico: 3 meses de lluvia no son una capa."""
    client, _ = cliente([])

    assert client.get(BASE, params={"hours": horas}).status_code == 422


# --- 4. GeoJSON: lo que MapLibre puede consumir -----------------------------


def test_el_flag_llega_como_booleano_de_verdad(cliente):
    """La capa filtra con `["==", ["get", "riesgo_inundacion"], true]`.

    Una expresión de MapLibre no compara tipos distintos: si el flag viajara
    como la cadena "true", el filtro no encontraría nada y el fallo sería
    silencioso del lado del mapa.
    """
    client, _ = cliente([fila(VALPO, [9.0, 9.0, 9.0])])

    feature = client.get(f"{BASE}/geojson").json()["features"][0]

    assert feature["properties"]["riesgo_inundacion"] is True
    assert isinstance(feature["properties"]["riesgo_inundacion"], bool)


def test_los_motivos_viajan_como_una_sola_cadena(cliente):
    """MapLibre serializa a texto cualquier arreglo anidado en `properties`."""
    client, _ = cliente([fila(VALPO, [9.0, 9.0, 9.0])])

    propiedades = client.get(f"{BASE}/geojson").json()["features"][0]["properties"]

    assert isinstance(propiedades["motivos"], str)
    assert "mm" in propiedades["motivos"]
    assert all(
        not isinstance(valor, list | dict) for valor in propiedades.values()
    ), "ninguna propiedad puede ser un objeto o un arreglo"


def test_la_geometria_es_lon_lat(cliente):
    """RFC 7946: primero longitud. Invertirlo pondría la capa en el Pacífico."""
    client, _ = cliente([fila(VALPO, [5.0])])

    feature = client.get(f"{BASE}/geojson").json()["features"][0]

    assert feature["geometry"]["type"] == "Point"
    lon, lat = feature["geometry"]["coordinates"]
    assert lon == pytest.approx(VALPO.lon)
    assert lat == pytest.approx(VALPO.lat)


def test_el_geojson_recuerda_que_es_un_pronostico(cliente):
    """Los dos recordatorios que esta capa arrastra."""
    client, _ = cliente([fila(VALPO, [9.0, 9.0, 9.0])])

    propiedades = client.get(f"{BASE}/geojson").json()["features"][0]["properties"]

    assert propiedades["es_pronostico"] is True
    assert propiedades["is_confirmed_incident"] is False


# --- 5. Filas raras: se omiten, no tumban la capa ---------------------------


def test_una_fila_sin_payload_no_tumba_la_capa(cliente):
    """Es un camino de LECTURA: servir el resto es mejor que un 500."""
    ahora = datetime.now(UTC)
    huerfana = fila(VALPO, [5.0], ahora=ahora)
    huerfana.raw_data = {"comuna": "Valparaíso"}  # sin `_weather`
    client, _ = cliente([huerfana, fila(QUILPUE, [4.0], ahora=ahora)])

    cuerpo = client.get(BASE).json()

    assert [item["comuna"] for item in cuerpo] == ["Quilpué"]


def test_los_dos_modos_de_fallo_se_distinguen_en_el_log():
    """Una fila sin payload y un payload ilegible son problemas distintos.

    * **Sin payload**: algo que no es este collector escribió una fila con
      `source = weather`.
    * **Payload ilegible**: el collector cambió de forma y las dos capas se
      separaron.

    Los dos se omiten igual, así que desde la respuesta HTTP son
    indistinguibles. Si además se confundieran en el log, habría que abrir la
    base para saber cuál de los dos está pasando — y son arreglos distintos.
    """
    sin_payload = fila(VALPO, [5.0])
    sin_payload.raw_data = {"comuna": "Valparaíso"}
    ilegible = fila(QUILPUE, [5.0])
    del ilegible.raw_data[WEATHER_KEY]["nivel"]

    registros: list[logging.LogRecord] = []

    class _Captura(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            registros.append(record)

    logger = logging.getLogger("app.schemas.weather")
    handler = _Captura()
    logger.addHandler(handler)
    try:
        assert WeatherForecastRead.from_event(sin_payload) is None
        assert WeatherForecastRead.from_event(ilegible) is None
    finally:
        logger.removeHandler(handler)

    mensajes = [record.getMessage() for record in registros]
    assert "fila meteorológica sin payload; se omite" in mensajes
    assert "payload meteorológico ilegible; se omite" in mensajes


def test_un_payload_incompleto_se_omite(cliente):
    ahora = datetime.now(UTC)
    rota = fila(VALPO, [5.0], ahora=ahora)
    del rota.raw_data[WEATHER_KEY]["riesgo_inundacion"]
    client, _ = cliente([rota, fila(QUILPUE, [4.0], ahora=ahora)])

    respuesta = client.get(BASE)

    assert respuesta.status_code == 200
    assert [item["comuna"] for item in respuesta.json()] == ["Quilpué"]


# --- 6. Resumen --------------------------------------------------------------


def test_el_resumen_cuenta_lluvia_y_riesgo_por_separado(cliente):
    """Nunca filtra: "cuántas comunas llueven" y "cuántas en riesgo" son dos."""
    ahora = datetime.now(UTC)
    client, _ = cliente(
        [
            fila(VALPO, [9.0, 9.0, 9.0], ahora=ahora),
            fila(QUILPUE, [0.6], ahora=ahora),
        ]
    )

    resumen = client.get(f"{BASE}/stats").json()

    assert resumen["comunas"] == 2
    assert resumen["en_riesgo"] == 1
    assert resumen["comunas_en_riesgo"] == ["Valparaíso"]
    assert resumen["mm_total_max"] == pytest.approx(27.0)
    assert resumen["mm_hora_max"] == pytest.approx(9.0)


def test_el_resumen_de_una_capa_vacia_no_revienta(cliente):
    """Un verano entero devuelve esto, y tiene que ser un 200."""
    client, _ = cliente([])

    resumen = client.get(f"{BASE}/stats").json()

    assert resumen == {
        "comunas": 0,
        "en_riesgo": 0,
        "mm_total_max": None,
        "mm_hora_max": None,
        "comunas_en_riesgo": [],
        "ventana_inicio": None,
        "ventana_fin": None,
    }
