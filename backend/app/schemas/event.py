"""Schemas Pydantic v2 para la ingesta y exposición de eventos.

Contrato de la capa de ingesta. Todo lo que entra al sistema —collectors,
API pública de reportes ciudadanos, workers— pasa por `EventCreate`. Esa es la
única frontera donde se normaliza; a partir de ahí el dato es confiable.

Decisiones:
  * `timestamp` se normaliza SIEMPRE a UTC con tzinfo. Un naive datetime se
    interpreta como UTC y se avisa en el docstring, no se adivina zona local.
  * lat/lon se validan como par: o vienen ambos o ninguno.
  * `confidence` es opcional: si la fuente no la entrega, se aplica la línea
    base de la fuente (`SOURCE_BASE_CONFIDENCE`).
  * Estar fuera de la Región de Valparaíso NO invalida el evento por defecto.
    Durante la ventana de recolección se marca y se decide después
    (`settings.REJECT_OUTSIDE_REGION`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.config import settings
from app.models.enums import SOURCE_BASE_CONFIDENCE, EventSource, EventType

# --- Tipos reutilizables -----------------------------------------------------

Latitude = Annotated[float, Field(ge=-90.0, le=90.0, description="Latitud WGS84")]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0, description="Longitud WGS84")]
Confidence = Annotated[
    float, Field(ge=0.0, le=1.0, description="Confianza de la señal en [0,1]")
]


class EventBase(BaseModel):
    """Campos comunes a la ingesta y a la lectura."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        use_enum_values=False,
        extra="forbid",
    )

    timestamp: datetime = Field(
        ...,
        description="Momento del evento SEGÚN LA FUENTE. Se normaliza a UTC.",
        examples=["2026-08-16T14:32:00-04:00"],
    )
    source: EventSource = Field(..., description="Origen de la señal.")
    type: EventType = Field(
        default=EventType.UNKNOWN,
        description=(
            "Naturaleza de la señal. 'thermal_anomaly' y 'smoke' NO son "
            "incendios confirmados."
        ),
    )
    lat: Latitude | None = None
    lon: Longitude | None = None
    text: str | None = Field(
        default=None,
        max_length=10_000,
        description="Texto libre: transcripción, descripción ciudadana, título de alerta.",
    )
    external_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "ID estable en el sistema de origen. Si la fuente no tiene uno, el "
            "collector debe generar un hash determinista de los atributos que "
            "identifican la observación. Es la clave de la idempotencia."
        ),
    )
    raw_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Payload original íntegro de la fuente. Permite reprocesar sin "
            "volver a consultarla."
        ),
    )

    # --- Normalizadores ------------------------------------------------------

    @field_validator("timestamp")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        """Naive se asume UTC; aware se convierte a UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("timestamp")
    @classmethod
    def _reject_far_future(cls, value: datetime) -> datetime:
        limit = datetime.now(UTC) + timedelta(
            seconds=settings.INGEST_FUTURE_TOLERANCE_SECONDS
        )
        if value > limit:
            raise ValueError(
                f"timestamp en el futuro ({value.isoformat()}); "
                f"tolerancia máxima {settings.INGEST_FUTURE_TOLERANCE_SECONDS}s"
            )
        return value

    @field_validator("text", "external_id")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("raw_data")
    @classmethod
    def _raw_data_must_be_object(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("raw_data debe ser un objeto JSON, no un array ni un escalar")
        return value

    @model_validator(mode="after")
    def _validate_geometry(self) -> EventBase:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat y lon deben venir juntos o ambos ausentes")
        if self.lat is None and self.text is None:
            raise ValueError(
                "un evento sin coordenadas y sin texto no aporta señal correlacionable"
            )
        if (
            settings.REJECT_OUTSIDE_REGION
            and self.lat is not None
            and self.lon is not None
            and not settings.region_bbox.contains(self.lat, self.lon)
        ):
            raise ValueError(
                f"coordenadas ({self.lat}, {self.lon}) fuera de la Región de Valparaíso"
            )
        return self

    # --- Derivados -----------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def in_region(self) -> bool:
        """¿Cae dentro del bounding box de la Región de Valparaíso?

        Informativo. No rechaza el evento salvo que REJECT_OUTSIDE_REGION esté
        activo: durante la recolección inicial es preferible guardar de más.
        """
        if self.lat is None or self.lon is None:
            return False
        return settings.region_bbox.contains(self.lat, self.lon)


class EventCreate(EventBase):
    """Payload de ingesta de un evento.

    Ejemplo (reporte ciudadano):
        {
          "timestamp": "2026-08-16T14:32:00-04:00",
          "source": "citizen",
          "type": "smoke",
          "lat": -33.025,
          "lon": -71.52,
          "text": "Humo denso en el cerro, sector Forestal",
          "raw_data": {"device": "pwa", "accuracy_m": 12}
        }
    """

    confidence: Confidence | None = Field(
        default=None,
        description=(
            "Confianza de esta señal. Si se omite se aplica la línea base de la "
            "fuente. NO es la confianza del incidente."
        ),
    )

    @model_validator(mode="after")
    def _apply_source_baseline(self) -> EventCreate:
        if self.confidence is None:
            self.confidence = SOURCE_BASE_CONFIDENCE.get(self.source, 0.5)
        return self

    def to_orm_kwargs(self) -> dict[str, Any]:
        """Campos listos para construir un `RawEvent`.

        Se excluyen los `computed_field` y `geom`, que es columna generada.
        """
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "type": self.type,
            "lat": self.lat,
            "lon": self.lon,
            "text": self.text,
            "external_id": self.external_id,
            "confidence": self.confidence if self.confidence is not None else 0.5,
            "raw_data": self.raw_data,
        }


class CitizenReportCreate(BaseModel):
    """Ingesta desde la PWA. La fuente la fija el servidor, nunca el cliente.

    Un cliente no puede declararse 'conaf' ni asignarse confianza propia: eso
    sería un vector trivial de falsificación de incidentes.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    lat: Latitude
    lon: Longitude
    type: EventType = Field(
        default=EventType.SMOKE,
        description="Sólo tipos observables por un ciudadano.",
    )
    text: str = Field(..., min_length=3, max_length=2_000)
    reported_at: datetime | None = Field(
        default=None, description="Si se omite, se usa la hora del servidor."
    )
    accuracy_m: float | None = Field(
        default=None, ge=0, le=100_000, description="Precisión GPS informada por el dispositivo."
    )
    media_url: str | None = Field(default=None, max_length=1_000)

    @field_validator("type")
    @classmethod
    def _restrict_citizen_types(cls, value: EventType) -> EventType:
        allowed = {
            EventType.SMOKE,
            EventType.WILDFIRE,
            EventType.STRUCTURAL_FIRE,
            EventType.ACCIDENT,
            EventType.FLOOD,
            EventType.LANDSLIDE,
            EventType.OTHER,
        }
        if value not in allowed:
            raise ValueError(
                f"tipo '{value.value}' no reportable por un ciudadano; "
                f"permitidos: {sorted(t.value for t in allowed)}"
            )
        return value

    def to_event_create(self) -> EventCreate:
        return EventCreate(
            timestamp=self.reported_at or datetime.now(UTC),
            source=EventSource.CITIZEN,
            type=self.type,
            lat=self.lat,
            lon=self.lon,
            text=self.text,
            external_id=None,
            confidence=None,  # línea base de la fuente
            raw_data={
                "channel": "pwa",
                "accuracy_m": self.accuracy_m,
                "media_url": self.media_url,
            },
        )


