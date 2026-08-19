"""Tests de la capa de validación de ingesta y del contrato de salida."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.enums import (
    EVENT_TO_INCIDENT_TYPE,
    ConfidenceLevel,
    EventSource,
    EventType,
    IncidentType,
    family_of_event,
)
from app.schemas.event import (
    CITIZEN_INITIAL_CONFIDENCE,
    CitizenReportCreate,
    EventBatchCreate,
    EventCreate,
    ReportCategory,
)
from app.schemas.incident import IncidentRead


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
    def test_fuerza_source_y_confianza_del_servidor(self) -> None:
        report = CitizenReportCreate(
            lat=-33.025,
            lon=-71.52,
            text="Humo denso en el cerro",
            category=ReportCategory.FIRE,
        )
        event = report.to_event_create()

        assert event.source is EventSource.CITIZEN
        # Baja y fija: de este número depende el ciclo de vida corto del reporte
        # sin corroborar. No sale de la línea base de la fuente.
        assert event.confidence == pytest.approx(CITIZEN_INITIAL_CONFIDENCE)
        assert event.raw_data["channel"] == "pwa"

    def test_la_categoria_es_obligatoria(self) -> None:
        """Sin categoría el motor no sabe con qué familia correlacionar."""
        with pytest.raises(ValidationError):
            CitizenReportCreate(lat=-33.0, lon=-71.5, text="algo pasa")

    def test_un_cliente_no_puede_declarar_un_tipo_de_dominio(self) -> None:
        """El cliente elige categoría de producto, no señal de dominio.

        `extra="forbid"` cierra la puerta: mandar `type` es un 422. Es la misma
        defensa de siempre —un cliente no puede declararse fuente oficial—
        aplicada al campo nuevo.
        """
        with pytest.raises(ValidationError):
            CitizenReportCreate(
                lat=-33.0,
                lon=-71.5,
                text="alerta roja",
                category=ReportCategory.OTHER,
                type=EventType.ALERT,
            )

    @pytest.mark.parametrize(
        ("categoria", "esperado", "familia"),
        [
            (ReportCategory.FIRE, EventType.SMOKE, "fire"),
            (ReportCategory.TRAFFIC_ACCIDENT, EventType.ACCIDENT, "traffic"),
            (ReportCategory.OTHER, EventType.OTHER, "other"),
        ],
    )
    def test_cada_categoria_cae_en_su_familia(
        self, categoria, esperado, familia
    ) -> None:
        """Requisito de aislamiento: cada categoría en su familia y sin cruces."""
        report = CitizenReportCreate(
            lat=-33.0, lon=-71.5, text="lo que veo", category=categoria
        )
        assert report.event_type is esperado
        assert family_of_event(report.to_event_create().type) == familia

    def test_incendio_ciudadano_no_afirma_un_incendio(self) -> None:
        """La decisión de diseño de `CITIZEN_CATEGORY_TO_TYPE`.

        Una persona que ve humo desde lejos elige "Incendio", pero mapear eso a
        `wildfire` haría que el mapa rotulara "Incendio forestal" un incidente
        sostenido por un solo testigo sin verificar. `smoke` degrada a
        `possible_fire` —"Posible incendio"—, que es lo que el sistema sabe.
        """
        report = CitizenReportCreate(
            lat=-33.0, lon=-71.5, text="veo humo en el cerro",
            category=ReportCategory.FIRE,
        )
        assert report.event_type is EventType.SMOKE
        assert EVENT_TO_INCIDENT_TYPE[report.event_type] is IncidentType.POSSIBLE_FIRE
        # Pero sigue correlacionando con FIRMS y CONAF: misma familia.
        assert family_of_event(report.event_type) == "fire"

    def test_la_categoria_cruda_queda_registrada(self) -> None:
        """Sin ella no se podría calibrar el formulario más adelante."""
        report = CitizenReportCreate(
            lat=-33.0, lon=-71.5, text="choque en la ruta",
            category=ReportCategory.TRAFFIC_ACCIDENT,
        )
        assert report.to_event_create().raw_data["category"] == "traffic_accident"

    def test_rechaza_texto_muy_corto(self) -> None:
        with pytest.raises(ValidationError):
            CitizenReportCreate(
                lat=-33.0, lon=-71.5, text="a", category=ReportCategory.OTHER
            )


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


def _incident(confidence: float, *, confirmed: bool = False) -> IncidentRead:
    now = _now()
    return IncidentRead.model_validate(
        {
            "code": "INC-2026-00001",
            "public_id": uuid4(),
            "type": "possible_fire",
            "status": "active",
            "lat": -33.0,
            "lon": -71.5,
            "confidence": confidence,
            "is_official_confirmed": confirmed,
            "alert_confidence": 0.0,
            "event_count": 1,
            "source_count": 1,
            "sources": ["nasa_firms"],
            "first_seen_at": now,
            "last_seen_at": now,
            "correlated_at": now,
        }
    )


class TestTramoDeConfianzaEnLaSalida:
    """`confidence_level` es lo que la PWA usa para elegir el color."""

    def test_viaja_en_el_payload_serializado(self) -> None:
        payload = _incident(0.40).model_dump(mode="json")
        assert payload["confidence_level"] == "possible"

    def test_esta_declarado_en_el_esquema_de_respuesta(self) -> None:
        """Si no aparece acá, no aparece en el OpenAPI y el cliente no puede
        tipar contra él."""
        schema = IncidentRead.model_json_schema(mode="serialization")
        assert "confidence_level" in schema["properties"]
        assert schema["$defs"]["ConfidenceLevel"]["enum"] == [
            "unsafe",
            "possible",
            "confirmed",
        ]

    @pytest.mark.parametrize(
        ("confidence", "esperado"),
        [
            (0.0, ConfidenceLevel.UNSAFE),
            (0.29, ConfidenceLevel.UNSAFE),
            (0.30, ConfidenceLevel.POSSIBLE),
            (0.60, ConfidenceLevel.POSSIBLE),
            (0.61, ConfidenceLevel.CONFIRMED),
            (1.0, ConfidenceLevel.CONFIRMED),
        ],
    )
    def test_respeta_los_cortes_30_y_60(
        self, confidence: float, esperado: ConfidenceLevel
    ) -> None:
        assert _incident(confidence).confidence_level is esperado

    def test_no_se_deja_arrastrar_por_la_confirmacion_institucional(self) -> None:
        """Los dos ejes son independientes en ambas direcciones.

        Un incidente puede estar `confirmed` por acumulación sin que nadie haya
        ido al lugar; el booleano institucional es el que autoriza a decir
        "CONAF lo confirmó", y no se deduce del tramo.
        """
        acumulado = _incident(0.80, confirmed=False)
        assert acumulado.confidence_level is ConfidenceLevel.CONFIRMED
        assert acumulado.is_official_confirmed is False
