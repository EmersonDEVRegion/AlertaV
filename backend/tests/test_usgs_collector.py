"""Tests del collector de sismos del USGS.

Las fixtures reproducen respuestas reales del feed `2.5_day.geojson` (esquema
verificado en agosto de 2026), recortadas y completadas con casos de la zona
central de Chile. `normalize()` es pura, así que todo el mapeo se prueba sin red
ni base de datos.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.usgs.client import UsgsClient, parse_feature, parse_feed
from app.collectors.usgs.collector import (
    UsgsCollector,
    UsgsMapping,
    _row_for_db,
    build_external_id,
    build_text,
    in_bbox,
    matches_event_type,
    seismic_row,
)
from app.core.config import BoundingBox
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType

CENTRAL_BBOX = BoundingBox(west=-73.0, south=-35.0, east=-69.0, north=-31.0)


def _mapping(**overrides) -> UsgsMapping:
    base = {
        "bbox": CENTRAL_BBOX,
        "min_magnitude": 0.0,
        "event_types": ("earthquake",),
        "include_automatic": True,
    }
    return UsgsMapping(**{**base, **overrides})


def _collector(mapping: UsgsMapping | None = None) -> UsgsCollector:
    """Instancia sin `__init__`: no se necesita sesión ni cliente para normalizar."""
    collector = UsgsCollector.__new__(UsgsCollector)
    collector._mapping = mapping or _mapping()
    return collector


# Feed real recortado. De los seis sismos:
#   * dos en la zona central de Chile (uno revisado, uno preliminar),
#   * uno en Alaska y uno en Indonesia — fuera de la caja,
#   * uno en el Atlántico Norte a lat 10 / lon -33: es el caso que la heurística
#     de ejes invertidos de `geoservices` "corregiría" y movería de continente,
#   * una tronadura de cantera dentro de la caja, que NO es un sismo.
# Además, una feature sin geometría y otra sin id, que deben contarse como
# ilegibles sin tumbar el lote.
USGS_GEOJSON = {
    "type": "FeatureCollection",
    "metadata": {
        "generated": 1787047455000,
        "title": "USGS Magnitude 2.5+ Earthquakes, Past Day",
        "status": 200,
        "api": "2.7.0",
        "count": 8,
    },
    "features": [
        {
            "type": "Feature",
            "properties": {
                "mag": 5.4,
                "place": "38 km WNW of Valparaíso, Chile",
                "time": 1787040000000,
                "updated": 1787044000000,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000central",
                "felt": 214,
                "cdi": 4.6,
                "mmi": 4.1,
                "alert": "green",
                "status": "reviewed",
                "tsunami": 1,
                "sig": 449,
                "net": "us",
                "code": "7000central",
                "magType": "mww",
                "type": "earthquake",
                "title": "M 5.4 - 38 km WNW of Valparaíso, Chile",
            },
            "geometry": {"type": "Point", "coordinates": [-71.9312, -32.9876, 42.5]},
            "id": "us7000central",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 3.1,
                "place": "20 km E of Los Andes, Chile",
                "time": 1787035000000,
                "updated": 1787035600000,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000andes",
                "felt": None,
                "alert": None,
                "status": "automatic",
                "tsunami": 0,
                "sig": 148,
                "magType": "ml",
                "type": "earthquake",
                "title": "M 3.1 - 20 km E of Los Andes, Chile",
            },
            "geometry": {"type": "Point", "coordinates": [-70.3901, -32.8334, 98.2]},
            "id": "us7000andes",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 3.2,
                "place": "93 km SE of King Cove, Alaska",
                "time": 1787043884087,
                "updated": 1787045006573,
                "status": "reviewed",
                "tsunami": 0,
                "magType": "ml",
                "type": "earthquake",
            },
            "geometry": {"type": "Point", "coordinates": [-161.454, 54.391, 20]},
            "id": "aka2026qhrekz",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 4.8,
                "place": "74 km N of Ruteng, Indonesia",
                "time": 1787043699508,
                "updated": 1787045964040,
                "status": "reviewed",
                "tsunami": 0,
                "magType": "mb",
                "type": "earthquake",
            },
            "geometry": {"type": "Point", "coordinates": [120.4656, -7.9353, 33.11]},
            "id": "us6000tlm3",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 4.2,
                "place": "northern Mid-Atlantic Ridge",
                "time": 1787020000000,
                "updated": 1787021000000,
                "status": "reviewed",
                "tsunami": 0,
                "magType": "mb",
                "type": "earthquake",
            },
            # lat 10.5 fuera del rango chileno, lon -33.2 dentro: el par que
            # dispararía la corrección de ejes de `geoservices`.
            "geometry": {"type": "Point", "coordinates": [-33.2, 10.5, 10]},
            "id": "us7000atlantic",
        },
        {
            "type": "Feature",
            "properties": {
                "mag": 2.6,
                "place": "12 km NE of Til Til, Chile",
                "time": 1787030000000,
                "updated": 1787030500000,
                "status": "reviewed",
                "tsunami": 0,
                "magType": "ml",
                "type": "quarry blast",
            },
            "geometry": {"type": "Point", "coordinates": [-70.8, -33.05, 0.5]},
            "id": "us7000quarry",
        },
        {
            "type": "Feature",
            "properties": {"mag": 3.0, "time": 1787030000000, "type": "earthquake"},
            "geometry": None,
            "id": "us7000nogeom",
        },
        {
            "type": "Feature",
            "properties": {"mag": 3.0, "time": 1787030000000, "type": "earthquake"},
            "geometry": {"type": "Point", "coordinates": [-71.0, -33.0, 10]},
        },
    ],
}


def _records():
    records, unreadable = parse_feed(USGS_GEOJSON, origin="test")
    return records, unreadable


def _one(usgs_id: str):
    records, _ = _records()
    return next(record for record in records if record.usgs_id == usgs_id)


# --- Parseo del feed ---------------------------------------------------------


class TestParseFeed:
    def test_lee_lon_lat_profundidad_en_ese_orden(self) -> None:
        central = _one("us7000central")
        assert central.lat == pytest.approx(-32.9876)
        assert central.lon == pytest.approx(-71.9312)
        assert central.depth_km == pytest.approx(42.5)

    def test_no_invierte_ejes_de_un_sismo_fuera_de_chile(self) -> None:
        """El caso que motiva no reutilizar `parse_feature_collection`.

        Un sismo en el Atlántico a lat 10.5 / lon -33.2 tiene la latitud fuera
        del rango chileno y la longitud dentro. La heurística de ejes invertidos
        de `geoservices` lo "corregiría" a lat -33.2 / lon 10.5 y lo mudaría de
        océano. Acá se lee el GeoJSON como manda la RFC 7946 y punto.
        """
        atlantic = _one("us7000atlantic")
        assert atlantic.lat == pytest.approx(10.5)
        assert atlantic.lon == pytest.approx(-33.2)

    def test_features_ilegibles_se_cuentan_sin_tumbar_el_lote(self) -> None:
        records, unreadable = _records()
        # Sin geometría y sin id.
        assert unreadable == 2
        assert len(records) == 6

    def test_mapea_los_campos_del_catalogo(self) -> None:
        central = _one("us7000central")
        assert central.magnitude == pytest.approx(5.4)
        assert central.mag_type == "mww"
        assert central.review_status == "reviewed"
        assert central.pager_alert == "green"
        assert central.felt_reports == 214
        assert central.significance == 449
        assert central.tsunami is True
        assert central.event_type == "earthquake"
        assert central.time == datetime(2026, 8, 18, 8, 0, tzinfo=UTC)

    def test_tsunami_cero_es_falso(self) -> None:
        andes = _one("us7000andes")
        assert andes.tsunami is False

    def test_feature_sin_id_se_descarta(self) -> None:
        assert parse_feature({"geometry": {"coordinates": [-71, -33]}}) is None

    def test_feature_sin_coordenadas_se_descarta(self) -> None:
        assert parse_feature({"id": "x", "geometry": {"type": "Point"}}) is None

    def test_respuesta_sin_features_es_error_no_lista_vacia(self) -> None:
        with pytest.raises(CollectorError, match="no tiene 'features'"):
            parse_feed({"type": "FeatureCollection"}, origin="test")

    def test_status_no_200_en_metadata_es_error(self) -> None:
        """El feed sirve su propio código de estado dentro de un HTTP 200."""
        with pytest.raises(CollectorError, match="status 500"):
            parse_feed(
                {"metadata": {"status": 500}, "features": []}, origin="test"
            )

    def test_coleccion_vacia_es_legitima(self) -> None:
        records, unreadable = parse_feed(
            {"metadata": {"status": 200}, "features": []}, origin="test"
        )
        assert records == []
        assert unreadable == 0


# --- Filtro espacial y de catálogo -------------------------------------------


class TestFiltros:
    @pytest.mark.parametrize(
        ("lat", "lon", "esperado"),
        [
            (-33.0, -71.5, True),  # Valparaíso
            (-31.0, -73.0, True),  # esquina noroeste, inclusive
            (-35.0, -69.0, True),  # esquina sureste, inclusive
            (-30.9, -71.5, False),  # justo al norte
            (-35.1, -71.5, False),  # justo al sur
            (-33.0, -73.1, False),  # justo al oeste
            (-33.0, -68.9, False),  # justo al este (Mendoza)
        ],
    )
    def test_caja_de_la_zona_central(self, lat: float, lon: float, esperado: bool) -> None:
        movido = replace(_one("us7000central"), lat=lat, lon=lon)
        assert in_bbox(movido, CENTRAL_BBOX) is esperado

    def test_in_bbox_usa_el_epicentro(self) -> None:
        assert in_bbox(_one("us7000central"), CENTRAL_BBOX) is True
        assert in_bbox(_one("aka2026qhrekz"), CENTRAL_BBOX) is False
        assert in_bbox(_one("us6000tlm3"), CENTRAL_BBOX) is False

    def test_tronadura_de_cantera_no_es_sismo(self) -> None:
        quarry = _one("us7000quarry")
        assert in_bbox(quarry, CENTRAL_BBOX) is True  # está en la caja…
        assert matches_event_type(quarry, ("earthquake",)) is False  # …pero no es sismo

    def test_lista_de_tipos_vacia_acepta_todo(self) -> None:
        assert matches_event_type(_one("us7000quarry"), ()) is True


# --- Normalización -----------------------------------------------------------


class TestNormalize:
    def test_solo_sobreviven_los_sismos_de_la_zona_central(self) -> None:
        records, _ = _records()
        events = _collector().normalize(records)
        assert [event.external_id for event in events] == [
            "usgs:us7000central",
            "usgs:us7000andes",
        ]

    def test_mapea_a_evento_del_dominio(self) -> None:
        records, _ = _records()
        event = _collector().normalize(records)[0]
        assert event.source is EventSource.USGS
        assert event.type is EventType.EARTHQUAKE
        assert event.confidence == 1.0
        assert event.lat == pytest.approx(-32.9876)
        assert event.lon == pytest.approx(-71.9312)
        assert event.timestamp == datetime(2026, 8, 18, 8, 0, tzinfo=UTC)

    def test_guarda_magnitud_y_profundidad_tipadas_para_la_tabla_satelite(self) -> None:
        records, _ = _records()
        event = _collector().normalize(records)[0]
        detalle = event.raw_data["_seismic"]
        assert detalle["usgs_id"] == "us7000central"
        assert detalle["magnitude"] == pytest.approx(5.4)
        assert detalle["depth_km"] == pytest.approx(42.5)
        assert detalle["mag_type"] == "mww"
        assert detalle["pager_alert"] == "green"
        assert detalle["review_status"] == "reviewed"
        assert detalle["tsunami"] is True

    def test_conserva_el_payload_original_y_la_geometria(self) -> None:
        records, _ = _records()
        event = _collector().normalize(records)[0]
        assert event.raw_data["net"] == "us"
        assert event.raw_data["_collector"] == "usgs_sismos"
        assert event.raw_data["_geometry"]["coordinates"] == [-71.9312, -32.9876, 42.5]

    def test_filtro_de_magnitud_minima(self) -> None:
        records, _ = _records()
        events = _collector(_mapping(min_magnitude=4.0)).normalize(records)
        assert [event.external_id for event in events] == ["usgs:us7000central"]

    def test_puede_excluir_soluciones_preliminares(self) -> None:
        records, _ = _records()
        collector = _collector(_mapping(include_automatic=False))
        events = collector.normalize(records)
        assert [event.external_id for event in events] == ["usgs:us7000central"]
        assert any("preliminares omitidos" in w for w in collector.warnings)

    def test_sismo_sin_hora_se_descarta_con_advertencia(self) -> None:
        sin_hora = replace(_one("us7000central"), time=None)
        collector = _collector()
        assert collector.normalize([sin_hora]) == []
        assert any("sin campo 'time'" in w for w in collector.warnings)


# --- Idempotencia y texto ----------------------------------------------------


class TestIdentidadYTexto:
    def test_external_id_usa_el_id_estable_del_usgs(self) -> None:
        central = _one("us7000central")
        assert build_external_id(central) == "usgs:us7000central"

    def test_el_external_id_no_cambia_al_revisarse_la_solucion(self) -> None:
        """Lo que hace que un preliminar y su revisión sean la misma fila.

        El USGS republica el evento con la magnitud corregida y `status` en
        'reviewed'. Si el id derivara de la magnitud o de la hora de
        actualización, cada revisión crearía un sismo fantasma en el mapa.
        """
        andes = _one("us7000andes")
        revisado = replace(
            andes,
            magnitude=3.4,
            review_status="reviewed",
            updated=datetime(2026, 8, 18, 18, 0, tzinfo=UTC),
        )
        assert build_external_id(revisado) == build_external_id(andes)

    def test_texto_en_espanol_con_magnitud_y_profundidad(self) -> None:
        central = _one("us7000central")
        texto = build_text(central)
        assert "magnitud 5.4 mww" in texto
        assert "42 km de profundidad" in texto
        assert "Reporte instrumental del USGS" in texto

    def test_texto_advierte_que_un_preliminar_es_preliminar(self) -> None:
        andes = _one("us7000andes")
        assert "preliminar" in build_text(andes).lower()

    def test_texto_no_declara_alerta_de_tsunami(self) -> None:
        """La bandera del feed marca región con protocolo, no alerta vigente."""
        central = _one("us7000central")
        texto = build_text(central)
        assert "protocolo de tsunami" in texto
        assert "SHOA/SENAPRED" in texto

    def test_texto_tolera_magnitud_ausente(self) -> None:
        sin_mag = replace(_one("us7000central"), magnitude=None)
        assert "magnitud no determinada" in build_text(sin_mag)


# --- Sanitización para los CHECK de la tabla ---------------------------------


class TestSeismicRow:
    def test_nivel_pager_desconocido_se_guarda_como_nulo(self) -> None:
        """Un valor nuevo del USGS no puede tumbar la inserción del lote."""
        raro = replace(_one("us7000central"), pager_alert="purple")
        assert seismic_row(raro)["pager_alert"] is None

    def test_fechas_van_como_iso_para_poder_viajar_en_jsonb(self) -> None:
        central = _one("us7000central")
        row = seismic_row(central)
        assert isinstance(row["source_updated_at"], str)
        assert row["source_updated_at"].startswith("2026-08-18T")

    def test_la_fecha_vuelve_a_datetime_antes_de_tocar_la_base(self) -> None:
        """asyncpg no convierte texto a timestamptz; psycopg2 sí.

        Es la diferencia que haría pasar los tests y reventar en producción, así
        que la reconstrucción se prueba explícitamente.
        """
        row = _row_for_db(42, seismic_row(_one("us7000central")))
        assert row["raw_event_id"] == 42
        assert row["source_updated_at"] == datetime(2026, 8, 18, 9, 6, 40, tzinfo=UTC)

    def test_una_fecha_corrupta_no_tumba_la_insercion(self) -> None:
        row = _row_for_db(42, {**seismic_row(_one("us7000central")),
                               "source_updated_at": "ayer por la tarde"})
        assert row["source_updated_at"] is None

    def test_sin_fecha_de_actualizacion_queda_en_nulo(self) -> None:
        row = _row_for_db(42, seismic_row(replace(_one("us7000central"), updated=None)))
        assert row["source_updated_at"] is None


# --- Cliente HTTP ------------------------------------------------------------

FEED_URL = "https://usgs.test/summary/2.5_day.geojson"


class TestClient:
    @respx.mock
    async def test_descarga_y_parsea_el_feed(self) -> None:
        respx.get(FEED_URL).mock(
            return_value=httpx.Response(200, json=USGS_GEOJSON)
        )
        client = UsgsClient(sources=f"geojson|{FEED_URL}")
        records, warnings = await client.fetch_earthquakes()
        assert len(records) == 6
        assert any("sin id o sin coordenadas" in w for w in warnings)

    @respx.mock
    async def test_html_de_un_portal_caido_no_es_una_lista_vacia(self) -> None:
        """El modo de falla más peligroso: cero sismos que parecen "no hubo"."""
        respx.get(FEED_URL).mock(
            return_value=httpx.Response(
                200, text="<!DOCTYPE html><html><body>Service Unavailable</body></html>"
            )
        )
        client = UsgsClient(sources=f"geojson|{FEED_URL}")
        with pytest.raises(CollectorError, match="ninguna de las fuentes"):
            await client.fetch_earthquakes()

    @respx.mock
    async def test_usa_el_respaldo_y_lo_deja_por_escrito(self) -> None:
        backup = "https://mirror.test/feed.geojson"
        respx.get(FEED_URL).mock(return_value=httpx.Response(503))
        respx.get(backup).mock(return_value=httpx.Response(200, json=USGS_GEOJSON))

        client = UsgsClient(
            sources=f"geojson|{FEED_URL};geojson|{backup}", timeout=1.0
        )
        records, warnings = await client.fetch_earthquakes()
        assert len(records) == 6
        assert any("fuente de respaldo" in w for w in warnings)

    def test_sin_fuentes_declaradas_falla_al_construirse(self) -> None:
        with pytest.raises(CollectorError, match="USGS_SOURCES está vacío"):
            UsgsClient(sources="")


# --- Contrato con el resto del sistema ---------------------------------------


class TestIntegracionConElPipeline:
    def test_registrado_con_cadencia_de_cinco_minutos(self) -> None:
        from app.collectors.registry import available_collectors, collector_class

        assert "usgs_sismos" in available_collectors()
        assert collector_class("usgs_sismos").poll_interval_seconds() == 300

    def test_el_sismo_no_entra_al_motor_de_correlacion(self) -> None:
        """Decisión de diseño, no omisión. Ver `enums.CORRELATABLE_EVENT_TYPES`.

        El radio de agrupación (1500 m) y la ventana (4 h) del motor son
        exactamente la escala de una réplica: correlacionar sismos fusionaría el
        evento principal con sus réplicas y borraría la secuencia.
        """
        from app.models.enums import CORRELATABLE_EVENT_TYPES

        assert EventType.EARTHQUAKE not in CORRELATABLE_EVENT_TYPES

    def test_la_fuente_no_aporta_confianza_sobre_el_fenomeno(self) -> None:
        """Un sismo es cierto, pero no corrobora que un punto se esté quemando."""
        from app.services.correlation.confidence import rule_for

        regla = rule_for(EventSource.USGS)
        assert regla.ceiling == 0.0
        assert regla.confirming is False
