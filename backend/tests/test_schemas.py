"""Tests de la capa de validación de ingesta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.enums import EventSource, EventType
from app.schemas.event import CitizenReportCreate, EventBatchCreate, EventCreate


def _now() -> datetime:
    return datetime.now(UTC)


class TestEventCreate:
    def test_normaliza_timestamp_a_utc(self) -> None:
        # 14:32 en Chile continental (UTC-4) son las 18:32 UTC
        event = EventCreate(
            timestamp=datetime(2026, 8, 16, 14, 32, tzinfo=timezone(timedelta(hours=-4))),
            source=EventSource.CITIZEN,
            type=EventType.SMOKE,
            lat=-33.025,
            lon=-71.52,
            text="humo en el cerro",
        )
        assert event.timestamp.tzinfo == UTC
        assert event.timestamp.hour == 18

    def test_naive_se_asume_utc(self) -> None:
        event = EventCreate(
            timestamp=datetime(2026, 8, 16, 10, 0),
            source=EventSource.CONAF,
            lat=-33.0,
            lon=-71.5,
        )
        assert event.timestamp.tzinfo == UTC

    def test_aplica_confianza_base_de_la_fuente(self) -> None:
        firms = EventCreate(
            timestamp=_now(), source=EventSource.NASA_FIRMS, lat=-33.0, lon=-71.5
        )
        conaf = EventCreate(
            timestamp=_now(), source=EventSource.CONAF, lat=-33.0, lon=-71.5
        )
        assert firms.confidence == pytest.approx(0.55)
        assert conaf.confidence == pytest.approx(1.0)

    def test_confianza_explicita_gana(self) -> None:
        event = EventCreate(
            timestamp=_now(),
            source=EventSource.NASA_FIRMS,
            lat=-33.0,
            lon=-71.5,
            confidence=0.8,
        )
        assert event.confidence == pytest.approx(0.8)

    def test_rechaza_confianza_fuera_de_rango(self) -> None:
        with pytest.raises(ValidationError):
            EventCreate(
                timestamp=_now(),
                source=EventSource.CITIZEN,
                lat=-33.0,
                lon=-71.5,
                confidence=1.5,
            )

    def test_rechaza_lat_sin_lon(self) -> None:
        with pytest.raises(ValidationError, match="juntos"):
            EventCreate(timestamp=_now(), source=EventSource.CITIZEN, lat=-33.0)

    def test_rechaza_evento_sin_senal(self) -> None:
        with pytest.raises(ValidationError, match="señal correlacionable"):
            EventCreate(timestamp=_now(), source=EventSource.CITIZEN)

    def test_rechaza_timestamp_muy_futuro(self) -> None:
        with pytest.raises(ValidationError, match="futuro"):
            EventCreate(
                timestamp=_now() + timedelta(hours=2),
                source=EventSource.CITIZEN,
                lat=-33.0,
                lon=-71.5,
            )

    def test_rechaza_raw_data_no_objeto(self) -> None:
        with pytest.raises(ValidationError):
            EventCreate(
                timestamp=_now(),
                source=EventSource.CITIZEN,
                lat=-33.0,
                lon=-71.5,
                raw_data=[1, 2, 3],  # type: ignore[arg-type]
            )

    def test_rechaza_campos_desconocidos(self) -> None:
        with pytest.raises(ValidationError):
            EventCreate(
                timestamp=_now(),
                source=EventSource.CITIZEN,
                lat=-33.0,
                lon=-71.5,
                campo_inventado="x",  # type: ignore[call-arg]
            )

    def test_in_region_detecta_valparaiso(self) -> None:
        dentro = EventCreate(
            timestamp=_now(), source=EventSource.CITIZEN, lat=-33.045, lon=-71.62
        )
        fuera = EventCreate(
            timestamp=_now(), source=EventSource.CITIZEN, lat=-53.15, lon=-70.91
        )
        assert dentro.in_region is True
        assert fuera.in_region is False
        # Por defecto NO se rechaza: durante la recolección se guarda y se filtra después.
        assert fuera.has_location is True

    def test_texto_vacio_se_normaliza_a_none(self) -> None:
        event = EventCreate(
            timestamp=_now(),
            source=EventSource.CITIZEN,
            lat=-33.0,
            lon=-71.5,
            text="   ",
            external_id="  ",
        )
        assert event.text is None
        assert event.external_id is None

    def test_to_orm_kwargs_excluye_derivados(self) -> None:
        event = EventCreate(
            timestamp=_now(), source=EventSource.CITIZEN, lat=-33.0, lon=-71.5
        )
        kwargs = event.to_orm_kwargs()
        assert "geom" not in kwargs
        assert "in_region" not in kwargs
        assert set(kwargs) == {
            "timestamp",
            "source",
            "type",
            "lat",
            "lon",
            "text",
            "external_id",
            "confidence",
            "raw_data",
        }


class TestCitizenReport:
    def test_fuerza_source_citizen(self) -> None:
        report = CitizenReportCreate(
            lat=-33.025, lon=-71.52, text="Humo denso en el cerro", type=EventType.SMOKE
        )
        event = report.to_event_create()
        assert event.source is EventSource.CITIZEN
        assert event.confidence == pytest.approx(0.5)
        assert event.raw_data["channel"] == "pwa"

    def test_prohibe_tipos_institucionales(self) -> None:
        """Un cliente no puede declarar una alerta oficial."""
        with pytest.raises(ValidationError, match="no reportable"):
            CitizenReportCreate(
                lat=-33.0, lon=-71.5, text="alerta roja", type=EventType.ALERT
            )

    def test_rechaza_texto_muy_corto(self) -> None:
        with pytest.raises(ValidationError):
            CitizenReportCreate(lat=-33.0, lon=-71.5, text="a")


class TestBatch:
    def test_rechaza_lote_vacio(self) -> None:
        with pytest.raises(ValidationError):
            EventBatchCreate(events=[])

    def test_acepta_lote_valido(self) -> None:
        batch = EventBatchCreate(
            events=[
                EventCreate(
                    timestamp=_now(), source=EventSource.NASA_FIRMS, lat=-33.0, lon=-71.5
                )
            ]
        )
        assert len(batch.events) == 1
