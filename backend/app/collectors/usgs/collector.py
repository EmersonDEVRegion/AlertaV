"""Collector del USGS — sismos en tiempo real de la zona central de Chile.

Qué es un sismo dentro de este sistema
--------------------------------------

Ni FIRMS ni CONAF: una tercera categoría. FIRMS entrega un indicio que necesita
corroboración (`thermal_anomaly`, confianza acotada); CONAF entrega un incendio
confirmado por quien va a apagarlo (`wildfire`, 1.0). Un sismo del USGS es un
**hecho medido con instrumentos, que no es un siniestro**.

Esa última mitad de la frase es la que ordena todas las decisiones de este
módulo. Un epicentro no es un lugar donde algo esté ocurriendo: es el punto donde
se originó la ruptura, a menudo decenas de kilómetros bajo el mar. Pintarlo en el
mapa como un incidente diría algo falso. Por eso:

* `type = earthquake` **no está** en `CORRELATABLE_EVENT_TYPES`. El motor de
  correlación resuelve incertidumbre por corroboración y un sismo no tiene
  ninguna que resolver; peor aún, su radio de 1500 m y su ventana de 4 h son
  exactamente la escala de una réplica, así que fusionaría el sismo principal con
  sus réplicas y borraría la secuencia.
* La confianza es 1.0 y significa "este sismo ocurrió", no "aquí hay una
  emergencia". La regla de `confidence.py` le da peso 0 sobre el fenómeno, igual
  que a la meteorología.

El valor de tenerlo es de contexto y de correlación humana: cuando entren cinco
reportes ciudadanos de derrumbe en Quillota, saber que quince minutos antes hubo
un M5.8 a 60 km cambia por completo la lectura de esos reportes.

Recorte espacial
----------------

El feed es global (~50 sismos por día en todo el planeta) y no admite parámetros
de consulta: es un archivo estático. El filtro es del lado del cliente, contra
`USGS_*BBOX` —lat -35.0 a -31.0, lon -73.0 a -69.0 por defecto—, deliberadamente
más ancho que `region_bbox`: un sismo a 200 km de Valparaíso se siente en
Valparaíso, y recortarlo a la región sería aplicarle a un fenómeno de escala
regional un criterio pensado para incendios puntuales.

Idempotencia
------------

El USGS asigna a cada evento un id estable (`us6000tlm3`) que sobrevive a las
revisiones de la solución. Es la clave del upsert: un sismo se publica primero
como solución automática y se corrige horas después con la magnitud revisada. Con
`external_id = usgs:<id>`, esa segunda lectura **actualiza** la fila y su detalle
sismológico en vez de crear un segundo sismo fantasma.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.collectors.base import BaseCollector
from app.collectors.usgs.client import SeismicRecord, UsgsClient
from app.core.config import BoundingBox, settings
from app.models.enums import EventSource, EventType
from app.repositories.seismic_repository import SeismicRepository
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: El sismo ocurrió: lo midió una red de sismógrafos. No dice nada sobre que haya
#: un siniestro en ese punto — eso lo fija la regla de `confidence.py`, que le da
#: peso 0 sobre el fenómeno.
USGS_CONFIDENCE = 1.0

#: Clave namespaced en `raw_data` donde `normalize()` deja los campos ya tipados
#: para que `after_ingest()` los persista sin volver a parsear el feed. Mismo
#: patrón que `_geometry`, `_collector` y `_alert_level` en los otros collectors.
SEISMIC_KEY = "_seismic"

#: Niveles PAGER que acepta el CHECK de la tabla. Cualquier otra cosa se guarda
#: como NULL: un valor nuevo del USGS no puede tumbar la inserción del lote.
_PAGER_LEVELS = frozenset({"green", "yellow", "orange", "red"})
_REVIEW_STATUSES = frozenset({"automatic", "reviewed"})


@dataclass(frozen=True, slots=True)
class UsgsMapping:
    """Parámetros de mapeo. Separados del cliente para poder testear sin red."""

    bbox: BoundingBox
    min_magnitude: float = 0.0
    event_types: tuple[str, ...] = ("earthquake",)
    include_automatic: bool = True

    @classmethod
    def from_settings(cls) -> UsgsMapping:
        return cls(
            bbox=settings.usgs_bbox,
            min_magnitude=settings.USGS_MIN_MAGNITUDE,
            event_types=tuple(
                kind.strip().lower() for kind in settings.USGS_EVENT_TYPES if kind.strip()
            ),
            include_automatic=settings.USGS_INCLUDE_AUTOMATIC,
        )


def build_external_id(record: SeismicRecord) -> str:
    """ID estable. El USGS entrega uno propio y sobrevive a las revisiones.

    A diferencia de CONAF, aquí no hace falta un hash de respaldo: una feature
    sin `id` no llega a construir un `SeismicRecord` (el cliente la descarta),
    así que este campo nunca está vacío.
    """
    return f"usgs:{record.usgs_id}"


def build_text(record: SeismicRecord) -> str:
    """Descripción legible en español. El feed viene en inglés."""
    if record.magnitude is None:
        cabeza = "Sismo de magnitud no determinada"
    else:
        escala = f" {record.mag_type}" if record.mag_type else ""
        cabeza = f"Sismo de magnitud {record.magnitude:.1f}{escala}"

    fragmentos = [cabeza]
    if record.depth_km is not None:
        fragmentos.append(f"a {record.depth_km:.0f} km de profundidad")

    frase = " ".join(fragmentos) + "."
    partes = [frase]

    if record.place:
        partes.append(f"Epicentro: {record.place}.")
    if record.review_status == "automatic":
        # No es un detalle menor: la magnitud de una solución automática puede
        # moverse varias décimas al revisarse, y quien lee el evento tiene que
        # saber que está mirando un preliminar.
        partes.append("Solución preliminar automática, sujeta a revisión.")
    if record.tsunami:
        # El feed marca "región con protocolo de tsunami", no "hay alerta".
        # Decirlo de otra manera sería inventar una alerta que nadie declaró.
        partes.append(
            "El evento ocurre en una zona con protocolo de tsunami; "
            "la alerta, si la hay, la declara SHOA/SENAPRED."
        )
    partes.append("Reporte instrumental del USGS.")
    return " ".join(partes)


def in_bbox(record: SeismicRecord, bbox: BoundingBox) -> bool:
    """¿El epicentro cae en la zona central de Chile?"""
    return bbox.contains(record.lat, record.lon)


def matches_event_type(record: SeismicRecord, event_types: Sequence[str]) -> bool:
    """El catálogo del USGS mezcla sismos con otros eventos sísmicos.

    Explosiones de cantera, detonaciones y eventos de hielo viajan en el mismo
    feed con el mismo esquema. Ingerirlos como sismos sería un error de dominio,
    no de mapeo: una tronadura minera en la cordillera no es un evento natural ni
    tiene el mismo significado para quien mira el mapa.
    """
    if not event_types:
        return True
    return record.event_type in event_types


def seismic_row(record: SeismicRecord) -> dict[str, Any]:
    """Campos de `seismic_details`, serializables a JSON.

    Las fechas van como ISO-8601 porque este diccionario viaja dentro de
    `raw_data` (JSONB) hasta `after_ingest()`. Los valores acotados por CHECK se
    normalizan aquí: si el USGS estrena un nivel PAGER nuevo, se guarda NULL en
    vez de hacer fallar la inserción del lote completo.
    """
    pager = (record.pager_alert or "").lower()
    review = (record.review_status or "").lower()
    return {
        "usgs_id": record.usgs_id,
        "magnitude": record.magnitude,
        "mag_type": record.mag_type,
        "depth_km": record.depth_km,
        "place": record.place,
        "felt_reports": record.felt_reports,
        "tsunami": record.tsunami,
        "pager_alert": pager if pager in _PAGER_LEVELS else None,
        "significance": record.significance,
        "review_status": review if review in _REVIEW_STATUSES else None,
        "usgs_url": record.url,
        "source_updated_at": record.updated.isoformat() if record.updated else None,
    }


def _row_for_db(raw_event_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Adapta el detalle guardado en JSONB a los tipos que espera la tabla.

    `source_updated_at` viaja como ISO-8601 porque `raw_data` es JSONB y un
    `datetime` no es serializable a JSON. Al volver hay que reconstruirlo:
    asyncpg es estricto con los tipos y **no** convierte un texto a `timestamptz`
    —a diferencia de psycopg2, que sí lo haría—. Sin esta conversión el insert
    revienta en producción y pasa en los tests, que no tocan la base.
    """
    row = {"raw_event_id": raw_event_id, **payload}
    updated = row.get("source_updated_at")
    if isinstance(updated, str):
        try:
            row["source_updated_at"] = datetime.fromisoformat(updated)
        except ValueError:
            row["source_updated_at"] = None
    return row


