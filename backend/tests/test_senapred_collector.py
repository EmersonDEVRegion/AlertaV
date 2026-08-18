"""Tests del collector de SENAPRED.

Fixture tomada de la capa real de alertas vigentes (esquema verificado en agosto
de 2026): las alertas son tabulares, sin geometría, y por eso los eventos entran
sin coordenadas y con texto. Es un caso límite del esquema de ingesta que vale la
pena tener cubierto.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.geoservices import parse_feature_collection, parse_source_specs
from app.collectors.senapred.client import SenapredClient
from app.collectors.senapred.collector import (
    SenapredCollector,
    SenapredMapping,
    alert_level,
    build_external_id,
    build_text,
    is_national,
    map_event_type,
)
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType

SENAPRED_GEOJSON = {
    "type": "FeatureCollection",
    "properties": {"exceededTransferLimit": False},
    "features": [
        {
            "type": "Feature",
            "id": 1,
            "geometry": None,
            "properties": {
                "Region": "Valparaíso",
                "Alerta": "Alerta Roja",
                "Razon": "Incendio forestal",
                "Comunas": "Viña del Mar",
                "Ambito": "Comunal",
                "Fecha": 1786320000000,
                "N_alertas": 1,
                "Actualizado": 1787011430284,
                "ObjectId": 1,
                "Evento": "Incendio Forestal",
            },
        },
        {
            "type": "Feature",
            "id": 2,
            "geometry": None,
            "properties": {
                "Region": "Valparaíso",
                "Alerta": "Alerta Roja",
                "Razon": "Evacuación preventiva por incendio forestal",
                "Comunas": "Quilpué, Villa Alemana",
                "Ambito": "Comunal",
                "Fecha": 1786330000000,
                "Actualizado": 1787011430284,
                "ObjectId": 2,
                "Evento": "Evacuación",
            },
        },
        {
            "type": "Feature",
            "id": 3,
            "geometry": None,
            "properties": {
                "Region": "Maule",
                "Alerta": "Alerta Amarilla",
                "Razon": "Crecida",
                "Comunas": "Toda la region",
                "Ambito": "Regional",
                "Fecha": 1785338700000,
                "Actualizado": 1787011430284,
                "ObjectId": 3,
                "Evento": "Viento",
            },
        },
        {
            "type": "Feature",
            "id": 4,
            "geometry": None,
            "properties": {
                "Region": "Biobío",
                "Alerta": "Alerta Temprana Preventiva",
                "Razon": "Evento meteorológico",
                "Comunas": "Toda la region",
                "Ambito": "Regional",
                "Fecha": None,
                "Actualizado": 1787011430284,
                "ObjectId": 4,
                "Evento": "Viento",
            },
        },
        {
            "type": "Feature",
            "id": 5,
            "geometry": None,
            "properties": {
                "Region": "Nacional",
                "Alerta": "Alerta Temprana Preventiva",
                "Razon": "Temporada de incendios forestales",
                "Comunas": "Todo el país",
                "Ambito": "Nacional",
                "Fecha": 1786000000000,
                "Actualizado": 1787011430284,
                "ObjectId": 5,
                "Evento": "Incendio Forestal",
            },
        },
    ],
}


def _features() -> list:
    return parse_feature_collection(SENAPRED_GEOJSON, origin="fixture")


def _collector(mapping: SenapredMapping | None = None) -> SenapredCollector:
    collector = SenapredCollector.__new__(SenapredCollector)
    if mapping is not None:
        collector._mapping = mapping
    return collector


DEFAULT_MAPPING = SenapredMapping(regions=("Valparaíso",), include_national=True)


class TestClasificacion:
    def test_nivel_de_alerta(self) -> None:
        features = _features()
        assert alert_level(features[0]) == "roja"
        assert alert_level(features[2]) == "amarilla"
        assert alert_level(features[3]) == "temprana_preventiva"

    def test_evacuacion_se_distingue_de_alerta(self) -> None:
        features = _features()
        assert map_event_type(features[0]) is EventType.ALERT
        assert map_event_type(features[1]) is EventType.EVACUATION

    def test_nunca_produce_wildfire(self) -> None:
        """SENAPRED declara la respuesta del Estado; no confirma el fenómeno.

        Promover una alerta a incendio confirmado inflaría artificialmente la
        confianza del incidente que arme después el correlacionador.
        """
        assert all(map_event_type(f) is not EventType.WILDFIRE for f in _features())

    def test_ambito_nacional(self) -> None:
        features = _features()
        assert is_national(features[4]) is True
        assert is_national(features[0]) is False


class TestExternalId:
    def test_es_determinista(self) -> None:
        assert build_external_id(_features()[0]) == build_external_id(_features()[0])

    def test_ignora_la_marca_de_actualizacion(self) -> None:
        """La capa cambia `Actualizado` en cada refresco.

        Si entrara en el ID, cada corrida sembraría una alerta nueva y el mapa se
        llenaría de duplicados de la misma emergencia.
        """
        original = _features()[0]
        modificado = parse_feature_collection(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": {
                            **SENAPRED_GEOJSON["features"][0]["properties"],
                            "Actualizado": 1787099999999,
                        },
                    }
                ],
            },
            origin="fixture",
        )[0]
        assert build_external_id(original) == build_external_id(modificado)

    def test_alertas_distintas_no_colisionan(self) -> None:
        ids = {build_external_id(feature) for feature in _features()}
        assert len(ids) == 5


class TestNormalize:
    def test_filtra_por_region_e_incluye_las_nacionales(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        # 2 de Valparaíso + 1 nacional. Maule y Biobío quedan fuera.
        assert len(events) == 3
        assert not any("Maule" in (event.text or "") for event in events)

    def test_puede_excluir_las_nacionales(self) -> None:
        mapping = SenapredMapping(regions=("Valparaíso",), include_national=False)
        assert len(_collector(mapping).normalize(_features())) == 2

    def test_fuente_confianza_y_tipos(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        assert all(event.source is EventSource.SENAPRED for event in events)
        assert all(event.confidence == pytest.approx(1.0) for event in events)
        assert {event.type for event in events} == {EventType.ALERT, EventType.EVACUATION}

    def test_timestamp_de_declaracion_en_utc(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        roja = next(e for e in events if "Viña del Mar" in (e.text or ""))
        assert roja.timestamp == datetime(2026, 8, 10, 0, 0, tzinfo=UTC)

    def test_sin_coordenadas_pero_con_texto(self) -> None:
        """La capa es tabular. Inventar un centroide sería peor que no tenerlo:
        el correlacionador lo trataría como una ubicación real."""
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        assert all(event.lat is None and event.lon is None for event in events)
        assert all(event.text for event in events)

    def test_nivel_normalizado_en_raw_data(self) -> None:
        events = _collector(DEFAULT_MAPPING).normalize(_features())
        roja = next(e for e in events if "Viña del Mar" in (e.text or ""))
        assert roja.raw_data["_alert_level"] == "roja"
        assert roja.raw_data["Razon"] == "Incendio forestal"

    def test_sin_fecha_de_declaracion_usa_la_de_actualizacion(self) -> None:
        """Varias alertas vigentes llegan con `Fecha` nula.

        Descartarlas sería perder alertas rojas reales. Se usa la marca de
        actualización de la capa como primer avistamiento; el upsert no reescribe
        `timestamp`, así que no se desplaza en cada corrida.
        """
        mapping = SenapredMapping(regions=("Biobío",), include_national=False)
        events = _collector(mapping).normalize(_features())
        assert len(events) == 1
        assert events[0].timestamp == datetime(
            2026, 8, 18, 0, 3, 50, 284000, tzinfo=UTC
        )

    def test_descarta_alertas_sin_ninguna_fecha_y_lo_advierte(self) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {
                        "Region": "Valparaíso",
                        "Alerta": "Alerta Roja",
                        "Evento": "Incendio Forestal",
                        "Fecha": None,
                        "Actualizado": None,
                    },
                }
            ],
        }
        collector = _collector(DEFAULT_MAPPING)
        events = collector.normalize(parse_feature_collection(payload, origin="fixture"))
        assert events == []
        assert any("sin fecha" in warning for warning in collector.warnings)

    def test_advierte_si_el_nivel_es_irreconocible(self) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {
                        "Region": "Valparaíso",
                        "Alerta": "Nivel 3",
                        "Evento": "Incendio Forestal",
                        "Fecha": 1786320000000,
                    },
                }
            ],
        }
        collector = _collector(DEFAULT_MAPPING)
        collector.normalize(parse_feature_collection(payload, origin="fixture"))
        assert any("nomenclatura" in warning for warning in collector.warnings)

    def test_texto_legible(self) -> None:
        texto = build_text(_features()[0])
        assert "Alerta Roja" in texto
        assert "Viña del Mar" in texto
        assert "SENAPRED" in texto


@respx.mock
class TestClient:
    def _client(self) -> SenapredClient:
        return SenapredClient(
            sources=parse_source_specs("arcgis|https://senapred.test/FeatureServer|0"),
            timeout=5,
        )

    async def test_descarga_y_parsea(self) -> None:
        respx.get("https://senapred.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, json=SENAPRED_GEOJSON)
        )
        features, warnings = await self._client().fetch_alertas()
        assert len(features) == 5
        assert warnings == []

    async def test_cero_alertas_en_el_pais_queda_anotado(self) -> None:
        """No es un error, pero repetido corrida tras corrida delata una capa
        congelada."""
        respx.get("https://senapred.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        )
        features, warnings = await self._client().fetch_alertas()
        assert features == []
        assert any("sin ninguna alerta" in warning for warning in warnings)

    async def test_respuesta_html_no_se_confunde_con_vacio(self) -> None:
        respx.get("https://senapred.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, text="<html><body>Mantenimiento</body></html>")
        )
        with pytest.raises(CollectorError, match="ninguna de las fuentes"):
            await self._client().fetch_alertas()


class TestMetadatosDelCollector:
    def test_identidad_y_cadencia(self) -> None:
        assert SenapredCollector.name == "senapred_alertas"
        assert SenapredCollector.source is EventSource.SENAPRED
        assert SenapredCollector.poll_interval_seconds() > 0
