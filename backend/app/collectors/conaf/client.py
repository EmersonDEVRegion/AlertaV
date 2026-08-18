"""Cliente de la capa de incendios de CONAF.

El Sistema de Información Territorial de CONAF se apoya en PostGIS y publica sus
capas a través de servidores geoespaciales estándar. La capa operativa de
incendios (`incendios_base`, organización GEPRIF) se sirve como ArcGIS
FeatureServer y acepta salida GeoJSON, que es lo que se consume aquí.

Esquema verificado de la capa (2026-08):

    id            entero, identificador del incendio en el sistema de CONAF
    nombre        nombre operativo del incendio
    estado        "En Combate" | "Controlado" | "Extinguido"
    f_inicio      epoch ms, inicio del incendio
    f_control     epoch ms, control
    f_extincion   epoch ms, extinción
    sup_total     hectáreas afectadas
    lat, lon      WGS84 (además de la geometría)
    comuna, provincia, region
    ambito        "CONAF" u otro organismo a cargo

La URL no está incrustada: se declara en `CONAF_SOURCES` y admite una cadena de
respaldos (`arcgis|…;wfs|…`). Si CONAF migra de plataforma o publica el WFS del
SIT, se cambia una variable de entorno.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.collectors.geoservices import (
    FailoverFetcher,
    GeoFeature,
    SourceSpec,
    parse_source_specs,
)
from app.core.config import settings
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: Campo de fecha usado para acotar la ventana consultada.
_START_FIELD = "f_inicio"


def build_where(lookback_days: int, *, now: datetime | None = None) -> str:
    """Cláusula WHERE que acota la consulta a la ventana de interés.

    Se releen los últimos `lookback_days` días en cada corrida a propósito: un
    incendio pasa de "En Combate" a "Controlado" y a "Extinguido" a lo largo de
    varios días, y el upsert por `external_id` actualiza la fila existente en vez
    de duplicarla. La ventana es, en la práctica, el seguimiento del ciclo de
    vida del incendio.
    """
    reference = now or datetime.now(UTC)
    since = reference - timedelta(days=lookback_days)
    return f"{_START_FIELD} >= timestamp '{since.strftime('%Y-%m-%d %H:%M:%S')}'"


class ConafClient:
    """Descarga las features de incendios, con respaldo y sin fallos silenciosos."""

    def __init__(
        self,
        *,
        sources: Sequence[SourceSpec] | str | None = None,
        timeout: float | None = None,
        page_size: int | None = None,
    ) -> None:
        raw_sources = sources if sources is not None else settings.CONAF_SOURCES
        self.sources: list[SourceSpec] = (
            list(raw_sources)
            if isinstance(raw_sources, list | tuple)
            else parse_source_specs(raw_sources)
        )
        if not self.sources:
            raise CollectorError(
                "CONAF_SOURCES está vacío: no hay de dónde leer la capa de incendios"
            )
        self.timeout = timeout if timeout is not None else settings.CONAF_TIMEOUT_SECONDS
        self.page_size = page_size or settings.CONAF_PAGE_SIZE
        self.fetcher = FailoverFetcher(
            self.sources, timeout=self.timeout, page_size=self.page_size
        )

    async def fetch_incendios(
        self, *, where: str | None = None, lookback_days: int | None = None
    ) -> tuple[list[GeoFeature], list[str]]:
        """Devuelve `(features, advertencias)`.

        Si la consulta acotada falla —típicamente porque cambió el nombre del
        campo de fecha— se reintenta sin filtro antes de darse por vencido. Un
        filtro roto no puede convertirse en "hoy no hubo incendios": se trae todo
        y se deja constancia de la degradación.
        """
        clause = where or settings.CONAF_WHERE or build_where(
            lookback_days if lookback_days is not None else settings.CONAF_LOOKBACK_DAYS
        )
        warnings: list[str] = []

        try:
            features = await self.fetcher.fetch(where=clause)
        except CollectorError as exc:
            if clause == "1=1":
                raise
            logger.warning(
                "la consulta acotada falló; se reintenta sin filtro temporal",
                extra={"where": clause, "error": exc.message},
            )
            warnings.append(
                f"la cláusula WHERE '{clause}' fue rechazada por la fuente "
                f"({exc.message}); se consultó sin filtro temporal. "
                f"Revisar si cambió el nombre del campo '{_START_FIELD}'."
            )
            features = await self.fetcher.fetch(where="1=1")

        if self.fetcher.used_fallback:
            warnings.append(
                "se usó una fuente de respaldo de CONAF: "
                + " | ".join(self.fetcher.failure_summary())
            )
        return features, warnings
