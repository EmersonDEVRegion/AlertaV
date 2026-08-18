"""Collector de NASA FIRMS.

Regla del proyecto, incrustada en el mapeo: una anomalía térmica **no** es un
incendio confirmado. Por eso todo evento producido aquí sale con
`type = thermal_anomaly`, nunca `wildfire`. La promoción a incendio la decide
el motor de correlación cuando aparece una fuente institucional que lo respalde.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.firms.client import FirmsClient
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: VIIRS entrega confianza categórica; MODIS un porcentaje 0–100.
#: Los valores se mantienen deliberadamente moderados: FIRMS es señal de
#: corroboración, no evidencia de incendio confirmado.
_VIIRS_CONFIDENCE = {"l": 0.30, "n": 0.55, "h": 0.80}
_MODIS_CONFIDENCE_FLOOR = 0.10
_MODIS_CONFIDENCE_CEILING = 0.85


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def map_confidence(raw_confidence: Any) -> float:
    """Normaliza la confianza de FIRMS a [0,1].

    VIIRS: 'l' | 'n' | 'h' (low / nominal / high).
    MODIS: entero 0–100.
    """
    if raw_confidence is None:
        return 0.5

    token = str(raw_confidence).strip().lower()
    if token in _VIIRS_CONFIDENCE:
        return _VIIRS_CONFIDENCE[token]

    numeric = _as_float(token)
    if numeric is None:
        return 0.5

    scaled = numeric / 100.0
    return max(_MODIS_CONFIDENCE_FLOOR, min(_MODIS_CONFIDENCE_CEILING, scaled))


def parse_acquisition(acq_date: str, acq_time: Any) -> datetime:
    """Combina `acq_date` (YYYY-MM-DD) y `acq_time` (HHMM UTC) en un datetime UTC.

    FIRMS entrega `acq_time` sin ceros a la izquierda: "24" son las 00:24.
    """
    token = str(acq_time or "0").strip()
    if not token.isdigit():
        raise ValueError(f"acq_time inválido: {acq_time!r}")

    padded = token.zfill(4)
    hour, minute = int(padded[:2]), int(padded[2:])
    if hour > 23 or minute > 59:
        raise ValueError(f"acq_time fuera de rango: {acq_time!r}")

    date = datetime.strptime(acq_date.strip(), "%Y-%m-%d")
    return date.replace(hour=hour, minute=minute, tzinfo=UTC)


def build_external_id(record: dict[str, Any], *, lat: float, lon: float) -> str:
    """ID determinista: FIRMS no entrega uno propio.

    Se hashean los atributos que identifican unívocamente una detección
    (sensor + satélite + instante + píxel). Reejecutar el collector sobre la
    misma ventana produce los mismos IDs → el upsert no duplica nada.
    """
    parts = "|".join(
        [
            str(record.get("_sensor", "")),
            str(record.get("satellite", "")),
            str(record.get("instrument", "")),
            str(record.get("acq_date", "")),
            str(record.get("acq_time", "")),
            f"{lat:.5f}",
            f"{lon:.5f}",
        ]
    )
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]
    return f"firms:{digest}"


def build_text(record: dict[str, Any]) -> str:
    """Descripción legible. Deja explícito que no es un incendio confirmado."""
    sensor = record.get("_sensor", "FIRMS")
    frp = _as_float(record.get("frp"))
    daynight = "día" if str(record.get("daynight", "")).upper() == "D" else "noche"
    frp_text = f", FRP {frp:.1f} MW" if frp is not None else ""
    return (
        f"Anomalía térmica detectada por {sensor} ({daynight}){frp_text}. "
        f"Detección satelital sin confirmar."
    )


class FirmsCollector(BaseCollector):
    """Consulta los sensores configurados sobre la Región de Valparaíso."""

    name = "nasa_firms_area"
    source = EventSource.NASA_FIRMS

    def __init__(
        self,
        session,
        *,
        sensors: Sequence[str] | None = None,
        day_range: int | None = None,
    ) -> None:
        super().__init__(session)
        self.sensors = list(sensors or settings.FIRMS_SOURCES)
        self.day_range = day_range or settings.FIRMS_DAY_RANGE
        self.bbox = settings.region_bbox.as_firms_param()
        self.client = FirmsClient()
        self._sensor_errors: list[str] = []

    def run_params(self) -> dict[str, Any]:
        return {
            "sensors": self.sensors,
            "bbox": self.bbox,
            "day_range": self.day_range,
        }

    async def fetch(self) -> Sequence[dict[str, Any]]:
        """Consulta cada sensor. Un sensor caído no aborta los demás."""
        records: list[dict[str, Any]] = []
        self._sensor_errors = []

        for sensor in self.sensors:
            try:
                rows = await self.client.fetch_area(
                    sensor=sensor, bbox=self.bbox, day_range=self.day_range
                )
            except CollectorError as exc:
                self._sensor_errors.append(f"{sensor}: {exc.message}")
                logger.warning(
                    "sensor FIRMS no disponible",
                    extra={"sensor": sensor, "error": exc.message},
                )
                continue

            for row in rows:
                row["_sensor"] = sensor
                records.append(row)

        if self._sensor_errors and not records:
            raise CollectorError(
                "ningún sensor de FIRMS respondió",
                detail={"errors": self._sensor_errors},
            )
        return records

    def normalize(self, records: Sequence[dict[str, Any]]) -> list[EventCreate]:
        """Mapea detecciones FIRMS a eventos del dominio. Función pura."""
        events: list[EventCreate] = []

        for record in records:
            lat = _as_float(record.get("latitude"))
            lon = _as_float(record.get("longitude"))
            if lat is None or lon is None:
                logger.debug("detección FIRMS sin coordenadas", extra={"record": record})
                continue

            try:
                timestamp = parse_acquisition(
                    str(record.get("acq_date", "")), record.get("acq_time")
                )
            except ValueError as exc:
                logger.debug("detección FIRMS con fecha inválida", extra={"error": str(exc)})
                continue

            raw_data = {k: v for k, v in record.items() if not k.startswith("_")}
            raw_data["sensor"] = record.get("_sensor")

            try:
                events.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=EventSource.NASA_FIRMS,
                        type=EventType.THERMAL_ANOMALY,
                        lat=lat,
                        lon=lon,
                        text=build_text(record),
                        external_id=build_external_id(record, lat=lat, lon=lon),
                        confidence=map_confidence(record.get("confidence")),
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "detección FIRMS descartada en validación",
                    extra={"error": str(exc)},
                )

        return events
