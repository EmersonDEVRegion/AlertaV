"""Grilla de lluvia: el pronóstico de Open-Meteo sobre una grilla regular.

Para qué
--------
La capa de lluvia dibujaba 36 círculos, uno por comuna. Un campo de lluvia de
verdad —el de Windy— sale de una grilla regular del modelo, que cubre también
el mar (de donde entran los frentes) y la cordillera. Este módulo arma esa
grilla, pide la precipitación horaria de cada punto y guarda **una sola foto**:
el máximo de mm/h de cada celda en las próximas `RAIN_GRID_HOURS` horas. El
mapa la pinta interpolada (`frontend/src/lib/rainRaster.ts`).

Qué no es
---------
No pasa por `raw_events` ni por el motor: son ~255 números que se reemplazan
cada hora, no señales. El flag `riesgo_inundacion` sigue saliendo de las 36
comunas (`collectors/weather`), con sus umbrales.

Detalles que importan
---------------------
* ``cell_selection=nearest``: el collector de las comunas pide ``land`` porque
  le importa la celda del pueblo. Acá el punto está donde está, y un punto en el
  mar tiene que traer la lluvia del mar.
* Los puntos van en lotes secuenciales de `RAIN_GRID_CHUNK_SIZE`, por el mismo
  truncado que documenta `OPENMETEO_CHUNK_SIZE`.
* La respuesta no trae identificador: se empareja por posición, igual que en
  `openmeteo_client`, y un lote con otra cantidad de puntos es un error.
* Un lote que falle corta la corrida y la foto anterior se conserva. Media grilla
  nueva y media vieja sería un frente partido en dos que nadie podría explicar.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.geoservices import as_float, parse_timestamp, request_json
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.core.identidad import user_agent
from app.models.weather_grid import WeatherGrid
from app.repositories.weather_grid_repository import WeatherGridRepository

logger = logging.getLogger(__name__)

#: Clave de la fila en `weather_grids`.
RAIN_GRID_KEY = "lluvia"

PROPOSITO = "grilla de lluvia"


@dataclass(frozen=True, slots=True)
class GridSpec:
    """Una grilla regular. Fila 0 es la del norte; columna 0, la del oeste."""

    west: float
    north: float
    step: float
    nx: int
    ny: int

    @property
    def east(self) -> float:
        return self.west + (self.nx - 1) * self.step

    @property
    def south(self) -> float:
        return self.north - (self.ny - 1) * self.step

    def points(self) -> list[tuple[float, float]]:
        """`(lat, lon)` de cada celda, fila a fila de norte a sur."""
        return [
            (round(self.north - i * self.step, 4), round(self.west + j * self.step, 4))
            for i in range(self.ny)
            for j in range(self.nx)
        ]


def build_grid(
    *, west: float, south: float, east: float, north: float, step: float
) -> GridSpec:
    """La grilla que cabe en la caja con ese paso, anclada en el noroeste."""
    if east <= west or north <= south:
        raise ValueError("caja vacía")
    # El épsilon evita perder la última columna por el redondeo de 2,5 / 0,15.
    nx = math.floor((east - west) / step + 1e-9) + 1
    ny = math.floor((north - south) / step + 1e-9) + 1
    return GridSpec(west=west, north=north, step=step, nx=nx, ny=ny)


def grid_from_settings() -> GridSpec:
    return build_grid(
        west=settings.RAIN_GRID_WEST,
        south=settings.RAIN_GRID_SOUTH,
        east=settings.RAIN_GRID_EAST,
        north=settings.RAIN_GRID_NORTH,
        step=settings.RAIN_GRID_STEP_DEGREES,
    )


def chunks(items: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(items[i : i + size]) for i in range(0, len(items), max(1, size))]


def max_next_hours(
    item: Mapping[str, Any], *, now: datetime, hours: int, origin: str
) -> float | None:
    """Máximo de `precipitation` entre la hora en curso y `hours` horas después.

    `None` si el modelo no trae ningún valor en la ventana (no es lo mismo que
    0 mm: es no saber).
    """
    hourly = item.get("hourly")
    if not isinstance(hourly, Mapping):
        raise CollectorError(f"{origin}: la respuesta no trae `hourly`")
    times = hourly.get("time")
    values = hourly.get("precipitation")
    if not isinstance(times, list) or not isinstance(values, list):
        raise CollectorError(f"{origin}: `hourly` sin `time` o sin `precipitation`")

    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=hours)
    best: float | None = None
    for raw_time, raw_value in zip(times, values, strict=False):
        moment = parse_timestamp(raw_time)
        if moment is None or moment < start or moment >= end:
            continue
        value = as_float(raw_value)
        if value is None or value < 0:
            continue
        best = value if best is None else max(best, value)
    return None if best is None else round(best, 2)


def parse_chunk(
    payload: Any,
    expected: int,
    *,
    now: datetime,
    hours: int,
    origin: str,
) -> list[float | None]:
    """Un lote: una lista de objetos (o uno solo, si se pidió un punto)."""
    if isinstance(payload, Mapping) and payload.get("error"):
        raise CollectorError(f"{origin}: {payload.get('reason') or 'error de Open-Meteo'}")
    items = [payload] if isinstance(payload, Mapping) else payload
    if not isinstance(items, list):
        raise CollectorError(f"{origin}: respuesta con forma inesperada")
    if len(items) != expected:
        # Se empareja por posición: con otra cantidad, cualquier valor podría
        # quedar en la celda equivocada.
        raise CollectorError(f"{origin}: se pidieron {expected} puntos y llegaron {len(items)}")
    return [
        max_next_hours(item, now=now, hours=hours, origin=origin)
        if isinstance(item, Mapping)
        else None
        for item in items
    ]


async def fetch_grid(
    grid: GridSpec,
    *,
    now: datetime,
    client: httpx.AsyncClient | None = None,
) -> list[float | None]:
    """Pide la grilla entera, lote por lote. Sube `CollectorError` si algo falla."""
    points = grid.points()
    batches = chunks(points, settings.RAIN_GRID_CHUNK_SIZE)
    values: list[float | None] = []

    own = client is None
    http = client or httpx.AsyncClient(
        timeout=settings.OPENMETEO_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": user_agent(PROPOSITO)},
    )
    try:
        for number, batch in enumerate(batches, start=1):
            origin = f"open-meteo grilla [lote {number}/{len(batches)}]"
            params = {
                "latitude": ",".join(f"{lat:.4f}" for lat, _ in batch),
                "longitude": ",".join(f"{lon:.4f}" for _, lon in batch),
                "hourly": "precipitation",
                "forecast_days": "2",
                "timezone": "UTC",
                "cell_selection": "nearest",
                "models": settings.OPENMETEO_MODEL,
            }
            payload = await request_json(http, settings.OPENMETEO_URL, params, origin=origin)
            values.extend(
                parse_chunk(
                    payload,
                    len(batch),
                    now=now,
                    hours=settings.RAIN_GRID_HOURS,
                    origin=origin,
                )
            )
    finally:
        if own:
            await http.aclose()
    return values


async def refresh_rain_grid(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    fetch: Any = None,
) -> bool:
    """Una corrida: pide la grilla y guarda la foto. `True` si salió bien.

    Un fallo no revienta: se anota en la fila (la foto anterior se conserva) y
    se reintenta en la próxima corrida.
    """
    moment = now or datetime.now(UTC)
    grid = grid_from_settings()
    repo = WeatherGridRepository(session)
    try:
        values = await (fetch or fetch_grid)(grid, now=moment)
    except CollectorError as exc:
        logger.warning("grilla de lluvia sin actualizar", extra={"error": str(exc)})
        await repo.mark_failure(RAIN_GRID_KEY, error=str(exc)[:500], now=moment)
        await session.commit()
        return False

    await repo.save(
        RAIN_GRID_KEY,
        grid=grid,
        values=values,
        model=settings.OPENMETEO_MODEL,
        hours=settings.RAIN_GRID_HOURS,
        now=moment,
    )
    await session.commit()
    logger.info(
        "grilla de lluvia actualizada",
        extra={
            "celdas": len(values),
            "con_lluvia": sum(1 for v in values if v is not None and v >= 0.2),
            "max_mm_h": max((v for v in values if v is not None), default=None),
        },
    )
    return True


# ---------------------------------------------------------------------------
#  Lectura (la ruta)
# ---------------------------------------------------------------------------


def grid_state(row: WeatherGrid | None, *, now: datetime) -> str:
    """`ok`, `stale`, `failing` o `never`, con la cadencia de la grilla.

    - `never`: nunca se leyó con éxito.
    - `failing`: el último intento falló (la foto que haya es vieja).
    - `stale`: sin intentos recientes: el proceso de workers no está corriendo.
    """
    if row is None or row.generated_at is None:
        return "never"
    if row.error:
        return "failing"
    interval = timedelta(seconds=settings.RAIN_GRID_POLL_INTERVAL_SECONDS)
    if now - row.generated_at > 3 * interval:
        return "stale"
    return "ok"


class RainGridService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = WeatherGridRepository(session)

    async def current(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        row = await self.repo.get(RAIN_GRID_KEY)
        fuente = {
            "estado": grid_state(row, now=moment),
            "ultima_lectura": row.generated_at if row else None,
            "detalle": row.error if row else None,
        }
        if row is None or row.generated_at is None or not row.values:
            return {"grilla": None, "fuente": fuente}
        return {
            "grilla": {
                "generado_en": row.generated_at,
                "modelo": row.model,
                "horas": row.hours,
                "paso": row.step,
                "oeste": row.west,
                "norte": row.north,
                "nx": row.nx,
                "ny": row.ny,
                "valores": row.values,
            },
            "fuente": fuente,
        }


__all__ = [
    "RAIN_GRID_KEY",
    "GridSpec",
    "RainGridService",
    "build_grid",
    "fetch_grid",
    "grid_state",
    "max_next_hours",
    "parse_chunk",
    "refresh_rain_grid",
]
