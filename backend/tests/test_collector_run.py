"""Tests de la orquestación de `BaseCollector.run()`.

Lo que se verifica acá es el requisito que hace utilizable la ventana de
recolección: **toda** corrida deja fila en `collector_runs`, haya terminado bien
o mal. Sin eso, un hueco en los datos es ambiguo —¿no hubo emergencias, o el
collector estaba caído?— y esa ambigüedad invalida cualquier calibración
posterior del correlacionador.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from app.collectors.base import BaseCollector
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus, EventSource, EventType
from app.schemas.event import EventCreate, IngestResult


class FakeRun:
    """Sustituto de la fila `CollectorRun`."""

    def __init__(self, source: EventSource, collector: str, params: dict[str, Any]) -> None:
        self.source = source
        self.collector = collector
        self.params = params
        self.status: str | None = None
        self.error: str | None = None


class FakeIngestService:
    """Registra las llamadas en vez de tocar la base de datos."""

    def __init__(self) -> None:
        self.started: FakeRun | None = None
        self.finished: dict[str, Any] | None = None
        self.ingested: list[EventCreate] = []

    async def start_run(self, *, source, collector, params):
        self.started = FakeRun(source, collector, params)
        return self.started

    async def finish_run(self, run, *, status, fetched=0, inserted=0, duplicate=0, error=None):
        self.finished = {
            "run": run,
            "status": status,
            "fetched": fetched,
            "inserted": inserted,
            "duplicate": duplicate,
            "error": error,
        }

    async def ingest_batch(self, events):
        self.ingested = list(events)
        return IngestResult(received=len(events), inserted=len(events), duplicated=0)


def _evento(external_id: str) -> EventCreate:
    return EventCreate(
        timestamp=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        source=EventSource.CONAF,
        type=EventType.WILDFIRE,
        lat=-33.0,
        lon=-71.4,
        text="incendio de prueba",
        external_id=external_id,
        confidence=1.0,
    )


class _Stub(BaseCollector):
    name = "stub"
    source = EventSource.CONAF

    def __init__(self, *, records=(), events=(), error: Exception | None = None) -> None:
        self.session = None
        self.service = FakeIngestService()
        self._records = list(records)
        self._events = list(events)
        self._error = error

    async def fetch(self) -> Sequence[Any]:
        if self._error is not None:
            raise self._error
        return self._records

    def normalize(self, records: Sequence[Any]) -> list[EventCreate]:
        return self._events


class TestTrazabilidad:
    async def test_corrida_exitosa_queda_registrada(self) -> None:
        collector = _Stub(records=[1, 2], events=[_evento("a"), _evento("b")])
        result = await collector.run()

        assert result.status is CollectorStatus.SUCCESS
        assert (result.fetched, result.inserted) == (2, 2)
        assert collector.service.started is not None
        assert collector.service.finished["status"] is CollectorStatus.SUCCESS
        assert collector.service.finished["error"] is None

    async def test_una_fuente_caida_queda_registrada_como_fallo(self) -> None:
        """El caso que justifica toda la tabla: el collector no pudo leer nada."""
        collector = _Stub(error=CollectorError("la fuente no respondió"))
        result = await collector.run()

        assert result.status is CollectorStatus.FAILED
        assert "no respondió" in (result.error or "")
        assert collector.service.finished["status"] is CollectorStatus.FAILED
        assert "no respondió" in collector.service.finished["error"]

    async def test_error_inesperado_tambien_se_registra(self) -> None:
        collector = _Stub(error=ValueError("campo renombrado"))
        result = await collector.run()

        assert result.status is CollectorStatus.FAILED
        assert "ValueError" in (result.error or "")
        assert collector.service.finished["status"] is CollectorStatus.FAILED

    async def test_filas_descartadas_dejan_la_corrida_en_partial(self) -> None:
        collector = _Stub(records=[1, 2, 3], events=[_evento("a")])
        result = await collector.run()

        assert result.rejected == 2
        assert result.status is CollectorStatus.PARTIAL

    async def test_las_advertencias_degradan_a_partial_y_se_persisten(self) -> None:
        """Una degradación no fatal no puede quedar sólo en los logs.

        Que se haya usado una fuente de respaldo, o que hayan llegado filas sin
        fecha, tiene que ser visible en `collector_runs` sin abrir el proceso.
        """
        collector = _Stub(records=[1], events=[_evento("a")])
        collector.warn("se usó una fuente de respaldo")
        result = await collector.run()

        assert result.status is CollectorStatus.PARTIAL
        assert result.details["warnings"] == ["se usó una fuente de respaldo"]
        assert collector.service.finished["status"] is CollectorStatus.PARTIAL
        assert "respaldo" in collector.service.finished["error"]

    async def test_advertencias_no_se_duplican(self) -> None:
        collector = _Stub()
        collector.warn("misma cosa")
        collector.warn("misma cosa")
        assert collector.warnings == ["misma cosa"]

    async def test_corrida_vacia_es_exito_no_fallo(self) -> None:
        """Cero eventos con la fuente respondiendo bien es un resultado válido."""
        collector = _Stub(records=[], events=[])
        result = await collector.run()

        assert result.status is CollectorStatus.SUCCESS
        assert result.fetched == 0
        assert collector.service.finished["status"] is CollectorStatus.SUCCESS


class TestWarningsSinInit:
    def test_warn_funciona_sobre_instancias_creadas_con_new(self) -> None:
        """`normalize()` se testea sobre instancias sin `__init__`, por convención
        del proyecto. El acumulador de advertencias tiene que tolerarlo."""
        collector = _Stub.__new__(_Stub)
        collector.warn("algo")
        assert collector.warnings == ["algo"]


@pytest.mark.parametrize("estado", list(CollectorStatus))
def test_todos_los_estados_son_validos_en_el_check_de_la_tabla(estado: CollectorStatus) -> None:
    """El CHECK de `collector_runs.status` acepta exactamente estos cuatro."""
    assert estado.value in {"running", "success", "partial", "failed"}
