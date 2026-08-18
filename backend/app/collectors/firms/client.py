"""Cliente HTTP de la API de área de NASA FIRMS.

Endpoint:
    GET {base}/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{day_range}

Devuelve CSV. Columnas según sensor:
    VIIRS  : latitude, longitude, bright_ti4, scan, track, acq_date, acq_time,
             satellite, instrument, confidence, version, bright_ti5, frp, daynight
    MODIS  : latitude, longitude, brightness, scan, track, acq_date, acq_time,
             satellite, instrument, confidence, version, bright_t31, frp, daynight

El parser no asume el orden ni el conjunto exacto de columnas: usa DictReader y
lee por nombre. Si FIRMS agrega columnas, siguen llegando íntegras a `raw_data`.

Docs: https://firms.modaps.eosdis.nasa.gov/api/area/
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import CollectorError, ConfigurationError

logger = logging.getLogger(__name__)

#: Respuestas de error de FIRMS que llegan con HTTP 200 y cuerpo de texto plano.
_ERROR_MARKERS = (
    "invalid map_key",
    "invalid mapkey",
    "map_key not found",
    "you have exceeded",
    "transaction limit",
    "error",
)


class FirmsClient:
    """Cliente async. Un `FirmsClient` por corrida del collector."""

    def __init__(
        self,
        *,
        map_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.map_key = map_key if map_key is not None else settings.FIRMS_MAP_KEY
        self.base_url = (base_url or settings.FIRMS_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.FIRMS_TIMEOUT_SECONDS

        if not self.map_key:
            raise ConfigurationError(
                "FIRMS_MAP_KEY no está configurada. Solicitar una MAP_KEY gratuita "
                "en https://firms.modaps.eosdis.nasa.gov/api/map_key/"
            )

    def build_url(self, sensor: str, bbox: str, day_range: int) -> str:
        return (
            f"{self.base_url}/api/area/csv/{self.map_key}/{sensor}/{bbox}/{day_range}"
        )

    async def fetch_area(
        self, *, sensor: str, bbox: str, day_range: int = 1
    ) -> list[dict[str, Any]]:
        """Descarga detecciones de un sensor para una caja envolvente.

        `bbox` en formato "west,south,east,north". `day_range` entre 1 y 10.
        """
        url = self.build_url(sensor, bbox, day_range)
        safe_url = url.replace(self.map_key, "***")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CollectorError(
                f"FIRMS respondió {exc.response.status_code} para {sensor}",
                detail={"url": safe_url},
            ) from exc
        except httpx.HTTPError as exc:
            raise CollectorError(
                f"error de red consultando FIRMS ({sensor}): {exc}",
                detail={"url": safe_url},
            ) from exc

        return self.parse_csv(response.text, sensor=sensor)

    @staticmethod
    def parse_csv(payload: str, *, sensor: str) -> list[dict[str, Any]]:
        """Parsea el CSV de FIRMS y detecta respuestas de error enmascaradas.

        FIRMS responde 200 con un mensaje en texto plano cuando la MAP_KEY es
        inválida o se agotó la cuota. Tratar eso como "cero detecciones" haría
        que el collector fallara en silencio durante días.
        """
        text = payload.strip()
        if not text:
            return []

        first_line = text.splitlines()[0].lower()
        if "latitude" not in first_line:
            if any(marker in text.lower()[:500] for marker in _ERROR_MARKERS):
                raise CollectorError(
                    f"FIRMS devolvió un error para {sensor}: {text[:200]}"
                )
            raise CollectorError(
                f"respuesta inesperada de FIRMS para {sensor}: {text[:200]}"
            )

        rows = list(csv.DictReader(io.StringIO(text)))
        logger.debug("FIRMS CSV parseado", extra={"sensor": sensor, "rows": len(rows)})
        return [{k: v for k, v in row.items() if k is not None} for row in rows]
