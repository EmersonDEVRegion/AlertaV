"""Tests del collector de CONAF.

Las fixtures reproducen respuestas reales de la capa de incendios (esquema
verificado en agosto de 2026). `normalize()` es pura, así que todo el mapeo se
prueba sin red ni base de datos.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.conaf.client import ConafClient, build_where
from app.collectors.conaf.collector import (
    ConafCollector,
    ConafMapping,
    build_external_id,
    build_text,
    matches_region,
    matches_state,
)
from app.collectors.geoservices import parse_feature_collection, parse_source_specs
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType

# Respuesta real de la capa, recortada: dos incendios de Valparaíso, uno de
# Tarapacá (fuera de región), uno sin geometría pero con lat/lon en atributos y
# uno sin ninguna fecha utilizable.
CONAF_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": 308638750,
            "geometry": {"type": "Point", "coordinates": [-71.1010818, -32.8603897]},
            "properties": {
                "id": 881424126,
                "nombre": "LOS MAITENES",
                "estado": "Extinguido",
                "f_inicio": 1782998520000,
                "f_control": 1783004700000,
                "f_extincion": None,
                "sup_total": 1.9,
                "lat": -32.8603897,
                "lon": -71.1010818,
                "ambito": "CONAF",
                "comuna": "Hijuelas",
                "provincia": "Quillota",
                "region": "Valparaíso",
                "ObjectId": 308638750,
            },
        },
        {
            "type": "Feature",
            "id": 308638751,
            "geometry": {"type": "Point", "coordinates": [-71.4500000, -33.0400000]},
            "properties": {
                "id": 881424999,
                "nombre": "QUEBRADA VERDE",
                "estado": "En Combate",
                "f_inicio": 1786320000000,
                "f_control": None,
                "f_extincion": None,
                "sup_total": 12.5,
                "lat": -33.04,
                "lon": -71.45,
                "ambito": "CONAF",
                "comuna": "Valparaíso",
                "provincia": "Valparaíso",
                "region": "Valparaíso",
                "ObjectId": 308638751,
            },
        },
        {
            "type": "Feature",
            "id": 308638743,
            "geometry": {"type": "Point", "coordinates": [-69.4236373, -19.3130340]},
            "properties": {
                "id": 884273580,
                "nombre": "CANCHA CAMIÑA",
                "estado": "Extinguido",
                "f_inicio": 1784236500000,
                "sup_total": 0.26,
                "comuna": "Camiña",
                "provincia": "Tamarugal",
                "region": "Tarapacá",
                "ObjectId": 308638743,
            },
        },
        {
            "type": "Feature",
            "id": 308638752,
            "geometry": None,
            "properties": {
                "id": 881425555,
                "nombre": "SIN GEOMETRIA",
                "estado": "Controlado",
                "f_inicio": 1786400000000,
                "lat": -32.95,
                "lon": -71.25,
                "comuna": "Quintero",
                "region": "Valparaíso",
                "ObjectId": 308638752,
            },
        },
        {
            "type": "Feature",
            "id": 308638753,
            "geometry": {"type": "Point", "coordinates": [-71.3, -33.1]},
            "properties": {
                "id": 881426666,
                "nombre": "SIN FECHA",
                "estado": "En Combate",
                "f_inicio": None,
                "f_control": None,
                "f_extincion": None,
                "region": "Valparaíso",
                "ObjectId": 308638753,
            },
        },
    ],
}


def _features() -> list:
    return parse_feature_collection(CONAF_GEOJSON, origin="fixture")


def _collector(mapping: ConafMapping | None = None) -> ConafCollector:
    # `normalize` es pura: no necesita sesión ni cliente HTTP.
    collector = ConafCollector.__new__(ConafCollector)
    if mapping is not None:
        collector._mapping = mapping
    return collector


DEFAULT_MAPPING = ConafMapping(regions=("Valparaíso",), filter_by_region=True)


class TestWhere:
    def test_acota_la_ventana_por_fecha_de_inicio(self) -> None:
        clause = build_where(7, now=datetime(2026, 8, 18, 12, 0, tzinfo=UTC))
        assert clause == "f_inicio >= timestamp '2026-08-11 12:00:00'"


class TestFiltros:
    def test_region_ignora_tildes_y_mayusculas(self) -> None:
        feature = _features()[0]
        assert matches_region(feature, ["VALPARAISO"]) is True
        assert matches_region(feature, ["Región de Valparaíso"]) is True
        assert matches_region(feature, ["Ñuble"]) is False

    def test_sin_regiones_configuradas_no_filtra(self) -> None:
        assert matches_region(_features()[2], []) is True

    def test_estado(self) -> None:
        feature = _features()[1]
        assert matches_state(feature, ["En Combate"]) is True
        assert matches_state(feature, ["en combate"]) is True
        assert matches_state(feature, ["Extinguido"]) is False
        assert matches_state(feature, []) is True


class TestExternalId:
    def test_usa_el_identificador_de_conaf(self) -> None:
        assert build_external_id(_features()[0]) == "conaf:881424126"

    def test_es_determinista(self) -> None:
        assert build_external_id(_features()[0]) == build_external_id(_features()[0])

    def test_hash_de_respaldo_si_no_hay_id(self) -> None:
        """Sin ID de origen se deriva uno estable: la idempotencia no es opcional."""
        from app.collectors.geoservices import GeoFeature

        feature = GeoFeature(
            properties={"nombre": "X", "f_inicio": 1786320000000, "comuna": "Quilpué"},
            lat=-33.0,
            lon=-71.4,
        )
        first = build_external_id(feature)
        assert first.startswith("conaf:h:")
        assert first == build_external_id(feature)

    def test_incendios_distintos_no_colisionan(self) -> None:
        ids = {build_external_id(feature) for feature in _features()}
        assert len(ids) == 5


class TestNormalize:
    def test_filtra_por_region(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        # 5 features: 1 es de Tarapacá y otra no tiene fecha utilizable.
        assert len(events) == 3
        assert all("Tarapac" not in (event.text or "") for event in events)

    def test_confirma_incendio_con_confianza_total(self) -> None:
        """Regla del proyecto: CONAF confirma, FIRMS sólo sugiere."""
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        assert all(event.source is EventSource.CONAF for event in events)
        assert all(event.type is EventType.WILDFIRE for event in events)
        assert all(event.confidence == pytest.approx(1.0) for event in events)

    def test_timestamp_en_utc(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        primero = next(e for e in events if e.external_id == "conaf:881424126")
        assert primero.timestamp == datetime(2026, 7, 2, 13, 22, tzinfo=UTC)

    def test_coordenadas_de_la_geometria(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        primero = next(e for e in events if e.external_id == "conaf:881424126")
        assert primero.lat == pytest.approx(-32.8603897)
        assert primero.lon == pytest.approx(-71.1010818)
        assert primero.in_region is True

    def test_usa_lat_lon_de_atributos_si_falta_la_geometria(self) -> None:
        """Perder un incendio por una geometría ausente sería inaceptable."""
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        sin_geom = next(e for e in events if e.external_id == "conaf:881425555")
        assert sin_geom.lat == pytest.approx(-32.95)
        assert sin_geom.lon == pytest.approx(-71.25)

    def test_descarta_filas_sin_fecha_y_lo_advierte(self) -> None:
        collector = _collector(DEFAULT_MAPPING)
        events = collector.normalize(_features())
        assert all(e.external_id != "conaf:881426666" for e in events)
        assert any("sin fecha" in warning for warning in collector.warnings)

    def test_filtra_por_estado(self) -> None:
        mapping = ConafMapping(regions=("Valparaíso",), states=("En Combate",))
        events = _collector(mapping).normalize(_features())
        assert [e.external_id for e in events] == ["conaf:881424999"]

    def test_conserva_el_payload_original(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        raw = next(e for e in events if e.external_id == "conaf:881424126").raw_data
        assert raw["nombre"] == "LOS MAITENES"
        assert raw["sup_total"] == 1.9
        assert raw["_collector"] == "conaf_incendios"

    def test_texto_legible_con_estado_y_ubicacion(self) -> None:
        texto = build_text(_features()[0])
        assert "LOS MAITENES" in texto
        assert "Extinguido" in texto
        assert "Hijuelas" in texto
        assert "CONAF" in texto

    def test_correccion_de_zona_horaria_configurable(self) -> None:
        mapping = ConafMapping(regions=("Valparaíso",), time_offset_minutes=-240)
        events = _collector(mapping).normalize(_features())
        corregido = next(e for e in events if e.external_id == "conaf:881424126")
        assert corregido.timestamp == datetime(2026, 7, 2, 17, 22, tzinfo=UTC)


@respx.mock
class TestClient:
    def _client(self) -> ConafClient:
        return ConafClient(
            sources=parse_source_specs("arcgis|https://conaf.test/FeatureServer|0"),
            timeout=5,
        )

    async def test_descarga_y_parsea(self) -> None:
        respx.get("https://conaf.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, json=CONAF_GEOJSON)
        )
        features, warnings = await self._client().fetch_incendios()
        assert len(features) == 5
        assert warnings == []

    async def test_where_rechazado_reintenta_sin_filtro(self) -> None:
        """Si cambia el nombre del campo de fecha, se trae todo y se advierte.

        Lo inaceptable sería devolver cero incendios porque el filtro dejó de ser
        válido: eso se leería como 'no hubo incendios'.
        """
        route = respx.get("https://conaf.test/FeatureServer/0/query")
        route.side_effect = [
            httpx.Response(400, text="Invalid field: f_inicio"),
            httpx.Response(200, json=CONAF_GEOJSON),
        ]
        features, warnings = await self._client().fetch_incendios()

        assert len(features) == 5
        assert any("WHERE" in warning for warning in warnings)
        assert route.call_count == 2

    async def test_si_la_fuente_no_responde_falla_ruidosamente(self) -> None:
        respx.get("https://conaf.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(CollectorError):
            await self._client().fetch_incendios(where="1=1")

    async def test_sin_fuentes_declaradas(self) -> None:
        with pytest.raises(CollectorError, match="CONAF_SOURCES"):
            ConafClient(sources=[])


class TestMetadatosDelCollector:
    def test_identidad_y_cadencia(self) -> None:
        assert ConafCollector.name == "conaf_incendios"
        assert ConafCollector.source is EventSource.CONAF
        assert ConafCollector.poll_interval_seconds() > 0
