"""Tránsito del MTT en la PWA: vigencia, nombres, capa, lista y push opcional."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.api.v1.endpoints import events as events_endpoint
from app.models.enums import EventSource, EventType
from app.repositories.push_repository import TransitoView
from app.services import transito
from app.services.push import radios
from app.services.push.messages import probe_message, transito_message

AHORA = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)

LINEA = {
    "type": "LineString",
    "coordinates": [[-71.55201, -33.02607], [-71.55579, -33.02567], [-71.56155, -33.02482]],
}


def mtt(tipo: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tipo": tipo,
        "via": "Viana",
        "desde": "Sucre",
        "hasta": "Von Schroeders",
        "punto": None,
        "sentido": None,
        "linea": LINEA,
        "ubicacion": {"metodo": "tramo", "largo_m": 900},
        "sin_ubicar": [],
    }
    base.update(extra)
    return base


def evento(tipo: str, *, hace: timedelta = timedelta(minutes=10), **raw: Any) -> SimpleNamespace:
    bloque = raw.pop("_mtt", None) or mtt(tipo)
    return SimpleNamespace(
        public_id=uuid4(),
        timestamp=AHORA - hace,
        source=EventSource.TRANSPORTE_INFORMA,
        type=EventType.ROAD_CLOSURE
        if tipo in {"pista_ocupada", "marcha"}
        else EventType.TRAFFIC_CONDITION,
        lat=-33.0256,
        lon=-71.5558,
        text=f"aviso de {tipo}",
        commune=None,
        external_id=f"mtt:transito:x:{tipo}",
        updated_at=None,
        raw_data={"_mtt": bloque, "comuna": "Viña del Mar", "url": "https://x.com/t/1", **raw},
        confidence=0.8,
    )


# --- Vigencia y nombres ------------------------------------------------------------


@pytest.mark.parametrize(
    ("tipo", "dura"),
    [
        ("congestion", timedelta(minutes=90)),
        ("lento", timedelta(minutes=90)),
        ("semaforo", timedelta(hours=6)),
        ("pista_ocupada", timedelta(hours=12)),
        ("marcha", timedelta(hours=12)),
        ("normalidad", timedelta(minutes=45)),
    ],
)
def test_cada_subtipo_vive_lo_suyo(tipo, dura):
    raw = {"_mtt": mtt(tipo)}
    assert transito.vigente(raw, AHORA - dura + timedelta(minutes=1), AHORA)
    assert not transito.vigente(raw, AHORA - dura - timedelta(minutes=1), AHORA)


def test_la_normalidad_del_mtt_lo_cierra_antes():
    raw = {"_mtt": mtt("congestion"), "_mtt_fin": {"at": AHORA.isoformat()}}
    assert not transito.vigente(raw, AHORA - timedelta(minutes=5), AHORA)


def test_lo_que_no_es_del_transito_nuevo_no_cuenta():
    assert transito.bloque_mtt({}) is None
    assert transito.bloque_mtt({"_mtt": {"tipo": "choque"}}) is None
    assert transito.bloque_mtt(None) is None
    assert not transito.vigente({"_mop": {}}, AHORA, AHORA)


def test_titulos_para_la_lista():
    assert transito.titulo(mtt("congestion")) == "Congestión en Viana, de Sucre a Von Schroeders"
    marcha = mtt("marcha", via="Av. Pedro Montt", desde=None, hasta=None)
    assert transito.titulo(marcha) == "Marcha por Av. Pedro Montt"
    semaforo = mtt("semaforo", desde=None, hasta=None, punto="Quillota")
    assert transito.titulo(semaforo) == "Semáforo apagado en Viana con Quillota"
    hito = mtt("lento", via="Av. España", desde=None, hasta=None, punto="Caleta Abarca")
    assert transito.tramo_texto(hito) == "Av. España, Caleta Abarca"
    assert transito.titulo(mtt("normalidad", via=None)) == "Tránsito normal"


# --- Push opcional -------------------------------------------------------------------


def test_avisan_sólo_los_que_cambian_qué_hacer():
    assert transito.avisa(mtt("congestion"))
    assert transito.avisa(mtt("semaforo", ubicacion={"metodo": "punto"}))
    assert transito.avisa(mtt("marcha", ubicacion=None))
    assert not transito.avisa(mtt("lento"))
    assert not transito.avisa(mtt("normalidad"))
    assert not transito.avisa(None)


def test_una_congestion_sin_tramo_no_avisa():
    """«Congestión en Av. España» sin más es toda la costanera."""
    assert not transito.avisa(mtt("congestion", ubicacion={"metodo": "via_completa"}))
    assert not transito.avisa(mtt("congestion", ubicacion={"metodo": "punto"}))
    assert transito.avisa(mtt("congestion", ubicacion={"metodo": "tramo_esquinas"}))


def test_la_categoria_transito_viene_apagada_y_no_recibe_incidentes():
    assert radios.TRANSITO in radios.CATEGORIAS
    assert radios.por_defecto()[radios.TRANSITO] == 0
    assert radios.ETIQUETAS[radios.TRANSITO] == "Tránsito"
    from app.models.enums import IncidentType

    assert {radios.categoria_de(t) for t in IncidentType} <= set(radios.CATEGORIAS) - {
        radios.TRANSITO,
        radios.WATER,
    }
    assert radios.normalizar({"transito": 2000}) == {"transito": 2000.0}


def test_el_mensaje_de_transito():
    mensaje = transito_message(
        key="mtt:transito:x:1",
        public_id="abc",
        lat=-33.02567,
        lon=-71.55579,
        distance_m=650,
        mtt=mtt("congestion"),
        comuna="Viña del Mar",
        place="Casa",
        now=AHORA,
    )
    assert mensaje.title == "🚦 Congestión a 650 m de Casa"
    assert mensaje.body == "Viana, de Sucre a Von Schroeders · Viña del Mar · MTT"
    assert mensaje.kind == "transito"
    assert mensaje.tag == "transito-mtt:transito:x:1"
    assert mensaje.url.startswith("/?transito=abc&lat=-33.0257")
    assert mensaje.ttl_seconds == 3600


def test_la_prueba_muestra_el_transito_sólo_si_está_encendido():
    apagado = probe_message(radios={**radios.por_defecto()})
    assert "🚦" not in apagado.body
    encendido = probe_message(radios={**radios.por_defecto(), "transito": 2000})
    assert "🚦 2 km" in encendido.body


def test_la_vista_del_notificador_filtra_lo_que_no_avisa():
    def fila(tipo: str, **kw: Any) -> Any:
        e = evento(tipo, **kw)
        return e

    assert TransitoView.de(fila("congestion"), ahora=AHORA) is not None
    assert TransitoView.de(fila("lento"), ahora=AHORA) is None
    viejo = fila("congestion", hace=timedelta(hours=3))
    assert TransitoView.de(viejo, ahora=AHORA) is None
    sin_punto = fila("semaforo")
    sin_punto.lat = None
    assert TransitoView.de(sin_punto, ahora=AHORA) is None


async def test_el_notificador_avisa_transito_a_quien_lo_encendió():
    from tests.test_push_notifier import NOW, FakeRepo, FakeSender, _notifier, _recipient

    fila = evento("semaforo", _mtt=mtt("semaforo", desde=None, hasta=None, punto="Quillota"))
    fila.timestamp = NOW - timedelta(minutes=5)
    vista = TransitoView.de(fila, ahora=NOW)
    assert vista is not None
    repo = FakeRepo(transito=[vista], transito_recipients={vista.key: [_recipient(5, 900)]})
    sender = FakeSender()
    notifier, _ = _notifier(repo, sender)

    resultado = await notifier.run(now=NOW)

    assert resultado.transito_considered == 1
    assert repo.transito_queries[0]["since"] == NOW - timedelta(minutes=30)
    assert [(k, s) for k, s, _ in repo.reserved] == [("transito", vista.key)]
    [(_, payload, _)] = sender.sent
    assert payload["title"] == "🚦 Semáforo apagado a 900 m de tu última ubicación"
    assert payload["body"].startswith("Viana con Quillota · Viña del Mar · MTT")


# --- Capa y lista -------------------------------------------------------------------


def test_la_feature_lleva_la_linea_del_tramo():
    feature = events_endpoint._transito_feature(evento("congestion"))
    assert feature.geometry == LINEA
    props = feature.properties
    assert props["subtipo"] == "congestion"
    assert props["nombre"] == "Congestión"
    assert props["titulo"] == "Congestión en Viana, de Sucre a Von Schroeders"
    assert props["comuna"] == "Viña del Mar"
    assert props["url"] == "https://x.com/t/1"
    assert props["metodo"] == "tramo"
    assert datetime.fromisoformat(props["vence"]) == AHORA - timedelta(minutes=10) + timedelta(
        minutes=90
    )


def test_sin_linea_va_el_punto_y_sin_punto_va_null():
    punto = evento("semaforo", _mtt=mtt("semaforo", linea=None))
    assert events_endpoint._transito_feature(punto).geometry == {
        "type": "Point",
        "coordinates": [-71.5558, -33.0256],
    }
    perdido = evento("lento", _mtt=mtt("lento", linea=None, ubicacion=None))
    perdido.lat = perdido.lon = None
    assert events_endpoint._transito_feature(perdido).geometry is None


class _Repo:
    def __init__(self, filas: list[Any]) -> None:
        self.filas = filas
        self.llamadas: list[dict[str, Any]] = []

    async def list_events(self, **kwargs: Any) -> list[Any]:
        self.llamadas.append(kwargs)
        return self.filas


async def test_el_endpoint_devuelve_sólo_lo_vigente(monkeypatch):
    import app.api.v1.endpoints.events as modulo

    class _Reloj(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return AHORA

    monkeypatch.setattr(modulo, "datetime", _Reloj)
    vigente = evento("congestion", hace=timedelta(minutes=20))
    vencido = evento("lento", hace=timedelta(hours=2))
    cerrado = evento("semaforo", _mtt_fin={"at": AHORA.isoformat()})
    portal = evento("pista_ocupada")
    portal.raw_data = {"url": "https://transporteinforma.cl"}
    verde = evento("normalidad", hace=timedelta(minutes=5))
    repo = _Repo([verde, vigente, vencido, cerrado, portal])

    salida = await modulo.transito_geojson(service=SimpleNamespace(repo=repo), limit=300)

    assert [f.properties["subtipo"] for f in salida.features] == ["normalidad", "congestion"]
    [llamada] = repo.llamadas
    assert llamada["sources"] == [EventSource.TRANSPORTE_INFORMA]
    assert set(llamada["types"]) == {EventType.TRAFFIC_CONDITION, EventType.ROAD_CLOSURE}
    assert llamada["since"] == AHORA - timedelta(hours=12)


async def test_los_cortes_de_ruta_ya_no_repiten_lo_del_transito():
    import app.api.v1.endpoints.events as modulo

    pista = evento("pista_ocupada", hace=timedelta(minutes=5))
    pista.timestamp = datetime.now(UTC) - timedelta(minutes=5)
    portal = evento("pista_ocupada", hace=timedelta(minutes=5))
    portal.timestamp = datetime.now(UTC) - timedelta(minutes=5)
    portal.raw_data = {"url": "https://transporteinforma.cl"}
    service = SimpleNamespace(repo=_Repo([pista, portal]), region_bbox=lambda: None)

    salida = await modulo.road_closures_geojson(service=service, hours=24, source=None, limit=500)

    assert [f.properties["public_id"] for f in salida.features] == [str(portal.public_id)]
