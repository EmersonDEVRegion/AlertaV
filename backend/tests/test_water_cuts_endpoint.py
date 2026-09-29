"""`GET /events/water-cuts/geojson`: los cortes de agua vigentes para el mapa.

Las filas se arman con el propio collector sobre las capturas reales del 23-09
(`test_esval_water`), así que si cambia lo que el collector escribe, estos
tests se enteran.
"""

from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.collectors.water import esval_worker
from app.collectors.water.esval_parser import parse_corte, parse_zonas_kml, unir
from app.core.config import settings
from app.services.water_cut_service import (
    MARGEN_VIGENCIA,
    WaterCutService,
    _https,
    feature_de_corte,
)
from tests.test_esval_water import AHORA, API_CORTES, KML_ZONAS, collector, registros_reales


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(esval_worker, "_ahora", lambda: AHORA)
    return AHORA


def _filas_reales() -> list[SimpleNamespace]:
    """Las dos filas que el collector escribe con las capturas: Viña y Quilpué."""
    cortes = unir([c for c in map(parse_corte, registros_reales()) if c], parse_zonas_kml(KML_ZONAS))
    eventos = collector().normalize(cortes)
    return [
        SimpleNamespace(
            public_id=uuid4(),
            raw_data=evento.raw_data,
            lat=evento.lat,
            lon=evento.lon,
            commune=None,
        )
        for evento in eventos
    ]


def _por_sisda(filas: list[SimpleNamespace]) -> dict[str, dict[str, Any]]:
    salida = {}
    for fila in filas:
        feature = feature_de_corte(fila)  # type: ignore[arg-type]
        assert feature is not None
        salida[feature.properties["sisda"]] = feature.model_dump()
    return salida


# --- La feature -------------------------------------------------------------------


def test_la_feature_trae_lo_que_muestra_el_mapa_y_nada_mas(reloj):
    features = _por_sisda(_filas_reales())
    vina = features["2916567"]

    assert vina["geometry"]["type"] == "Point"
    lon, lat = vina["geometry"]["coordinates"]
    assert -71.7 < lon < -71.4 and -33.1 < lat < -32.9

    props = vina["properties"]
    assert set(props) == {
        "public_id", "sisda", "comuna", "tipo", "programado", "motivo", "calles",
        "sector", "inicio", "fin", "suministro_alternativo", "url_mapa", "visto_en",
    }
    assert props["comuna"] == "Viña del Mar"
    assert props["tipo"] == "emergencia"
    assert props["programado"] is False
    assert props["motivo"] == "Vida util vencida", "el motivo no grita, como en el texto del evento"
    assert props["calles"] == "LOS PENSAMIENTOS"
    assert props["suministro_alternativo"] is False
    assert props["url_mapa"] == "https://tupuntodeagua.esval.cl/?sisda=2916567"
    assert props["visto_en"] == AHORA.isoformat()
    assert props["inicio"].startswith("2026-09-23T14:00")  # 11:00 en Chile

    # Ni el registro original ni los polígonos: ~5 KB por corte que el mapa no usa.
    texto = repr(vina)
    assert "_source_record" not in texto and "anillo" not in texto


def test_el_programado_que_cruza_la_medianoche(reloj):
    quilpue = _por_sisda(_filas_reales())["2912217"]["properties"]
    assert quilpue["programado"] is True
    assert quilpue["sector"] == "Doctor Salas esquina La Obra"
    assert quilpue["fin"].startswith("2026-09-24T05:00")  # 02:00 del 24 en Chile


def test_sin_punto_viaja_con_geometria_nula(reloj):
    fila = _filas_reales()[0]
    fila.lat = fila.lon = None
    feature = feature_de_corte(fila)  # type: ignore[arg-type]
    assert feature is not None and feature.geometry is None


def test_una_fila_sin_bloque_esval_se_omite_sin_tumbar_la_capa(caplog):
    fila = SimpleNamespace(public_id=uuid4(), raw_data={"comuna": "Quilpué"}, lat=None, lon=None)
    assert feature_de_corte(fila) is None  # type: ignore[arg-type]
    assert "sin bloque _esval" in caplog.text


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("http://tupuntodeagua.esval.cl/?sisda=1", "https://tupuntodeagua.esval.cl/?sisda=1"),
        ("https://www.esval.cl/cortes", "https://www.esval.cl/cortes"),
        ("http://tupuntodeagua.aguasdelvalle.cl/?sisda=1", None),
        ("http://esval.cl.example.com/", None),
        ("javascript:alert(1)", None),
        (None, None),
    ],
)
def test_el_enlace_a_esval_va_en_https_y_solo_si_es_de_esval(url, esperado):
    assert _https(url) == esperado


# --- La consulta -------------------------------------------------------------------


def test_la_vigencia_se_mide_con_visto_en_y_los_sin_punto_entran():
    from app.repositories.event_repository import EventRepository

    repo = EventRepository.__new__(EventRepository)
    stmt = repo.water_cuts_stmt(vistos_desde=AHORA, bbox=settings.region_bbox, limit=50)
    compilado = stmt.compile(dialect=postgresql.dialect())
    sql = str(compilado)
    params = dict(compilado.params)

    assert re.search(
        r"CAST\(\(+[\w.]*raw_events\.raw_data -> %\(\w+\)s\)+ ->> %\(\w+\)s\) "
        r"AS TIMESTAMP WITH TIME ZONE\) >=",
        sql,
    )
    assert "_esval" in params.values() and "visto_en" in params.values()
    assert AHORA in params.values()
    # Sin punto entra; con punto, dentro de la caja. AND liga antes que OR.
    assert re.search(
        r"\([\w.]*raw_events\.lat IS NULL OR [\w.]*raw_events\.lon IS NULL OR "
        r"[\w.]*raw_events\.lat BETWEEN",
        sql,
    )
    assert "raw_events.timestamp >=" not in sql, "un corte largo empezó hace días"
    assert re.search(r"ORDER BY [\w.]*raw_events\.timestamp DESC", sql)
    assert 50 in params.values()


