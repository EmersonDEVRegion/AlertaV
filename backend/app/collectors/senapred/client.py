"""Cliente de alertas vigentes de SENAPRED.

SENAPRED no publica una API documentada de alertas en tiempo real. Lo que sí es
estable es la capa que alimenta sus visores institucionales (el tablero de
alertas vigentes y el Visor Chile Preparado): un servicio geoespacial público
que se puede consultar con los mismos verbos que cualquier otra capa.

Esquema verificado de la capa de alertas vigentes (2026-08):

    Region        nombre de la región
    Alerta        "Alerta Roja" | "Alerta Amarilla" | "Alerta Temprana Preventiva"
    Razon         motivo declarado ("Crecida", "Evento meteorológico", …)
    Evento        tipo de evento ("Viento", "Crecida", "Incendio Forestal", …)
    Comunas       "Toda la region" o listado de comunas
    Ambito        "Regional" | "Comunal" | "Nacional" | "Provincial"
    Fecha         epoch ms de declaración
    Actualizado   epoch ms de la última actualización de la capa

Advertencia deliberada: esta capa es un punto único de dependencia sobre un
tercero. Por eso `SENAPRED_SOURCES` admite una cadena de respaldos y el uso de un
respaldo queda registrado como advertencia en `collector_runs`. Cuando SENAPRED
publique un endpoint propio, se antepone en la cadena sin tocar código.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.collectors.geoservices import (
    FailoverFetcher,
    GeoFeature,
    SourceSpec,
    parse_source_specs,
)
from app.core.config import settings
from app.core.exceptions import CollectorError

logger = logging.getLogger(__name__)

#: El universo de alertas vigentes en Chile es de decenas de filas. No hace
#: falta filtrar en el servidor: se trae todo y se filtra en memoria, que además
#: es inmune a que cambie el nombre del campo de región.
_WHERE_ALL = "1=1"


class SenapredClient:
    def __init__(
        self,
        *,
        sources: Sequence[SourceSpec] | str | None = None,
        timeout: float | None = None,
        page_size: int | None = None,
    ) -> None:
        raw_sources = sources if sources is not None else settings.SENAPRED_SOURCES
        self.sources: list[SourceSpec] = (
            list(raw_sources)
            if isinstance(raw_sources, list | tuple)
            else parse_source_specs(raw_sources)
        )
        if not self.sources:
            raise CollectorError(
                "SENAPRED_SOURCES está vacío: no hay de dónde leer las alertas"
            )
        self.timeout = (
            timeout if timeout is not None else settings.SENAPRED_TIMEOUT_SECONDS
        )
        self.page_size = page_size or settings.SENAPRED_PAGE_SIZE
        self.fetcher = FailoverFetcher(
            self.sources, timeout=self.timeout, page_size=self.page_size
        )

    async def fetch_alertas(self) -> tuple[list[GeoFeature], list[str]]:
        """Devuelve `(features, advertencias)`."""
        features = await self.fetcher.fetch(where=_WHERE_ALL)
        warnings: list[str] = []

        if self.fetcher.used_fallback:
            warnings.append(
                "se usó una fuente de respaldo de SENAPRED: "
                + " | ".join(self.fetcher.failure_summary())
            )

        # Cero alertas vigentes en todo el país es posible pero infrecuente.
        # No es un error, pero conviene que quede anotado: si se repite corrida
        # tras corrida, probablemente la capa dejó de actualizarse.
        if not features:
            warnings.append(
                "la fuente respondió correctamente pero sin ninguna alerta vigente "
                "en el país; verificar que la capa siga actualizándose"
            )
        return features, warnings
