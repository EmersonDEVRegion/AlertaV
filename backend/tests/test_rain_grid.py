"""Grilla de lluvia: geometría, lectura de Open-Meteo, foto y ruta.

Sin red ni base: el transporte y el repositorio se reemplazan por dobles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_rain_grid_service
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.main import app
from app.services import rain_grid_service as svc
from app.services.rain_grid_service import (
    RainGridService,
    build_grid,
    grid_from_settings,
    grid_state,
    max_next_hours,
    parse_chunk,
    refresh_rain_grid,
    seconds_until_due,
)

NOW = datetime(2026, 9, 30, 14, 25, tzinfo=UTC)


def _item(values: list[float | None], start: datetime | None = None) -> dict[str, Any]:
    first = start or NOW.replace(minute=0)
    times = [(first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(values))]
    return {
        "latitude": -33.0,
        "longitude": -71.6,
        "hourly": {"time": times, "precipitation": values},
    }


class TestGeometria:
    def test_la_caja_por_defecto_son_255_puntos(self) -> None:
        grid = build_grid(west=-72.3, south=-34.0, east=-69.8, north=-31.9, step=0.15)
        assert (grid.nx, grid.ny) == (17, 15)
        assert len(grid.points()) == 255

    def test_la_caja_configurada_cubre_el_mapa_y_cabe_en_el_presupuesto(self) -> None:
        # La caja es `MAP_MAX_BOUNDS` de la PWA: el borde del campo queda en el
        # límite del mapa y no a la vista (§L). La PWA compara la caja con
        # MAP_MAX_BOUNDS (`frontend/src/config/rainGridBounds.test.ts`).
        grid = build_grid(
            west=settings.RAIN_GRID_WEST,
            south=settings.RAIN_GRID_SOUTH,
            east=settings.RAIN_GRID_EAST,
            north=settings.RAIN_GRID_NORTH,
            step=settings.RAIN_GRID_STEP_DEGREES,
        )
        assert (grid.nx, grid.ny) == (25, 26)
        east = grid.west + (grid.nx - 1) * grid.step
        south = grid.north - (grid.ny - 1) * grid.step
        assert east == pytest.approx(settings.RAIN_GRID_EAST)
        assert south == pytest.approx(settings.RAIN_GRID_SOUTH)
        # Presupuesto de Open-Meteo: las corridas del día más las 36 comunas
        # cada 30 min (1728).
        corridas = 86_400 // settings.RAIN_GRID_POLL_INTERVAL_SECONDS
        assert len(grid.points()) * corridas + 1728 < 10_000

    def test_filas_de_norte_a_sur_y_columnas_de_oeste_a_este(self) -> None:
        grid = build_grid(west=-72.0, south=-33.2, east=-71.8, north=-33.0, step=0.1)
        points = grid.points()
        assert points[0] == (-33.0, -72.0)
        assert points[1] == (-33.0, -71.9)
        assert points[grid.nx] == (-33.1, -72.0)
        assert points[-1] == (-33.2, -71.8)

    def test_caja_vacia(self) -> None:
        with pytest.raises(ValueError):
            build_grid(west=-70, south=-33, east=-71, north=-32, step=0.1)


class TestLectura:
    def test_maximo_de_las_proximas_horas_sin_contar_las_pasadas(self) -> None:
        # Arranca 2 h antes de la hora en curso: esas dos no cuentan.
        item = _item([9.0, 9.0, 0.4, 3.2, 1.1], start=NOW.replace(minute=0) - timedelta(hours=2))
        assert max_next_hours(item, now=NOW, hours=24, origin="t") == 3.2

    def test_fuera_de_la_ventana_no_cuenta(self) -> None:
        item = _item([0.1] * 3 + [20.0])
        assert max_next_hours(item, now=NOW, hours=3, origin="t") == 0.1

    def test_sin_datos_es_none_y_no_cero(self) -> None:
        assert max_next_hours(_item([None, None]), now=NOW, hours=24, origin="t") is None
        assert max_next_hours(_item([0.0, 0.0]), now=NOW, hours=24, origin="t") == 0.0

    def test_un_lote_se_empareja_por_posicion(self) -> None:
        payload = [_item([1.0]), _item([2.5]), _item([None])]
        assert parse_chunk(payload, 3, now=NOW, hours=24, origin="t") == [1.0, 2.5, None]

    def test_un_punto_solo_viene_como_objeto(self) -> None:
        assert parse_chunk(_item([4.0]), 1, now=NOW, hours=24, origin="t") == [4.0]

    def test_otra_cantidad_de_puntos_es_un_error(self) -> None:
        with pytest.raises(CollectorError, match="se pidieron 2"):
            parse_chunk([_item([1.0])], 2, now=NOW, hours=24, origin="t")

    def test_error_de_open_meteo(self) -> None:
        with pytest.raises(CollectorError, match="Parameter"):
            parse_chunk(
                {"error": True, "reason": "Parameter x"}, 1, now=NOW, hours=24, origin="t"
            )


class FakeRepo:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None
        self.failure: str | None = None

    async def save(self, key: str, **kwargs: Any) -> None:
        self.saved = {"key": key, **kwargs}

    async def mark_failure(self, key: str, *, error: str, now: datetime) -> None:
        self.failure = error


class FakeSession:
    commits = 0

    async def commit(self) -> None:
        self.commits += 1


class TestCorrida:
    async def test_guarda_la_foto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = FakeRepo()
        monkeypatch.setattr(svc, "WeatherGridRepository", lambda _s: repo)

        async def fetch(grid: Any, *, now: datetime) -> list[float | None]:
            return [0.0] * (grid.nx * grid.ny)

        session: Any = FakeSession()
        assert await refresh_rain_grid(session, now=NOW, fetch=fetch)
        assert repo.saved is not None and repo.saved["key"] == "lluvia"
        assert len(repo.saved["values"]) == 650  # 25 × 26: la caja de MAP_MAX_BOUNDS
        assert repo.saved["hours"] == settings.RAIN_GRID_HOURS

    async def test_un_fallo_se_anota_y_no_pisa_la_foto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = FakeRepo()
        monkeypatch.setattr(svc, "WeatherGridRepository", lambda _s: repo)

        async def fetch(grid: Any, *, now: datetime) -> list[float | None]:
            raise CollectorError("open-meteo grilla [lote 3/11]: sin respuesta")

        session: Any = FakeSession()
        assert not await refresh_rain_grid(session, now=NOW, fetch=fetch)
        assert repo.saved is None
        assert repo.failure is not None and "lote 3/11" in repo.failure

    async def test_pide_la_grilla_por_lotes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pedidos: list[int] = []

        async def request_json(_client: Any, _url: str, params: dict[str, str], **_: Any) -> Any:
            n = len(params["latitude"].split(","))
            pedidos.append(n)
            assert params["cell_selection"] == "nearest"
            assert params["hourly"] == "precipitation"
            return [_item([0.5])] * n

        monkeypatch.setattr(svc, "request_json", request_json)
        pausas: list[float] = []

        async def sleep(seconds: float) -> None:
            pausas.append(seconds)

        monkeypatch.setattr(svc, "_pausa", sleep)
        grid = build_grid(west=-72.3, south=-34.0, east=-69.8, north=-31.9, step=0.15)
        http: Any = SimpleNamespace()
        values = await svc.fetch_grid(grid, now=NOW, client=http)
        assert len(values) == 255
        assert sum(pedidos) == 255
        assert max(pedidos) <= settings.RAIN_GRID_CHUNK_SIZE
        # Una pausa ENTRE lotes (no antes del primero): el límite por minuto de
        # Open-Meteo cortó la primera corrida de la caja grande en el lote 25.
        assert len(pausas) == len(pedidos) - 1
        assert all(p == settings.RAIN_GRID_BATCH_PAUSE_SECONDS for p in pausas)
        pausa = settings.RAIN_GRID_BATCH_PAUSE_SECONDS
        assert settings.RAIN_GRID_CHUNK_SIZE * 60 / pausa < 600  # puntos por minuto


class TestArranque:
    """Un arranque del proceso no relee la grilla si la foto sirve (§L)."""

    def _row(self, **over: Any) -> SimpleNamespace:
        grid = grid_from_settings()
        base: dict[str, Any] = {
            "generated_at": NOW - timedelta(hours=1),
            "attempted_at": NOW - timedelta(hours=1),
            "error": None,
            "nx": grid.nx,
            "ny": grid.ny,
            "step": grid.step,
            "west": grid.west,
            "north": grid.north,
            "model": settings.OPENMETEO_MODEL,
            "hours": settings.RAIN_GRID_HOURS,
        }
        base.update(over)
        return SimpleNamespace(**base)

    def _wait(self, row: Any) -> float:
        return seconds_until_due(row, grid_from_settings(), now=NOW)

    def test_foto_vigente_espera_el_resto_de_la_cadencia(self) -> None:
        restante = settings.RAIN_GRID_POLL_INTERVAL_SECONDS - 3600
        assert self._wait(self._row()) == pytest.approx(restante)

    def test_foto_vencida_o_ausente_toca_ya(self) -> None:
        vieja = NOW - timedelta(seconds=settings.RAIN_GRID_POLL_INTERVAL_SECONDS + 1)
        assert self._wait(self._row(generated_at=vieja)) == 0
        assert self._wait(None) == 0
        assert self._wait(self._row(generated_at=None, error=None)) == 0

    def test_foto_de_otra_caja_toca_ya(self) -> None:
        # El deploy que cambia la caja (o el paso, o el modelo) no espera 3 h.
        assert self._wait(self._row(nx=19, ny=15)) == 0
        assert self._wait(self._row(step=0.15)) == 0
        assert self._wait(self._row(west=-72.3)) == 0
        assert self._wait(self._row(model="otro")) == 0
        assert self._wait(self._row(hours=12)) == 0

    def test_tras_un_fallo_respeta_el_reintento(self) -> None:
        # Un deploy en medio de un 429 no vuelve a pedir enseguida.
        row = self._row(error="HTTP 429", attempted_at=NOW - timedelta(minutes=5))
        assert self._wait(row) == pytest.approx(settings.RAIN_GRID_RETRY_SECONDS - 300)
        viejo = self._row(error="HTTP 429", attempted_at=NOW - timedelta(hours=1))
        assert self._wait(viejo) == 0


class TestEstado:
    def _row(self, **over: Any) -> SimpleNamespace:
        base: dict[str, Any] = {"generated_at": NOW - timedelta(minutes=30), "error": None}
        base.update(over)
        return SimpleNamespace(**base)

    def test_estados(self) -> None:
        assert grid_state(None, now=NOW) == "never"
        assert grid_state(self._row(generated_at=None), now=NOW) == "never"
        assert grid_state(self._row(), now=NOW) == "ok"
        assert grid_state(self._row(error="x"), now=NOW) == "failing"
        # Tres cadencias sin foto (3 × 3 h): el proceso de workers no corre.
        assert grid_state(self._row(generated_at=NOW - timedelta(hours=5)), now=NOW) == "ok"
        assert grid_state(self._row(generated_at=NOW - timedelta(hours=10)), now=NOW) == "stale"


class FakeService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def current(self) -> dict[str, Any]:
        return self.payload


@pytest.fixture
def client() -> Any:
    yield TestClient(app)
    app.dependency_overrides.pop(get_rain_grid_service, None)


class TestRuta:
    def test_sin_lectura_todavia(self, client: TestClient) -> None:
        app.dependency_overrides[get_rain_grid_service] = lambda: FakeService(
            {"grilla": None, "fuente": {"estado": "never", "ultima_lectura": None, "detalle": None}}
        )
        data = client.get("/api/v1/events/weather/grid").json()
        assert data == {
            "grilla": None,
            "fuente": {"estado": "never", "ultima_lectura": None, "detalle": None},
        }

    def test_con_grilla(self, client: TestClient) -> None:
        row = SimpleNamespace(
            generated_at=NOW,
            model="best_match",
            hours=24,
            step=0.15,
            west=-72.3,
            north=-31.9,
            nx=2,
            ny=1,
            values=[0.0, 3.4],
            error=None,
        )

        class Repo:
            async def get(self, _key: str) -> Any:
                return row

        service = RainGridService.__new__(RainGridService)
        service.repo = Repo()  # type: ignore[assignment]
        app.dependency_overrides[get_rain_grid_service] = lambda: service
        data = client.get("/api/v1/events/weather/grid").json()
        assert data["grilla"]["valores"] == [0.0, 3.4]
        assert data["grilla"]["nx"] == 2 and data["grilla"]["norte"] == -31.9
        assert data["fuente"]["estado"] in {"ok", "stale"}