class EventBatchCreate(BaseModel):
    """Ingesta por lote — lo que usan los collectors."""

    model_config = ConfigDict(extra="forbid")

    events: list[EventCreate] = Field(..., min_length=1)

    @field_validator("events")
    @classmethod
    def _max_batch(cls, value: list[EventCreate]) -> list[EventCreate]:
        if len(value) > settings.INGEST_MAX_BATCH_SIZE:
            raise ValueError(
                f"lote de {len(value)} eventos excede el máximo "
                f"({settings.INGEST_MAX_BATCH_SIZE})"
            )
        return value


# --- Salida ------------------------------------------------------------------


class EventRead(BaseModel):
    """Representación de lectura de un evento."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    timestamp: datetime
    source: EventSource
    type: EventType
    lat: float | None
    lon: float | None
    text: str | None
    external_id: str | None
    confidence: float
    raw_data: dict[str, Any]
    commune: str | None
    province: str | None
    ingested_at: datetime
    processed_at: datetime | None
    incident_id: int | None


class IngestResult(BaseModel):
    """Resultado de una operación de ingesta idempotente."""

    received: int = Field(..., description="Eventos recibidos en el lote.")
    inserted: int = Field(..., description="Eventos nuevos.")
    duplicated: int = Field(..., description="Eventos ya conocidos (mismo source + external_id).")
    rejected: int = Field(default=0, description="Eventos descartados por validación.")
    errors: list[str] = Field(default_factory=list)


class EventStats(BaseModel):
    """Resumen para monitorear la ventana de recolección."""

    total: int
    by_source: dict[str, int]
    by_type: dict[str, int]
    georeferenced: int
    first_event_at: datetime | None
    last_event_at: datetime | None


# --- GeoJSON -----------------------------------------------------------------


class GeoJSONFeature(BaseModel):
    type: str = "Feature"
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    """Salida directamente consumible por MapLibre GL JS en la PWA."""

    type: str = "FeatureCollection"
    features: list[GeoJSONFeature]
