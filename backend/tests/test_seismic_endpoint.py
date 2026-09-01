"""Tests del contrato sísmico.

Cubren lo que puede romperse en silencio: el orden de registro de las rutas, la
preservación de una magnitud nula y el recorte geográfico correcto.

No tocan la base: `SeismicRepository` se sustituye por un doble. Verificar el
SQL del JOIN exige PostGIS y eso vive en los tests de integración.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_seismic_service
from app.main import app
from app.services.seismic_service import SeismicService


def _row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "public_id": "3f2b6c1e-0000-4000-8000-000000000001",
        "timestamp": datetime(2026, 8, 17, 18, 32, tzinfo=UTC),
        "lat": -33.05,
        "lon": -71.62,
        "commune": "Valparaíso",
        "province": "Valparaíso",
        "usgs_id": "us7000abcd",
        "magnitude": 5.8,
        "mag_type": "mww",
        "depth_km": 42.3,
        "place": "offshore Valparaiso, Chile",
        "felt_reports": 214,
        "tsunami": False,
        "pager_alert": "green",
        "significance": 518,
        "review_status": "reviewed",
        "usgs_url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeRepo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.kwargs: dict[str, Any] = {}

    async def list_seismic(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.rows


@pytest.fixture
def client_and_repo() -> Any:
    repo = _FakeRepo(
        [
            _row(),
            _row(usgs_id="us7000efgh", magnitude=3.2, review_status="automatic"),
            # Solución preliminar: el USGS publica antes de calcular la magnitud.
            _row(usgs_id="us7000ijkl", magnitude=None, depth_km=None),
        ]
    )
    service = SeismicService.__new__(SeismicService)
    service.repo = repo  # type: ignore[attr-defined]
    app.dependency_overrides[get_seismic_service] = lambda: service
    yield TestClient(app), repo
    app.dependency_overrides.pop(get_seismic_service, None)


def _declared_paths() -> list[str]:
    """Rutas declaradas, en orden de registro.

    **Por qué no se lee `app.routes`.** Hasta FastAPI 0.115 esa lista era plana:
    `include_router()` copiaba cada subruta al nivel de la aplicación y bastaba
    con recorrerla. Desde Starlette 1.0 ya no — `include_router()` agrega un
    único objeto contenedor (`_IncludedRouter`) que guarda las subrutas dentro y
    **no** expone `.path`. La consecuencia práctica es que
    `next(p for p in paths if p.endswith("/events/seismic"))` deja de encontrar
    nada y el test revienta con `StopIteration`, sin que el endpoint tenga nada
    malo: un fallo del instrumento de medición, no de lo medido.

    El esquema OpenAPI sí es superficie pública y estable, y FastAPI lo arma
    recorriendo las rutas en orden de registro, así que las claves de `paths`
    llegan ordenadas y con el prefijo ya aplicado. Da la misma respuesta en las
    dos versiones.
    """
    return list(app.openapi()["paths"])


class TestSeismicRouting:
    def test_seismic_va_declarada_antes_que_la_ruta_de_detalle(self) -> None:
        """`/events/seismic` va antes que `/events/{public_id}`.

        Si se registraran al revés, FastAPI intentaría leer "seismic" como UUID
        y la ruta devolvería 422 en vez de la lista.
        """
        paths = _declared_paths()
        seismic = next(i for i, p in enumerate(paths) if p.endswith("/events/seismic"))
        detail = next(i for i, p in enumerate(paths) if p.endswith("/events/{public_id}"))
        assert seismic < detail

    def test_seismic_no_entra_por_la_ruta_de_detalle(
        self, client_and_repo: Any
    ) -> None:
        """La misma garantía, comprobada por comportamiento y no por estructura.

        Es la que de verdad protege al usuario: da igual cómo FastAPI guarde sus
        rutas internamente mientras «seismic» no termine en el parseador de
        UUID. Si el orden se invierte, esto devuelve 422 y el test cae — que es
        exactamente el síntoma que vería el frontend.
        """
        client, _ = client_and_repo
        response = client.get("/api/v1/events/seismic")

        assert response.status_code == 200, response.text
        # Un objeto en vez de una lista significaría que respondió el detalle.
        assert isinstance(response.json(), list)


class TestSeismicEndpoint:
    def test_lista_devuelve_magnitud_y_profundidad(self, client_and_repo: Any) -> None:
        client, _ = client_and_repo
        response = client.get("/api/v1/events/seismic")
        assert response.status_code == 200

        first = response.json()[0]
        assert first["magnitude"] == 5.8
        assert first["depth_km"] == 42.3
        assert first["usgs_id"] == "us7000abcd"

    def test_usa_el_bbox_sismico_y_no_el_regional(self, client_and_repo: Any) -> None:
        """El recorte es `usgs_bbox`, más ancho que la Región de Valparaíso.

        Un sismo a 200 km se siente igual; aplicarle el recorte pensado para
        incendios puntuales borraría del mapa los eventos que explican por qué
        tembló.
        """
        client, repo = client_and_repo
        client.get("/api/v1/events/seismic")

        from app.core.config import settings

        west, south, east, north = repo.kwargs["bbox"]
        assert (west, south, east, north) == (
            settings.usgs_bbox.west,
            settings.usgs_bbox.south,
            settings.usgs_bbox.east,
            settings.usgs_bbox.north,
        )
        assert west < settings.region_bbox.west
        assert north > settings.region_bbox.north

    def test_propaga_los_filtros(self, client_and_repo: Any) -> None:
        client, repo = client_and_repo
        client.get(
            "/api/v1/events/seismic"
            "?hours=24&min_magnitude=4.5&max_depth_km=60&tsunami_only=true"
        )
        assert repo.kwargs["min_magnitude"] == 4.5
        assert repo.kwargs["max_depth_km"] == 60.0
        assert repo.kwargs["tsunami_only"] is True

    def test_geojson_preserva_la_magnitud_nula(self, client_and_repo: Any) -> None:
        """Una solución preliminar no puede llegar al mapa como magnitud 0."""
        client, _ = client_and_repo
        collection = client.get("/api/v1/events/seismic/geojson").json()

        preliminary = next(
            f for f in collection["features"] if f["properties"]["usgs_id"] == "us7000ijkl"
        )
        assert preliminary["properties"]["magnitude"] is None

    def test_geojson_ordena_las_coordenadas_lon_lat(self, client_and_repo: Any) -> None:
        client, _ = client_and_repo
        collection = client.get("/api/v1/events/seismic/geojson").json()
        assert collection["features"][0]["geometry"]["coordinates"] == [-71.62, -33.05]

    def test_geojson_solo_lleva_escalares(self, client_and_repo: Any) -> None:
        """MapLibre serializa los objetos anidados: no tiene sentido mandarlos."""
        client, _ = client_and_repo
        collection = client.get("/api/v1/events/seismic/geojson").json()
        for feature in collection["features"]:
            for value in feature["properties"].values():
                assert not isinstance(value, (dict, list))

    def test_geojson_marca_que_un_sismo_no_es_un_incidente(
        self, client_and_repo: Any
    ) -> None:
        client, _ = client_and_repo
        collection = client.get("/api/v1/events/seismic/geojson").json()
        assert all(
            f["properties"]["is_confirmed_incident"] is False
            for f in collection["features"]
        )

    def test_stats_ignora_las_magnitudes_nulas(self, client_and_repo: Any) -> None:
        client, _ = client_and_repo
        stats = client.get("/api/v1/events/seismic/stats").json()
        assert stats["total"] == 3
        assert stats["max_magnitude"] == 5.8