# --- El servicio ---------------------------------------------------------------------


class _RepoFalso:
    def __init__(self, filas: list[SimpleNamespace]) -> None:
        self.filas = filas
        self.kwargs: dict[str, Any] | None = None

    async def list_water_cuts(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.filas


def _corrida(status: str, hace: timedelta, error: str | None = None) -> SimpleNamespace:
    inicio = AHORA - hace
    return SimpleNamespace(
        status=status, started_at=inicio, finished_at=inicio + timedelta(seconds=5), error=error
    )


def _servicio(filas, *, ultima, lectura) -> tuple[WaterCutService, _RepoFalso]:
    servicio = WaterCutService.__new__(WaterCutService)
    repo = _RepoFalso(filas)
    servicio.repo = repo  # type: ignore[assignment]

    async def _ultima_corrida(*, solo_lecturas: bool = False):
        return lectura if solo_lecturas else ultima

    servicio._ultima_corrida = _ultima_corrida  # type: ignore[method-assign]
    return servicio, repo


def _vigentes(servicio: WaterCutService):
    import asyncio

    return asyncio.run(servicio.vigentes(ahora=AHORA))


def test_sin_ninguna_lectura_no_hay_capa(reloj):
    """Lo que ve producción hoy: todas las corridas fallaron (Esval no responde fuera de Chile)."""
    fallida = _corrida("failed", timedelta(minutes=3), error="falta ESVAL_PROXY_URL")
    servicio, repo = _servicio(_filas_reales(), ultima=fallida, lectura=None)

    respuesta = _vigentes(servicio)

    assert respuesta.features == [] and respuesta.total == 0
    assert repo.kwargs is None, "sin lectura no hay contra qué medir la vigencia"
    assert respuesta.fuente.ultima_lectura is None
    assert respuesta.fuente.estado == "failing"
    assert respuesta.fuente.detalle == "falta ESVAL_PROXY_URL"


def test_con_lectura_reciente_la_capa_trae_los_vigentes(reloj):
    lectura = _corrida("success", timedelta(minutes=4))
    servicio, repo = _servicio(_filas_reales(), ultima=lectura, lectura=lectura)

    respuesta = _vigentes(servicio)

    assert respuesta.total == 2
    assert respuesta.fuente.estado == "ok"
    assert respuesta.fuente.ultima_lectura == lectura.started_at
    assert repo.kwargs is not None
    assert repo.kwargs["vistos_desde"] == lectura.started_at - MARGEN_VIGENCIA
    assert repo.kwargs["bbox"] == settings.region_bbox


def test_si_el_collector_cae_se_ve_lo_ultimo_conocido_con_aviso(reloj):
    """Un mapa sin cortes diría que no hay cortes: se muestra lo último, marcado."""
    lectura = _corrida("partial", timedelta(minutes=40))
    fallida = _corrida("failed", timedelta(minutes=5), error="esval vía proxy-cl: sin respuesta")
    servicio, repo = _servicio(_filas_reales(), ultima=fallida, lectura=lectura)

    respuesta = _vigentes(servicio)

    assert respuesta.total == 2
    assert respuesta.fuente.estado == "failing"
    assert repo.kwargs is not None
    assert repo.kwargs["vistos_desde"] == lectura.started_at - MARGEN_VIGENCIA


# --- La ruta -------------------------------------------------------------------------


@pytest.fixture
def cliente(reloj):
    from datetime import UTC, datetime

    from app.api.deps import get_water_cut_service
    from app.main import app

    # La ruta usa el reloj de verdad: la corrida tiene que ser de hace 4 minutos
    # respecto de ahora, no de la fecha de las capturas.
    inicio = datetime.now(UTC) - timedelta(minutes=4)
    lectura = SimpleNamespace(
        status="success", started_at=inicio, finished_at=inicio + timedelta(seconds=5), error=None
    )
    servicio, _ = _servicio(_filas_reales(), ultima=lectura, lectura=lectura)
    app.dependency_overrides[get_water_cut_service] = lambda: servicio
    yield TestClient(app)
    app.dependency_overrides.pop(get_water_cut_service, None)


def test_la_ruta_responde_geojson_con_la_fuente(cliente):
    respuesta = cliente.get("/api/v1/events/water-cuts/geojson")

    assert respuesta.status_code == 200, "si responde 422, la capturó /{public_id}"
    cuerpo = respuesta.json()
    assert cuerpo["type"] == "FeatureCollection"
    assert cuerpo["total"] == len(cuerpo["features"]) == 2
    assert cuerpo["fuente"]["collector"] == "esval_cortes_agua"
    assert cuerpo["fuente"]["estado"] == "ok"
    assert cuerpo["fuente"]["ultima_lectura"] is not None
    assert {f["properties"]["sisda"] for f in cuerpo["features"]} == {"2916567", "2912217"}


def test_api_cortes_sigue_siendo_la_captura_real():
    """Ancla: si alguien edita la captura, los números de arriba dejan de valer."""
    assert '"sisda":"2916567"' in API_CORTES
