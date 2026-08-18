"""Collector de CONAF — incendios forestales reportados oficialmente.

Contrapunto deliberado al collector de FIRMS. Una detección satelital es una
anomalía térmica sin confirmar (`thermal_anomaly`, confianza baja); un incendio
en el registro operativo de CONAF **es** un incendio forestal confirmado por el
organismo a cargo de combatirlo. De ahí `type = wildfire` y `confidence = 1.0`.

Un mismo incendio se relee en corridas sucesivas mientras avanza su ciclo de
vida (En Combate → Controlado → Extinguido). El `external_id` se construye sobre
el identificador de CONAF, así que cada relectura actualiza la fila existente en
vez de crear una nueva.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.conaf.client import ConafClient, build_where
from app.collectors.geoservices import (
    GeoFeature,
    as_float,
    normalise_text,
    parse_timestamp,
)
from app.core.config import settings
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Confianza de una fuente institucional que confirma el hecho. No es la
#: confianza del incidente correlacionado, es la de esta señal en particular.
CONAF_CONFIDENCE = 1.0

#: Alias aceptados por campo. Las capas institucionales renombran columnas entre
#: temporadas; declararlo aquí evita quedarse ciego por un cambio cosmético.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "id_incendio", "codigo", "cod_incendio", "OBJECTID", "ObjectId"),
    "nombre": ("nombre", "nombre_incendio", "nom_incendio", "denominacion"),
    "estado": ("estado", "estado_incendio", "situacion", "status"),
    "inicio": ("f_inicio", "fmt_inicio", "fecha_inicio", "fecha_deteccion"),
    "control": ("f_control", "fecha_control"),
    "extincion": ("f_extincion", "fecha_extincion"),
    "superficie": ("sup_total", "superficie", "sup_afectada", "hectareas"),
    "comuna": ("comuna", "nom_comuna", "COMUNA"),
    "provincia": ("provincia", "nom_provincia"),
    "region": ("region", "nom_region", "REGION"),
    "ambito": ("ambito", "organismo"),
    "lat": ("lat", "latitud", "latitude", "y"),
    "lon": ("lon", "longitud", "longitude", "x"),
}


@dataclass(frozen=True, slots=True)
class ConafMapping:
    """Parámetros de mapeo. Separados del cliente para poder testear sin red."""

    regions: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    filter_by_region: bool = True
    time_offset_minutes: int = 0

    @classmethod
    def from_settings(cls) -> ConafMapping:
        return cls(
            regions=tuple(settings.CONAF_REGIONS),
            states=tuple(settings.CONAF_STATES),
            filter_by_region=settings.CONAF_FILTER_BY_REGION,
            time_offset_minutes=settings.CONAF_TIME_OFFSET_MINUTES,
        )


def build_external_id(feature: GeoFeature) -> str:
    """ID estable. CONAF entrega uno propio; si falta, se deriva uno.

    El identificador de CONAF es la clave de la idempotencia: permite que la
    misma emergencia, releída mientras cambia de estado, actualice su fila en vez
    de multiplicarse en el mapa.
    """
    native = feature.get(*FIELD_ALIASES["id"])
    if native is not None:
        return f"conaf:{native}"
    if feature.feature_id:
        return f"conaf:{feature.feature_id}"

    parts = "|".join(
        [
            str(feature.get(*FIELD_ALIASES["nombre"], default="")),
            str(feature.get(*FIELD_ALIASES["inicio"], default="")),
            str(feature.get(*FIELD_ALIASES["comuna"], default="")),
            f"{feature.lat:.5f}" if feature.lat is not None else "",
            f"{feature.lon:.5f}" if feature.lon is not None else "",
        ]
    )
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]
    return f"conaf:h:{digest}"


def build_text(feature: GeoFeature) -> str:
    """Descripción legible. Deja explícito que la fuente es institucional."""
    nombre = feature.get(*FIELD_ALIASES["nombre"], default="sin nombre")
    estado = feature.get(*FIELD_ALIASES["estado"], default="estado no informado")
    comuna = feature.get(*FIELD_ALIASES["comuna"])
    provincia = feature.get(*FIELD_ALIASES["provincia"])
    superficie = as_float(feature.get(*FIELD_ALIASES["superficie"]))

    ubicacion = ", ".join(part for part in (comuna, provincia) if part)
    fragmentos = [f'Incendio forestal "{nombre}" — estado: {estado}.']
    if ubicacion:
        fragmentos.append(f"Ubicación: {ubicacion}.")
    if superficie is not None:
        fragmentos.append(f"Superficie afectada: {superficie:.2f} ha.")
    fragmentos.append("Reporte oficial de CONAF.")
    return " ".join(fragmentos)


def extract_coordinates(feature: GeoFeature) -> tuple[float | None, float | None]:
    """Coordenadas de la geometría; si no hay, de los campos lat/lon.

    La capa trae ambas cosas. Que una feature llegue sin geometría no debería
    costar el evento: el par lat/lon de los atributos es igual de válido.
    """
    if feature.has_location:
        return (feature.lat, feature.lon)
    lat = as_float(feature.get(*FIELD_ALIASES["lat"]))
    lon = as_float(feature.get(*FIELD_ALIASES["lon"]))
    if lat is None or lon is None:
        return (None, None)
    return (lat, lon)


def matches_region(feature: GeoFeature, regions: Sequence[str]) -> bool:
    """¿La feature corresponde a alguna de las regiones configuradas?

    Se compara sin tildes ni mayúsculas ("VALPARAISO" ≡ "Valparaíso") y por
    inclusión, para tolerar "Región de Valparaíso". Si la capa no trae el campo
    región, se cae al bounding box configurado: perder un evento por un campo
    ausente sería peor que evaluarlo geométricamente.
    """
    if not regions:
        return True

    raw_region = feature.get(*FIELD_ALIASES["region"])
    if raw_region is not None:
        found = normalise_text(raw_region)
        return any(
            normalise_text(candidate) in found or found in normalise_text(candidate)
            for candidate in regions
        )

    lat, lon = extract_coordinates(feature)
    if lat is None or lon is None:
        return False
    return settings.region_bbox.contains(lat, lon)


def matches_state(feature: GeoFeature, states: Sequence[str]) -> bool:
    if not states:
        return True
    current = normalise_text(feature.get(*FIELD_ALIASES["estado"]))
    return any(normalise_text(state) == current for state in states)


def resolve_timestamp(feature: GeoFeature, *, offset_minutes: int = 0) -> datetime | None:
    """Instante del evento: inicio del incendio, con respaldos razonables."""
    for key in ("inicio", "control", "extincion"):
        moment = parse_timestamp(
            feature.get(*FIELD_ALIASES[key]), offset_minutes=offset_minutes
        )
        if moment is not None:
            return moment
    return None


class ConafCollector(BaseCollector):
    """Incendios forestales del registro operativo de CONAF."""

    name = "conaf_incendios"
    source = EventSource.CONAF
    default_interval_seconds = 300

    def __init__(
        self,
        session,
        *,
        client: ConafClient | None = None,
        mapping: ConafMapping | None = None,
        lookback_days: int | None = None,
    ) -> None:
        super().__init__(session)
        self.client = client or ConafClient()
        self._mapping = mapping or ConafMapping.from_settings()
        self.lookback_days = (
            lookback_days if lookback_days is not None else settings.CONAF_LOOKBACK_DAYS
        )

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.CONAF_POLL_INTERVAL_SECONDS

    @property
    def mapping(self) -> ConafMapping:
        """Config de mapeo, construida perezosamente desde `settings`.

        Permite `ConafCollector.__new__(ConafCollector).normalize(features)` en
        los tests, igual que con el collector de FIRMS.
        """
        existing = getattr(self, "_mapping", None)
        if existing is None:
            existing = ConafMapping.from_settings()
            self._mapping = existing
        return existing

    def run_params(self) -> dict[str, Any]:
        return {
            "sources": [spec.label for spec in self.client.sources],
            "lookback_days": self.lookback_days,
            "regions": list(self.mapping.regions),
            "states": list(self.mapping.states) or ["(todos)"],
            "where": settings.CONAF_WHERE or build_where(self.lookback_days),
        }

    async def fetch(self) -> Sequence[GeoFeature]:
        features, warnings = await self.client.fetch_incendios(
            lookback_days=self.lookback_days
        )
        for message in warnings:
            self.warn(message)
        return features

    def normalize(self, records: Sequence[GeoFeature]) -> list[EventCreate]:
        """Mapea features de CONAF a eventos del dominio. Función pura."""
        mapping = self.mapping
        events: list[EventCreate] = []
        sin_fecha = 0
        sin_coordenadas = 0

        for feature in records:
            if mapping.filter_by_region and not matches_region(feature, mapping.regions):
                continue
            if not matches_state(feature, mapping.states):
                continue

            timestamp = resolve_timestamp(
                feature, offset_minutes=mapping.time_offset_minutes
            )
            if timestamp is None:
                sin_fecha += 1
                continue

            lat, lon = extract_coordinates(feature)
            if lat is None or lon is None:
                sin_coordenadas += 1

            raw_data: dict[str, Any] = dict(feature.properties)
            raw_data["_geometry"] = feature.geometry
            raw_data["_collector"] = self.name

            try:
                events.append(
                    EventCreate(
                        timestamp=timestamp,
                        source=EventSource.CONAF,
                        # CONAF confirma el hecho: esto sí es un incendio.
                        type=EventType.WILDFIRE,
                        lat=lat,
                        lon=lon,
                        text=build_text(feature),
                        external_id=build_external_id(feature),
                        confidence=CONAF_CONFIDENCE,
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "incendio CONAF descartado en validación",
                    extra={"error": str(exc), "external_id": build_external_id(feature)},
                )

        if sin_fecha:
            self.warn(
                f"{sin_fecha} incendios de CONAF sin fecha utilizable "
                f"(campos {FIELD_ALIASES['inicio']}); se descartaron"
            )
        if sin_coordenadas:
            self.warn(
                f"{sin_coordenadas} incendios de CONAF sin coordenadas; "
                f"se ingirieron sólo con texto"
            )
        return events