class UsgsCollector(BaseCollector):
    """Sismos del catálogo del USGS acotados a la zona central de Chile."""

    name = "usgs_sismos"
    source = EventSource.USGS
    default_interval_seconds = 300

    def __init__(
        self,
        session,
        *,
        client: UsgsClient | None = None,
        mapping: UsgsMapping | None = None,
    ) -> None:
        super().__init__(session)
        self.client = client or UsgsClient()
        self._mapping = mapping or UsgsMapping.from_settings()

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.USGS_POLL_INTERVAL_SECONDS

    @property
    def mapping(self) -> UsgsMapping:
        """Config de mapeo, construida perezosamente desde `settings`.

        Permite `UsgsCollector.__new__(UsgsCollector).normalize(records)` en los
        tests, igual que en los collectors de CONAF y FIRMS.
        """
        existing = getattr(self, "_mapping", None)
        if existing is None:
            existing = UsgsMapping.from_settings()
            self._mapping = existing
        return existing

    def run_params(self) -> dict[str, Any]:
        bbox = self.mapping.bbox
        return {
            "sources": [spec.label for spec in self.client.sources],
            "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
            "min_magnitude": self.mapping.min_magnitude,
            "event_types": list(self.mapping.event_types) or ["(todos)"],
            "include_automatic": self.mapping.include_automatic,
        }

    async def fetch(self) -> Sequence[SeismicRecord]:
        records, warnings = await self.client.fetch_earthquakes()
        for message in warnings:
            self.warn(message)
        return records

    def normalize(self, records: Sequence[SeismicRecord]) -> list[EventCreate]:
        """Mapea sismos del USGS a eventos del dominio. Función pura.

        El orden de los filtros es el que minimiza trabajo: primero el recorte
        espacial, que descarta el 99 % de un feed global, y sólo después los
        criterios de catálogo.
        """
        mapping = self.mapping
        events: list[EventCreate] = []
        sin_fecha = 0
        preliminares_omitidos = 0

        for record in records:
            if not in_bbox(record, mapping.bbox):
                continue
            if not matches_event_type(record, mapping.event_types):
                continue
            if (
                mapping.min_magnitude > 0.0
                and record.magnitude is not None
                and record.magnitude < mapping.min_magnitude
            ):
                continue
            if not mapping.include_automatic and record.review_status == "automatic":
                preliminares_omitidos += 1
                continue

            if record.time is None:
                # Sin instante no hay correlación posible ni orden en el mapa, y
                # un sismo sin hora no es un dato: es ruido.
                sin_fecha += 1
                continue

            raw_data: dict[str, Any] = dict(record.properties)
            raw_data["_geometry"] = {
                "type": "Point",
                "coordinates": [record.lon, record.lat, record.depth_km],
            }
            raw_data["_collector"] = self.name
            raw_data[SEISMIC_KEY] = seismic_row(record)

            try:
                events.append(
                    EventCreate(
                        timestamp=record.time,
                        source=EventSource.USGS,
                        type=EventType.EARTHQUAKE,
                        lat=record.lat,
                        lon=record.lon,
                        text=build_text(record),
                        external_id=build_external_id(record),
                        confidence=USGS_CONFIDENCE,
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:  # una fila mala no puede tumbar el lote
                logger.debug(
                    "sismo del USGS descartado en validación",
                    extra={"error": str(exc), "external_id": build_external_id(record)},
                )

        if sin_fecha:
            self.warn(
                f"{sin_fecha} sismos del USGS sin campo 'time' utilizable; se descartaron"
            )
        if preliminares_omitidos:
            self.warn(
                f"{preliminares_omitidos} sismos preliminares omitidos por "
                f"USGS_INCLUDE_AUTOMATIC=false"
            )
        return events

    async def after_ingest(self, events: Sequence[EventCreate]) -> None:
        """Cuelga el detalle sismológico de las señales recién escritas.

        Va después de la ingesta y no dentro porque `seismic_details` referencia
        `raw_events.id`, que la fila no tiene hasta estar en la base. Los ids se
        recuperan por `external_id`, que es justamente la clave que hace
        idempotente todo lo anterior: releer el feed actualiza ambas filas.

        Si una señal no aparece en el mapa de ids, algo falló en la ingesta de esa
        fila en particular. Se registra como advertencia —la corrida queda
        `partial`— en vez de fingir que se guardó todo.
        """
        pending = [
            (event.external_id, event.raw_data.get(SEISMIC_KEY))
            for event in events
            if event.external_id and isinstance(event.raw_data.get(SEISMIC_KEY), dict)
        ]
        if not pending:
            return

        ids = await self.service.repo.ids_by_external_id(
            EventSource.USGS, [external_id for external_id, _ in pending]
        )

        rows: list[dict[str, Any]] = []
        huerfanos = 0
        for external_id, payload in pending:
            raw_event_id = ids.get(external_id)
            if raw_event_id is None:
                huerfanos += 1
                continue
            rows.append(_row_for_db(raw_event_id, payload))

        if rows:
            await SeismicRepository(self.session).upsert_many(rows)
            await self.session.commit()

        if huerfanos:
            self.warn(
                f"{huerfanos} sismos quedaron sin detalle sismológico: no se "
                f"encontró su fila en raw_events tras la ingesta"
            )

        logger.info(
            "detalle sismológico persistido",
            extra={"collector": self.name, "rows": len(rows), "orphans": huerfanos},
        )
