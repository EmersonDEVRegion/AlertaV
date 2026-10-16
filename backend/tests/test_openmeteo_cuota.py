"""Cuota de Open-Meteo: un 429 por límite diario u horario pausa las consultas
hasta que la cuota se renueva, en vez de insistir cada ciclo."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.geoservices import request_json
from app.collectors.weather import cuota
from app.collectors.weather.comunas import Comuna
from app.collectors.weather.openmeteo_client import OpenMeteoClient
from app.core.exceptions import CollectorError

API_URL = "https://api.open-meteo.test/v1/forecast"
AHORA = datetime(2026, 10, 15, 16, 30, tzinfo=UTC)
DIARIO = {"error": True, "reason": "Daily API request limit exceeded. Please try again tomorrow."}
HORARIO = {"error": True, "reason": "Hourly API request limit exceeded. Please try again in the next hour."}


def _cliente() -> OpenMeteoClient:
    return OpenMeteoClient(
        comunas=[Comuna("Valparaíso", -33.0472, -71.6127)],
        url=API_URL,
        timeout=5.0,
        model="best_match",
    )


@pytest.mark.parametrize(
    ("motivo", "esperado"),
    [
        (DIARIO["reason"], datetime(2026, 10, 16, 0, 0, tzinfo=UTC)),
        (HORARIO["reason"], datetime(2026, 10, 15, 17, 0, tzinfo=UTC)),
        ("Minutely API request limit exceeded.", None),
        (None, None),
    ],
)
def test_cada_limite_se_renueva_cuando_corresponde(motivo, esperado) -> None:
    assert cuota.renovacion(motivo, now=AHORA) == esperado


@respx.mock
async def test_el_429_trae_el_motivo_de_la_fuente() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(429, json=DIARIO))

    async with httpx.AsyncClient() as client:
        with pytest.raises(CollectorError, match="Daily API request limit") as excinfo:
            await request_json(client, API_URL, {}, origin="open-meteo")

    assert excinfo.value.detail["motivo"].startswith("Daily API request limit")


@respx.mock
async def test_agotada_la_cuota_diaria_no_se_vuelve_a_pedir() -> None:
    ruta = respx.get(API_URL).mock(return_value=httpx.Response(429, json=DIARIO))
    cliente = _cliente()

    with pytest.raises(CollectorError, match="rate limit"):
        await cliente.fetch_forecast()
    with pytest.raises(CollectorError, match="cuota de Open-Meteo agotada"):
        await cliente.fetch_forecast()

    assert ruta.call_count == 1, "la segunda corrida no debe salir a la red"


@respx.mock
async def test_el_limite_por_minuto_no_pausa() -> None:
    ruta = respx.get(API_URL).mock(
        return_value=httpx.Response(429, json={"error": True, "reason": "Minutely API request limit exceeded."})
    )
    cliente = _cliente()

    for _ in range(2):
        with pytest.raises(CollectorError, match="rate limit"):
            await cliente.fetch_forecast()

    assert ruta.call_count == 2


def test_la_pausa_se_levanta_al_renovarse_la_cuota() -> None:
    error = CollectorError("429", detail={"status": 429, "motivo": DIARIO["reason"]})
    cuota.registrar(error, now=AHORA)

    with pytest.raises(CollectorError):
        cuota.verificar("open-meteo", now=AHORA)
    cuota.verificar("open-meteo", now=datetime(2026, 10, 16, 0, 0, 1, tzinfo=UTC))
