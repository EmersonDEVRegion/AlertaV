"""Cliente del feed público de sismos del USGS.

El United States Geological Survey publica el catálogo global en GeoJSON estático,
sin credenciales ni cuota declarada. El feed por defecto es `2.5_day`: magnitud
≥ 2.5 de las últimas 24 horas, regenerado cada minuto.

Esquema verificado del feed (2026-08):

    id                       identificador estable del evento ("us6000tlm3")
    properties.mag           magnitud (puede ser null en soluciones preliminares)
    properties.magType       escala usada: ml | mb | mww | md | …
    properties.place         "42 km SW of Coquimbo, Chile"
    properties.time          epoch ms UTC del sismo
    properties.updated       epoch ms UTC del último ajuste de la solución
    properties.type          "earthquake" | "quarry blast" | "ice quake" | …
    properties.status        "automatic" (preliminar) | "reviewed"
    properties.tsunami       0 | 1
    properties.alert         nivel PAGER: green | yellow | orange | red | null
    properties.felt, cdi, mmi, sig, net, code, url
    geometry.coordinates     [lon, lat, profundidad_km]

Por qué este módulo no reutiliza `geoservices.parse_feature_collection`
-----------------------------------------------------------------------

Se evaluó y se descartó por un motivo concreto. Ese parser aplica una heurística
que deduce si un servidor emitió los ejes invertidos comparando la latitud contra
el rango de Chile ([-57, -17]). Es la decisión correcta para las capas de CONAF y
SENAPRED —servidores WFS mal configurados que sólo publican datos chilenos—, pero
sobre un feed **global** se vuelve un riesgo: un sismo en el Atlántico a
lat 10, lon -33 tiene una latitud fuera del rango chileno y una longitud dentro,
así que la heurística "corregiría" un dato que estaba bien y lo movería a otro
continente.

El USGS publica GeoJSON conforme a la RFC 7946, donde el orden `[lon, lat, alt]`
es obligatorio y no ambiguo. Aquí se lee así, literalmente. Lo que sí se reutiliza
de `geoservices` es el transporte —`request_json`, con reintentos, detección de
HTML de portales caídos y errores servidos con HTTP 200—, que es donde estaba el
valor.

La tercera coordenada es la otra razón: `_representative_point` la descarta, y
para un sismo la profundidad no es un detalle decorativo — separa un evento
intraplaca profundo casi inofensivo de uno superficial destructivo de la misma
magnitud.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.collectors.geoservices import (
    SourceSpec,
    as_float,
    parse_source_specs,
    parse_timestamp,
    raise_if_service_error,
    request_json,
)
from app.core.config import settings
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeismicRecord:
    """Un sismo del catálogo, ya desenvuelto y con los tipos resueltos.

    Existe para que `normalize()` sea una función pura sobre datos planos: los
    tests arman `SeismicRecord` a mano —o los parsean de una respuesta real
    guardada— sin tocar la red.
    """

    usgs_id: str
    lat: float
    lon: float
    depth_km: float | None
    magnitude: float | None
    mag_type: str | None
    place: str | None
    time: datetime | None
    updated: datetime | None
    event_type: str
    review_status: str | None
    tsunami: bool
    pager_alert: str | None
    felt_reports: int | None
    significance: int | None
    url: str | None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


def _as_int(value: Any) -> int | None:
    number = as_float(value)
    return None if number is None else int(number)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def parse_feature(raw_feature: Mapping[str, Any]) -> SeismicRecord | None:
    """Convierte una feature del feed en un `SeismicRecord`.

    Devuelve `None` —en vez de reventar— cuando a la feature le falta lo mínimo
    para ser un sismo ubicable: id, o coordenadas. Una fila corrupta no debe
    tumbar el lote; el collector cuenta cuántas se cayeron y lo deja como
    advertencia en `collector_runs`.
    """
    usgs_id = _clean(raw_feature.get("id"))
    if usgs_id is None:
        return None

    geometry = raw_feature.get("geometry")
    if not isinstance(geometry, Mapping):
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list | tuple) or len(coordinates) < 2:
        return None

    # RFC 7946: [lon, lat, altitud]. En el catálogo del USGS la tercera es la
    # profundidad del hipocentro en km, positiva hacia abajo.
    lon = as_float(coordinates[0])
    lat = as_float(coordinates[1])
    depth_km = as_float(coordinates[2]) if len(coordinates) > 2 else None
    if lat is None or lon is None:
        return None

    properties = raw_feature.get("properties")
    properties = dict(properties) if isinstance(properties, Mapping) else {}

    return SeismicRecord(
        usgs_id=usgs_id,
        lat=lat,
        lon=lon,
        depth_km=depth_km,
        magnitude=as_float(properties.get("mag")),
        mag_type=_clean(properties.get("magType")),
        place=_clean(properties.get("place")),
        time=parse_timestamp(properties.get("time")),
        updated=parse_timestamp(properties.get("updated")),
        # `type` del catálogo, no del dominio: distingue un sismo de una
        # explosión de cantera, que viaja en el mismo feed y con el mismo esquema.
        event_type=(_clean(properties.get("type")) or "unknown").lower(),
        review_status=_clean(properties.get("status")),
        tsunami=bool(properties.get("tsunami")),
        pager_alert=_clean(properties.get("alert")),
        felt_reports=_as_int(properties.get("felt")),
        significance=_as_int(properties.get("sig")),
        url=_clean(properties.get("url")),
        properties=properties,
    )


def parse_feed(payload: Any, *, origin: str) -> tuple[list[SeismicRecord], int]:
    """Extrae los sismos de un FeatureCollection del USGS.

    Devuelve `(registros, features_ilegibles)`. Distingue las tres situaciones
    que un `except: return []` confundiría: colección válida y vacía (día sin
    sismos ≥ 2.5, plausible), colección válida con features, y cualquier otra
    cosa —que es un `CollectorError`, no un cero—.
    """
    if not isinstance(payload, Mapping):
        raise CollectorError(
            f"{origin}: se esperaba un objeto GeoJSON y llegó {type(payload).__name__}"
        )

    raise_if_service_error(payload, origin=origin)

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        status = metadata.get("status")
        # El feed lleva su propio código de estado dentro del cuerpo, y puede
        # traer 500 con un HTTP 200 por delante.
        if status is not None and int(status) != 200:
            raise CollectorError(
                f"{origin}: el feed declara status {status} en su metadata",
                detail={"metadata": dict(metadata)},
            )

    features = payload.get("features")
    if features is None:
        raise CollectorError(
            f"{origin}: la respuesta no tiene 'features'. Claves recibidas: "
            f"{sorted(str(key) for key in payload)[:15]}"
        )
    if not isinstance(features, list):
        raise CollectorError(
            f"{origin}: 'features' debería ser una lista y es {type(features).__name__}"
        )

    records: list[SeismicRecord] = []
    unreadable = 0
    for raw_feature in features:
        if not isinstance(raw_feature, Mapping):
            unreadable += 1
            continue
        record = parse_feature(raw_feature)
        if record is None:
            unreadable += 1
            continue
        records.append(record)

    return records, unreadable


class UsgsClient:
    """Descarga el feed de sismos, con cadena de respaldos y sin fallos silenciosos."""

    def __init__(
        self,
        *,
        sources: Sequence[SourceSpec] | str | None = None,
        timeout: float | None = None,
    ) -> None:
        raw_sources = sources if sources is not None else settings.USGS_SOURCES
        self.sources: list[SourceSpec] = (
            list(raw_sources)
            if isinstance(raw_sources, list | tuple)
            else parse_source_specs(raw_sources)
        )
        if not self.sources:
            raise CollectorError(
                "USGS_SOURCES está vacío: no hay de dónde leer el feed de sismos"
            )
        self.timeout = timeout if timeout is not None else settings.USGS_TIMEOUT_SECONDS

    async def fetch_earthquakes(self) -> tuple[list[SeismicRecord], list[str]]:
        """Devuelve `(registros, advertencias)`.

        Recorre las fuentes declaradas hasta que una responde algo interpretable.
        Que se haya llegado al dato por un respaldo no invalida la corrida, pero
        tampoco puede pasar inadvertido: sale como advertencia y deja la corrida
        en `partial`.
        """
        warnings: list[str] = []
        failures: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            # El USGS pide identificarse; sin User-Agent propio algunos de sus
            # frontales responden 403 sin explicar por qué.
            headers={"User-Agent": "AlertaV/1.0 (+https://github.com/alertav)"},
        ) as client:
            for spec in self.sources:
                try:
                    payload = await request_json(
                        client, spec.url, spec.params, origin=spec.label
                    )
                    records, unreadable = parse_feed(payload, origin=spec.label)
                except CollectorError as exc:
                    failures.append(f"{spec.label}: {exc.message}")
                    logger.warning(
                        "fuente de sismos no disponible; se intenta la siguiente",
                        extra={"source": spec.label, "error": exc.message},
                    )
                    continue
                except Exception as exc:  # se degrada a la siguiente fuente
                    message = f"{type(exc).__name__}: {exc}"
                    failures.append(f"{spec.label}: {message}")
                    logger.warning(
                        "fuente de sismos falló de forma inesperada",
                        extra={"source": spec.label, "error": message},
                    )
                    continue

                if unreadable:
                    warnings.append(
                        f"{unreadable} features del feed del USGS llegaron sin id o "
                        f"sin coordenadas utilizables; se descartaron"
                    )
                if failures:
                    warnings.append(
                        "se usó una fuente de respaldo del USGS: " + " | ".join(failures)
                    )
                logger.debug(
                    "feed del USGS leído",
                    extra={"origin": spec.label, "features": len(records)},
                )
                return records, warnings

        raise CollectorError(
            "ninguna de las fuentes declaradas del USGS respondió",
            detail={"attempts": failures},
        )
