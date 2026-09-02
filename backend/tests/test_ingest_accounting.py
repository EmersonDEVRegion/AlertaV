"""La contabilidad de un lote tiene que cuadrar.

Este archivo existe por una pérdida real. Durante meses el collector de
Chilquinta cerró cada corrida así:

    received: 39 · inserted: 0 · duplicated: 38 · rejected: 0

39 no es 0 + 38. La fila que falta la fundía `_dedupe_batch` antes del INSERT
—hace falta: Postgres aborta si un solo INSERT trae dos veces la misma clave de
conflicto— pero nadie la contaba. `received` medía la lista original y las otras
dos el lote ya colapsado, así que la resta nunca cerraba y no había ni una línea
de log que lo dijera. CGE, MOP, CSN y FIRMS cuadraban perfecto; sólo Chilquinta
perdía exactamente uno, todos los días.

Lo que se prueba acá es la invariante que lo habría delatado el primer día.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.models.enums import EventSource, EventType
from app.repositories.event_repository import _dedupe_batch
from app.schemas.event import EventCreate

AHORA = datetime.now(UTC)


def evento(external_id: str | None, *, texto: str = "x", lat: float = -33.0) -> EventCreate:
    return EventCreate(
        timestamp=AHORA,
        source=EventSource.CHILQUINTA,
        type=EventType.POWER_OUTAGE,
        lat=lat,
        lon=-71.5,
        text=texto,
        external_id=external_id,
        confidence=0.9,
        raw_data={},
    )


# --- 1. El colapso se cuenta -------------------------------------------------


def test_dos_filas_con_la_misma_identidad_dejan_rastro():
    lote, colapsados = _dedupe_batch([evento("chilquinta:abc"), evento("chilquinta:abc")])

    assert len(lote) == 1, "el INSERT no puede llevar la misma clave dos veces"
    assert colapsados == ["chilquinta:abc"], "y el que se fundió tiene que decirse"


def test_la_suma_cierra_siempre():
    """La invariante: nada desaparece sin quedar en alguna cuenta.

    Es la comprobación que le faltaba a la corrida de Chilquinta. Con ella, el
    `39 = 0 + 38` habría sido un fallo el primer día en vez de una resta que
    nadie hacía.
    """
    entrada = [
        evento("a"),
        evento("b"),
        evento("a"),  # repetida
        evento(None),  # ciudadano: no participa de la deduplicación
        evento("b"),  # repetida
        evento("c"),
    ]

    lote, colapsados = _dedupe_batch(entrada)

    assert len(lote) + len(colapsados) == len(entrada)
    assert sorted(colapsados) == ["a", "b"]


def test_gana_la_ultima_ocurrencia_y_no_la_primera():
    """La lectura más reciente de la fuente es la que describe el presente.

    Un corte que en la misma respuesta aparece dos veces con distinto conteo de
    clientes tiene que quedar con el segundo, no con el primero.
    """
    lote, _ = _dedupe_batch([evento("x", texto="viejo"), evento("x", texto="nuevo")])

    assert [e.text for e in lote] == ["nuevo"]


# --- 2. Los reportes ciudadanos no se tocan ----------------------------------


def test_los_eventos_sin_external_id_nunca_se_funden():
    """Dos vecinos reportando el mismo humo son dos señales, no una.

    Esa multiplicidad es justamente lo que sube la confianza del incidente:
    fundirlas destruiría la corroboración que el motor está buscando.
    """
    lote, colapsados = _dedupe_batch([evento(None), evento(None), evento(None)])

    assert len(lote) == 3
    assert colapsados == []


# --- 3. El aviso ------------------------------------------------------------


@pytest.mark.asyncio
async def test_un_lote_con_identidades_repetidas_avisa(caplog):
    """WARNING y no INFO, y con los ids dentro.

    Los ids son lo que separa los dos diagnósticos posibles: si se repiten entre
    corridas, la fuente está devolviendo una fila dos veces y es benigno; si son
    distintos cada vez, son hechos distintos chocando en la construcción del
    `external_id` y se está perdiendo uno real por corrida.
    """
    from app.services.ingest_service import IngestService

    class RepoFalso:
        async def upsert_many(self, events):
            from app.repositories.event_repository import UpsertOutcome

            lote, colapsados = _dedupe_batch(events)
            return UpsertOutcome(inserted=len(lote), duplicated=0, collapsed=colapsados)

    class SesionFalsa:
        async def commit(self):
            return None

    servicio = IngestService.__new__(IngestService)
    servicio.session = SesionFalsa()
    servicio.repo = RepoFalso()

    with caplog.at_level(logging.WARNING, logger="app.services.ingest_service"):
        resultado = await servicio.ingest_batch(
            [evento("chilquinta:dup"), evento("chilquinta:dup"), evento("chilquinta:otro")]
        )

    assert resultado.received == 3
    assert resultado.collapsed == 1
    assert resultado.inserted + resultado.duplicated + resultado.collapsed == 3

    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert avisos, "una identidad repetida tiene que despertar a alguien"
    assert avisos[-1].external_ids == ["chilquinta:dup"]


@pytest.mark.asyncio
async def test_un_lote_limpio_no_avisa_nada(caplog):
    """Un canal que grita siempre deja de comunicar.

    Es la misma lección del `partial` permanente del USGS: el aviso sólo sirve
    si el caso normal es silencioso.
    """
    from app.repositories.event_repository import UpsertOutcome
    from app.services.ingest_service import IngestService

    class RepoFalso:
        async def upsert_many(self, events):
            return UpsertOutcome(inserted=len(events), duplicated=0, collapsed=[])

    class SesionFalsa:
        async def commit(self):
            return None

    servicio = IngestService.__new__(IngestService)
    servicio.session = SesionFalsa()
    servicio.repo = RepoFalso()

    with caplog.at_level(logging.WARNING, logger="app.services.ingest_service"):
        resultado = await servicio.ingest_batch([evento("a"), evento("b")])

    assert resultado.collapsed == 0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
