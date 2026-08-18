"""Collector de SENAPRED — alertas y evacuaciones declaradas oficialmente.

Una alerta de SENAPRED no es la observación de un incendio: es un acto
administrativo del organismo que coordina la respuesta. Por eso se mapea a
`alert` (o `evacuation` cuando el acto es una evacuación) y nunca a `wildfire`,
aunque el motivo declarado sea un incendio forestal. La confirmación del hecho
la aporta CONAF; SENAPRED aporta la respuesta del Estado.

Distinción que importa para el motor de correlación: una alerta roja por incendio
forestal concurrente con detecciones de FIRMS y un incendio de CONAF en la misma
comuna es la señal más fuerte que este sistema puede producir.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.geoservices import (
    GeoFeature,
    normalise_text,
    parse_timestamp,
)
from app.collectors.senapred.client import SenapredClient
from app.core.config import settings
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Organismo oficial que declara el acto: la señal es cierta por definición.
SENAPRED_CONFIDENCE = 1.0

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "region": ("Region", "region", "REGION", "nom_region"),
    "alerta": ("Alerta", "alerta", "tipo_alerta", "nivel"),
    "razon": ("Razon", "razon", "motivo", "causa"),
    "evento": ("Evento", "evento", "tipo_evento", "amenaza"),
    "comunas": ("Comunas", "comunas", "comuna", "COMUNA"),
    "ambito": ("Ambito", "ambito", "alcance", "nivel_territorial"),
    "fecha": ("Fecha", "fecha", "fecha_declaracion", "fecha_inicio"),
    "actualizado": ("Actualizado", "actualizado", "last_edited_date"),
    "id": ("id", "ID", "id_alerta", "codigo"),
}

#: Nivel normalizado a partir del texto de la alerta. Se guarda en `raw_data`
#: para poder filtrar sin volver a parsear strings en cada consulta.
_LEVELS: tuple[tuple[str, str], ...] = (
    ("roja", "roja"),
    ("amarilla", "amarilla"),
    ("temprana preventiva", "temprana_preventiva"),
    ("preventiva", "temprana_preventiva"),
    ("verde", "verde"),
)

#: Palabras que convierten el acto administrativo en una evacuación.
_EVACUATION_MARKERS = ("evacuacion", "evacuar", "evacuación", "desalojo")


@dataclass(frozen=True, slots=True)
class SenapredMapping:
    regions: tuple[str, ...] = ()
    include_national: bool = True
    filter_by_region: bool = True
    time_offset_minutes: int = 0

    @classmethod
    def from_settings(cls) -> SenapredMapping:
        return cls(
            regions=tuple(settings.SENAPRED_REGIONS),
            include_national=settings.SENAPRED_INCLUDE_NATIONAL,
            filter_by_region=settings.SENAPRED_FILTER_BY_REGION,
            time_offset_minutes=settings.SENAPRED_TIME_OFFSET_MINUTES,
        )


def alert_level(feature: GeoFeature) -> str:
    """Nivel normalizado: roja | amarilla | temprana_preventiva | verde | otra."""
    haystack = normalise_text(feature.get(*FIELD_ALIASES["alerta"]))
    for needle, level in _LEVELS:
        if normalise_text(needle) in haystack:
            return level
    return "otra"


def map_event_type(feature: GeoFeature) -> EventType:
    """`evacuation` si el acto ordena evacuar; en cualquier otro caso `alert`.

    Nunca `wildfire`: SENAPRED declara la respuesta, no confirma el fenómeno.
    Promover una alerta a incendio confirmado aquí falsearía la confianza del
    incidente que produzca después el correlacionador.
    """
    haystack = " ".join(
        normalise_text(feature.get(*FIELD_ALIASES[key]))
        for key in ("alerta", "razon", "evento")
    )
    if any(marker in haystack for marker in (normalise_text(m) for m in _EVACUATION_MARKERS)):
        return EventType.EVACUATION
    return EventType.ALERT


def build_external_id(feature: GeoFeature) -> str:
    """ID determinista: la capa no expone un folio de la alerta.

    Se hashean los atributos que identifican el acto administrativo —región,
    comunas, nivel, evento, motivo y fecha de declaración— y deliberadamente NO
    la marca de actualización de la capa, que cambia en cada refresco. Así una
    misma alerta vigente leída cada 10 minutos actualiza su fila en vez de
    sembrar el mapa de duplicados.
    """
    native = feature.get(*FIELD_ALIASES["id"])
    if native is not None:
        return f"senapred:{native}"

    parts = "|".join(
        normalise_text(feature.get(*FIELD_ALIASES[key], default=""))
        for key in ("region", "comunas", "alerta", "evento", "razon", "fecha")
    )
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]
    return f"senapred:h:{digest}"


def build_text(feature: GeoFeature) -> str:
    alerta = feature.get(*FIELD_ALIASES["alerta"], default="Alerta")
    region = feature.get(*FIELD_ALIASES["region"])
    comunas = feature.get(*FIELD_ALIASES["comunas"])
    evento = feature.get(*FIELD_ALIASES["evento"])
    razon = feature.get(*FIELD_ALIASES["razon"])
    ambito = feature.get(*FIELD_ALIASES["ambito"])

    fragmentos = [f"{alerta} vigente."]
    if region:
        fragmentos.append(f"Región: {region}.")
    if comunas:
        fragmentos.append(f"Comunas: {comunas}.")
    motivo = evento or razon
    if motivo:
        detalle = (
            f"{evento} ({razon})"
            if evento and razon and evento != razon
            else f"{motivo}"
        )
        fragmentos.append(f"Motivo: {detalle}.")
    if ambito:
        fragmentos.append(f"Ámbito: {ambito}.")
    fragmentos.append("Declarada por SENAPRED.")
    return " ".join(fragmentos)


def is_national(feature: GeoFeature) -> bool:
    ambito = normalise_text(feature.get(*FIELD_ALIASES["ambito"]))
    region = normalise_text(feature.get(*FIELD_ALIASES["region"]))
    return "nacional" in ambito or region in ("", "nacional", "todo el pais")


def matches_region(feature: GeoFeature, mapping: SenapredMapping) -> bool:
    if not mapping.filter_by_region or not mapping.regions:
        return True
    if mapping.include_national and is_national(feature):
        return True

    found = normalise_text(feature.get(*FIELD_ALIASES["region"]))
    if not found:
        return False
    return any(
        normalise_text(candidate) in found or found in normalise_text(candidate)
        for candidate in mapping.regions
    )


def resolve_timestamp(
    feature: GeoFeature, *, offset_minutes: int = 0
) -> datetime | None:
    """Fecha de declaración; si falta, la última actualización de la capa.

    El upsert no reescribe `timestamp`, así que el respaldo queda fijado en el
    primer avistamiento de la alerta y no se desplaza en cada corrida.
    """
    for key in ("fecha", "actualizado"):
        moment = parse_timestamp(
            feature.get(*FIELD_ALIASES[key]), offset_minutes=offset_minutes
        )
        if moment is not None:
            return moment
    return None


class SenapredCollector(BaseCollector):
    """Alertas preventivas, amarillas, rojas y evacuaciones vigentes."""

    name = "senapred_alertas"
    source = EventSource.SENAPRED
    default_interval_seconds = 600

    def __init__(
        self,
        session,
        *,
        client: SenapredClient | None = None,
        mapping: SenapredMapping | None = None,
    ) -> None:
        super().__init__(session)
        self.client = client or SenapredClient()
        self._mapping = mapping or SenapredMapping.from_settings()

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.SENAPRED_POLL_INTERVAL_SECONDS

    @property
    def mapping(self) -> SenapredMapping:
        existing = getattr(self, "_mapping", None)
        if existing is None:
            existing = SenapredMapping.from_settings()
            self._mapping = existing
        return existing

    def run_params(self) -> dict[str, Any]:
        return {
            "sources": [spec.label for spec in self.client.sources],
            "regions": list(self.mapping.regions),
            "include_national": self.mapping.include_national,
        }

    async def fetch(self) -> Sequence[GeoFeature]:
        features, warnings = await self.client.fetch_alertas()
        for message in warnings:
            self.warn(message)
        return features

    def normalize(self, records: Sequence[GeoFeature]) -> list[EventCreate]:
        """Mapea alertas de SENAPRED a eventos del dominio. Función pura."""
        mapping = self.mapping
        events: list[EventCreate] = []
        sin_fecha = 0
        sin_nivel = 0

        for feature in records:
            if not matches_region(feature, mapping):
                continue

            timestamp = resolve_timestamp(
                feature, offset_minutes=mapping.time_offset_minutes
            )
            if timestamp is None:
                sin_fecha += 1
                continue

            level = alert_level(feature)
            if level == "otra":
                sin_nivel += 1

            raw_data: dict[str, Any] = dict(feature.properties)
            raw_data["_alert_level"] = level
            raw_data["_national"] = is_national(feature)
            raw_data["_collector"] = self.name
            if feature.geometry:
                raw_data["_geometry"] = feature.geometry

            try:
                events.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=EventSource.SENAPRED,
                        type=map_event_type(feature),
                        # La capa de alertas vigentes es tabular: la alerta cubre
                        # una comuna o una región completa, no un punto. Se ingiere
                        # sin coordenadas antes que inventarle un centroide que el
                        # correlacionador trataría como una ubicación real.
                        lat=feature.lat,
                        lon=feature.lon,
                        text=build_text(feature),
                        external_id=build_external_id(feature),
                        confidence=SENAPRED_CONFIDENCE,
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "alerta SENAPRED descartada en validación",
                    extra={"error": str(exc)},
                )

        if sin_fecha:
            self.warn(
                f"{sin_fecha} alertas de SENAPRED sin fecha utilizable "
                f"(campos {FIELD_ALIASES['fecha']}); se descartaron"
            )
        if sin_nivel:
            self.warn(
                f"{sin_nivel} alertas de SENAPRED con un nivel no reconocido; "
                f"revisar si cambió la nomenclatura del campo "
                f"{FIELD_ALIASES['alerta'][0]!r}"
            )
        return events
