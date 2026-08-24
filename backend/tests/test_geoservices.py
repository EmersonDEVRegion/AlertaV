"""Tests del transporte y el parseo compartidos por CONAF y SENAPRED.

El objetivo de casi todos estos casos es el mismo: comprobar que una respuesta
que el sistema no entiende produce un error visible y no una lista vacía. Un
collector que reporta `success` con cero eventos durante una semana es el peor
modo de falla de este proyecto, porque el hueco en los datos se confunde con la
ausencia de emergencias.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.geoservices import (
    ArcGisFeatureClient,
    FailoverFetcher,
    GeoFeature,
    SourceSpec,
    normalise_text,
    parse_feature_collection,
    parse_source_specs,
    parse_timestamp,
    request_json,
    resolve_coordinates,
)
from app.core.exceptions import CollectorError

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": 308638750,
            "geometry": {"type": "Point", "coordinates": [-71.1010818, -32.8603897]},
            "properties": {"nombre": "LOS MAITENES", "region": "Valparaíso"},
        }
    ],
}


class TestParseTimestamp:
    def test_epoch_milisegundos_de_arcgis(self) -> None:
        moment = parse_timestamp(1782998520000)
        assert moment == datetime(2026, 7, 2, 13, 22, tzinfo=UTC)

    def test_epoch_segundos(self) -> None:
        assert parse_timestamp(1782998520) == datetime(2026, 7, 2, 13, 22, tzinfo=UTC)

    def test_iso_con_zona(self) -> None:
        assert parse_timestamp("2026-08-16T14:32:00Z") == datetime(
            2026, 8, 16, 14, 32, tzinfo=UTC
        )

    def test_iso_sin_zona_se_asume_utc(self) -> None:
        """Mismo criterio que `EventCreate`: naive es UTC, no zona local adivinada."""
        assert parse_timestamp("2026-08-16 14:32:00").tzinfo == UTC

    def test_formato_chileno(self) -> None:
        assert parse_timestamp("16-08-2026 14:32").day == 16

    def test_offset_configurable(self) -> None:
        """Permite corregir fuentes que publican hora local etiquetada como UTC."""
        base = parse_timestamp(1782998520000)
        corregido = parse_timestamp(1782998520000, offset_minutes=-240)
        assert (corregido - base).total_seconds() == 240 * 60

    def test_valores_inutilizables(self) -> None:
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None
        assert parse_timestamp("no es fecha") is None
        assert parse_timestamp(True) is None


class TestCoordenadas:
    def test_orden_geojson_estandar(self) -> None:
        assert resolve_coordinates(-71.1, -32.86, origin="t") == (-32.86, -71.1)

    def test_detecta_ejes_invertidos(self) -> None:
        """WFS 1.1.0 con EPSG:4326 suele emitir lat/lon en vez de lon/lat."""
        assert resolve_coordinates(-32.86, -71.1, origin="t") == (-32.86, -71.1)

    def test_no_corrige_si_ambas_lecturas_son_implausibles(self) -> None:
        """Ante la duda se conserva el estándar: un punto raro es visible; uno
        'corregido' en silencio, no."""
        assert resolve_coordinates(10.0, 5.0, origin="t") == (5.0, 10.0)

    def test_faltan_coordenadas(self) -> None:
        assert resolve_coordinates(None, -32.0, origin="t") == (None, None)


class TestParseFeatureCollection:
    def test_coleccion_valida(self) -> None:
        features = parse_feature_collection(FEATURE_COLLECTION, origin="t")
        assert len(features) == 1
        assert features[0].lat == pytest.approx(-32.8603897)
        assert features[0].lon == pytest.approx(-71.1010818)
        assert features[0].feature_id == "308638750"

    def test_coleccion_vacia_es_legitima(self) -> None:
        payload = {"type": "FeatureCollection", "features": []}
        assert parse_feature_collection(payload, origin="t") == []

    def test_sin_clave_features_es_error(self) -> None:
        """Si desapareciera 'features', devolver [] equivaldría a inventar que no
        hubo emergencias."""
        with pytest.raises(CollectorError, match="features"):
            parse_feature_collection({"type": "FeatureCollection"}, origin="t")

    def test_error_de_arcgis_con_http_200(self) -> None:
        payload = {"error": {"code": 400, "message": "Invalid field: estado"}}
        with pytest.raises(CollectorError, match="Invalid field"):
            parse_feature_collection(payload, origin="t")

    def test_payload_que_no_es_objeto(self) -> None:
        with pytest.raises(CollectorError):
            parse_feature_collection([1, 2, 3], origin="t")

    def test_poligono_usa_punto_representativo(self) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-71.2, -33.0], [-71.0, -33.0], [-71.0, -32.8], [-71.2, -32.8]]
                        ],
                    },
                    "properties": {},
                }
            ],
        }
        feature = parse_feature_collection(payload, origin="t")[0]
        assert feature.lat == pytest.approx(-32.9)
        assert feature.lon == pytest.approx(-71.1)

    def test_feature_sin_geometria(self) -> None:
        payload = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None, "properties": {"a": 1}}],
        }
        feature = parse_feature_collection(payload, origin="t")[0]
        assert feature.has_location is False
        assert feature.properties == {"a": 1}


class TestGeoFeatureGet:
    def test_alias_y_case_insensitive(self) -> None:
        feature = GeoFeature(properties={"ESTADO": "En Combate"})
        assert feature.get("estado", "situacion") == "En Combate"

    def test_primer_alias_no_vacio(self) -> None:
        feature = GeoFeature(properties={"estado": "  ", "situacion": "Controlado"})
        assert feature.get("estado", "situacion") == "Controlado"

    def test_default(self) -> None:
        assert GeoFeature(properties={}).get("x", default="?") == "?"


class TestSourceSpecs:
    def test_declaracion_simple(self) -> None:
        specs = parse_source_specs("arcgis|https://host/FeatureServer|0")
        assert specs == [SourceSpec(kind="arcgis", url="https://host/FeatureServer", layer="0")]

    def test_cadena_de_respaldos(self) -> None:
        specs = parse_source_specs(
            "arcgis|https://a/FeatureServer|0; wfs|https://b/wfs|ns:capa"
        )
        assert [spec.kind for spec in specs] == ["arcgis", "wfs"]

    def test_kind_desconocido(self) -> None:
        with pytest.raises(ValueError, match="kind desconocido"):
            parse_source_specs("soap|https://host")

    def test_declaracion_incompleta(self) -> None:
        with pytest.raises(ValueError, match="mal declarada"):
            parse_source_specs("arcgis")

    def test_vacio(self) -> None:
        assert parse_source_specs("") == []
        assert parse_source_specs(None) == []


class TestNormaliseText:
    def test_ignora_tildes_y_mayusculas(self) -> None:
        assert normalise_text("VALPARAÍSO") == normalise_text("Valparaiso") == "valparaiso"

    def test_colapsa_espacios(self) -> None:
        assert normalise_text("  Región   de  Valparaíso ") == "region de valparaiso"


@respx.mock
class TestRequestJson:
    async def test_html_en_vez_de_json(self) -> None:
        """Un portal caído devuelve HTML con HTTP 200. Eso no es 'cero eventos'."""
        respx.get("https://x.test/q").mock(
            return_value=httpx.Response(200, text="<!DOCTYPE html><html>502</html>")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CollectorError, match="HTML"):
                await request_json(client, "https://x.test/q", {}, origin="t", retries=0)

    async def test_json_invalido(self) -> None:
        respx.get("https://x.test/q").mock(return_value=httpx.Response(200, text="{roto"))
        async with httpx.AsyncClient() as client:
            with pytest.raises(CollectorError, match="JSON"):
                await request_json(client, "https://x.test/q", {}, origin="t", retries=0)

    async def test_reintenta_ante_5xx(self) -> None:
        route = respx.get("https://x.test/q").mock(
            side_effect=[
                httpx.Response(503, text="unavailable"),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        async with httpx.AsyncClient() as client:
            payload = await request_json(
                client, "https://x.test/q", {}, origin="t", retries=1, backoff=0
            )
        assert payload == {"ok": True}
        assert route.call_count == 2

    async def test_no_reintenta_ante_4xx(self) -> None:
        """Un 400 es un contrato roto: reintentarlo sólo retrasa el diagnóstico."""
        route = respx.get("https://x.test/q").mock(
            return_value=httpx.Response(400, text="bad where clause")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CollectorError, match="400"):
                await request_json(
                    client, "https://x.test/q", {}, origin="t", retries=3, backoff=0
                )
        assert route.call_count == 1

    async def test_el_query_string_de_la_url_sobrevive_a_params_vacio(self) -> None:
        """La trampa de httpx: `params={}` **borra** la query que trae la URL.

            client.get("https://host/x?emp=006", params={})
            → https://host/x          ← el emp=006 desapareció

        Varios collectors llaman con `{}` porque su filtro ya viene dentro de la
        URL configurada. Sin la guarda, ese filtro se perdía en silencio y la
        fuente devolvía el catálogo completo: un fallo que no rompe nada y sólo
        trae de más, que es de los que tardan meses en notarse.
        """
        route = respx.get("https://x.test/mapas").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient() as client:
            await request_json(
                client, "https://x.test/mapas?emp=006", {}, origin="t", retries=0
            )
        assert "emp=006" in str(route.calls[0].request.url)


@respx.mock
class TestMetodoCabecerasYCuerpo:
    """El transporte admite POST y cabeceras propias sin perder lo demás.

    Existe porque no todas las fuentes son capas abiertas: el visor de
    Chilquinta consulta su backend por POST y exige una API key estática. Que
    eso viva acá y no en el collector es lo que le deja heredar reintentos,
    detección de HTML y conversión de errores a `CollectorError`.
    """

    async def test_por_defecto_sigue_siendo_un_get_sin_cuerpo(self) -> None:
        """El refactor no puede cambiar cómo consultan las fuentes de siempre."""
        route = respx.get("https://x.test/q").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient() as client:
            await request_json(client, "https://x.test/q", {}, origin="t", retries=0)

        peticion = route.calls[0].request
        assert peticion.method == "GET"
        assert peticion.content == b""

    async def test_post_con_cabeceras_y_cuerpo_json(self) -> None:
        route = respx.post("https://x.test/obtiene").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with httpx.AsyncClient() as client:
            await request_json(
                client,
                "https://x.test/obtiene",
                {},
                origin="t",
                retries=0,
                method="POST",
                headers={"x-api-key": "abc123"},
                json_body={"codEmp": "006"},
            )

        peticion = route.calls[0].request
        assert peticion.headers["x-api-key"] == "abc123"
        assert json.loads(peticion.content) == {"codEmp": "006"}
        assert peticion.headers["content-type"] == "application/json"

    async def test_un_post_tambien_hereda_los_reintentos_ante_5xx(self) -> None:
        """Cambiar de verbo no puede costar la resiliencia.

        Estos endpoints son de lectura —una consulta disfrazada de POST—, así
        que repetirlos es idempotente en la práctica.
        """
        route = respx.post("https://x.test/obtiene").mock(
            side_effect=[
                httpx.Response(502, text="bad gateway"),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        async with httpx.AsyncClient() as client:
            payload = await request_json(
                client,
                "https://x.test/obtiene",
                {},
                origin="t",
                retries=1,
                backoff=0,
                method="POST",
                json_body={"codEmp": "006"},
            )

        assert payload == {"ok": True}
        assert route.call_count == 2
        assert json.loads(route.calls[1].request.content) == {"codEmp": "006"}

    async def test_un_post_que_devuelve_html_falla_diciendolo(self) -> None:
        """La detección de HTML no depende del verbo.

        Es el modo de fallo que más caro sale en este proyecto: un portal que
        responde su propia página con HTTP 200 y un collector que lo lee como
        'cero cortes'.
        """
        respx.post("https://x.test/obtiene").mock(
            return_value=httpx.Response(200, text="<!DOCTYPE html><html>login</html>")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CollectorError, match="HTML"):
                await request_json(
                    client,
                    "https://x.test/obtiene",
                    {},
                    origin="t",
                    retries=0,
                    method="POST",
                    json_body={"codEmp": "006"},
                )

    async def test_un_401_no_se_reintenta_y_lo_dice(self) -> None:
        """Una API key mal configurada es un contrato roto, no un fallo pasajero."""
        route = respx.post("https://x.test/obtiene").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CollectorError, match="401"):
                await request_json(
                    client,
                    "https://x.test/obtiene",
                    {},
                    origin="t",
                    retries=3,
                    backoff=0,
                    method="POST",
                    headers={"x-api-key": "caducada"},
                )
        assert route.call_count == 1


@respx.mock
class TestArcGisFeatureClient:
    def _spec(self) -> SourceSpec:
        return SourceSpec(kind="arcgis", url="https://x.test/FeatureServer", layer="0")

    def _page(self, ids: list[int], *, exceeded: bool) -> dict:
        return {
            "type": "FeatureCollection",
            "properties": {"exceededTransferLimit": exceeded},
            "features": [
                {
                    "type": "Feature",
                    "id": i,
                    "geometry": {"type": "Point", "coordinates": [-71.0, -33.0]},
                    "properties": {"id": i},
                }
                for i in ids
            ],
        }

    def test_construye_la_url_de_query(self) -> None:
        client = ArcGisFeatureClient()
        assert (
            client.query_url("https://x.test/FeatureServer", "0")
            == "https://x.test/FeatureServer/0/query"
        )
        assert (
            client.query_url("https://x.test/FeatureServer/2", None)
            == "https://x.test/FeatureServer/2/query"
        )
        assert (
            client.query_url("https://x.test/FeatureServer/0/query", "0")
            == "https://x.test/FeatureServer/0/query"
        )

    async def test_pagina_hasta_agotar(self) -> None:
        respx.get("https://x.test/FeatureServer/0/query").mock(
            side_effect=[
                httpx.Response(200, json=self._page([1, 2], exceeded=True)),
                httpx.Response(200, json=self._page([3], exceeded=False)),
            ]
        )
        features = await ArcGisFeatureClient(page_size=2).fetch(self._spec())
        assert [f.properties["id"] for f in features] == [1, 2, 3]

    async def test_error_de_servicio_aborta(self) -> None:
        respx.get("https://x.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, json={"error": {"code": 400, "message": "nope"}})
        )
        with pytest.raises(CollectorError, match="nope"):
            await ArcGisFeatureClient().fetch(self._spec())


@respx.mock
class TestFailoverFetcher:
    async def test_usa_el_respaldo_y_lo_deja_anotado(self) -> None:
        respx.get("https://primaria.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(500, text="boom")
        )
        respx.get("https://respaldo.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(200, json=FEATURE_COLLECTION)
        )
        fetcher = FailoverFetcher(
            parse_source_specs(
                "arcgis|https://primaria.test/FeatureServer|0;"
                "arcgis|https://respaldo.test/FeatureServer|0"
            )
        )
        fetcher.arcgis.timeout = 5
        features = await fetcher.fetch(where="1=1")

        assert len(features) == 1
        assert fetcher.used_fallback is True
        assert "primaria.test" in fetcher.failure_summary()[0]

    async def test_si_todas_fallan_la_corrida_falla(self) -> None:
        respx.get("https://a.test/FeatureServer/0/query").mock(
            return_value=httpx.Response(404, text="gone")
        )
        fetcher = FailoverFetcher(parse_source_specs("arcgis|https://a.test/FeatureServer|0"))
        with pytest.raises(CollectorError, match="ninguna de las fuentes"):
            await fetcher.fetch(where="1=1")

    async def test_sin_fuentes_declaradas(self) -> None:
        with pytest.raises(CollectorError, match="no hay fuentes"):
            await FailoverFetcher([]).fetch()
