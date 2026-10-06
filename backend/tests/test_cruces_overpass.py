"""Cruces de calles con Overpass (§K, 2026-10-05).

Los dos casos que lo motivaron, del 05-10-2026 en Valparaíso:

* «LAS MONJAS / ANDRES BELLO» (INC-2026-00872): Nominatim devolvía el comienzo
  de Las Monjas en Av. Colón, a ~200 m de la esquina con Andrés Bello.
* «AVENIDA ALEMANIA / GUILLERMO RIVERA» (INC-2026-00887): la avenida cruza los
  cerros por kilómetros y el pin cayó en otro cerro.

La geometría de los tests imita esos dos casos con coordenadas inventadas: lo
que se prueba es el cálculo, no los datos de OSM.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from app.collectors import nominatim, overpass
from app.collectors.nominatim import GeocodeResult, RateLimiter, geocode
from app.collectors.overpass import (
    calcular_cruce,
    nombres_calzan,
    patron_servidor,
    vias_desde_respuesta,
)
from app.core.config import settings


def via(id_: int, nombre: str, nodos: list[tuple[int, float, float]]) -> dict[str, Any]:
    return {
        "type": "way",
        "id": id_,
        "tags": {"name": nombre, "highway": "residential"},
        "nodes": [n for n, _, _ in nodos],
        "geometry": [{"lat": lat, "lon": lon} for _, lat, lon in nodos],
    }


# Las Monjas baja desde Av. Colón (nodo 1) hasta la esquina con Andrés Bello (3).
LAS_MONJAS = via(
    100, "Las Monjas", [(1, -33.0480, -71.6160), (2, -33.0490, -71.6155), (3, -33.0500, -71.6150)]
)
ANDRES_BELLO = via(
    200, "Andrés Bello", [(5, -33.0495, -71.6140), (3, -33.0500, -71.6150), (4, -33.0510, -71.6145)]
)
AV_COLON = via(300, "Avenida Colón", [(1, -33.0480, -71.6160), (9, -33.0475, -71.6140)])

# Av. Alemania en dos tramos; Guillermo Rivera la cruza en el nodo 13. Otra
# «Rivera» la cruza lejos, en el nodo 11.
ALEMANIA_1 = via(
    400,
    "Avenida Alemania",
    [(10, -33.0400, -71.6300), (11, -33.0420, -71.6250), (12, -33.0440, -71.6200)],
)
ALEMANIA_2 = via(
    401,
    "Avenida Alemania",
    [(12, -33.0440, -71.6200), (13, -33.0460, -71.6150), (14, -33.0480, -71.6100)],
)
GUILLERMO_RIVERA = via(
    500, "Guillermo Rivera", [(20, -33.0450, -71.6160), (13, -33.0460, -71.6150)]
)
OTRA_RIVERA = via(501, "Guillermo Rivera", [(30, -33.0410, -71.6260), (11, -33.0420, -71.6250)])


def respuesta(*vias: dict[str, Any]) -> dict[str, Any]:
    return {"elements": list(vias)}


# --- Nombres --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fuente", "osm"),
    [
        ("LAS MONJAS", "Las Monjas"),
        ("ANDRES BELLO", "Andrés Bello"),
        ("AVENIDA ALEMANIA", "Avenida Alemania"),
        ("ALEMANIA", "Avenida Alemania"),
        ("Av. Alemania", "Avenida Alemania"),
        ("ALVAREZ", "Doctor Álvarez"),
        ("PDTE. ERRAZURIZ", "Presidente Errázuriz"),
        ("GRAL. CRUZ", "General Cruz"),
    ],
)
def test_nombres_que_calzan(fuente, osm):
    assert nombres_calzan(fuente, osm)


@pytest.mark.parametrize(
    ("fuente", "osm"),
    [
        ("ANDRES BELLO", "Bello"),
        ("LAS MONJAS", "Las Monjas Altas"),
        ("ALEMANIA", "Pasaje Alemania Norte"),
        ("", "Las Monjas"),
    ],
)
def test_nombres_que_no_calzan(fuente, osm):
    assert not nombres_calzan(fuente, osm)


def test_el_filtro_del_servidor_ignora_tipo_de_via_y_prueba_las_tildes():
    import re

    patron = patron_servidor("AVENIDA ALEMANIA")
    assert patron is not None and "avenida" not in patron
    for nombre in ("Avenida Alemania", "alemania"):
        assert re.search(patron, nombre, re.IGNORECASE)
    patron = patron_servidor("ANDRES BELLO")
    assert patron is not None
    assert re.search(patron, "Andrés Bello", re.IGNORECASE)
    assert patron_servidor("AV") is None


def test_sin_clases_de_caracteres_por_la_compilacion_byte_a_byte():
    patron = patron_servidor("ANGEL GUARELLO")
    assert patron is not None and "[" not in patron
    assert patron.startswith("(guarello|")
    assert "Ángel" in (patron_servidor("ANGEL") or "")


# --- Geometría ------------------------------------------------------------------


def test_las_monjas_con_andres_bello_cae_en_la_esquina():
    vias = vias_desde_respuesta(respuesta(LAS_MONJAS, ANDRES_BELLO, AV_COLON))
    cruce = calcular_cruce(vias, "LAS MONJAS", "ANDRES BELLO", cerca_de=(-33.0480, -71.6160))
    assert cruce is not None
    assert (cruce.lat, cruce.lon) == (-33.05, -71.615)
    assert cruce.metodo == "nodo"
    assert cruce.via_2 == "Andrés Bello"


def test_con_dos_cruces_gana_el_cercano_al_punto_de_nominatim():
    vias = vias_desde_respuesta(respuesta(ALEMANIA_1, ALEMANIA_2, GUILLERMO_RIVERA, OTRA_RIVERA))
    cerca_del_13 = (-33.0465, -71.6140)
    cruce = calcular_cruce(vias, "AVENIDA ALEMANIA", "GUILLERMO RIVERA", cerca_de=cerca_del_13)
    assert cruce is not None
    assert (cruce.lat, cruce.lon) == (-33.046, -71.615)
    assert cruce.candidatos == 2


def test_sin_nodo_comun_vale_la_proximidad():
    """Una calle que muere a ~20 m de la otra sin tocarla."""
    cortada = via(600, "Andrés Bello", [(40, -33.0510, -71.6140), (41, -33.0502, -71.6148)])
    vias = vias_desde_respuesta(respuesta(LAS_MONJAS, cortada))
    cruce = calcular_cruce(vias, "LAS MONJAS", "ANDRES BELLO", max_separacion_m=60)
    assert cruce is not None and cruce.metodo == "proximidad"
    assert 0 < cruce.separacion_m <= 60


def test_calles_que_no_se_tocan_no_dan_cruce():
    lejos = via(700, "Andrés Bello", [(50, -33.0600, -71.6000), (51, -33.0610, -71.5990)])
    vias = vias_desde_respuesta(respuesta(LAS_MONJAS, lejos))
    assert calcular_cruce(vias, "LAS MONJAS", "ANDRES BELLO", max_separacion_m=60) is None


def test_si_falta_una_calle_no_hay_cruce():
    vias = vias_desde_respuesta(respuesta(LAS_MONJAS, AV_COLON))
    assert calcular_cruce(vias, "LAS MONJAS", "ANDRES BELLO") is None


def test_una_via_rota_en_la_respuesta_se_ignora():
    rota = {
        "type": "way",
        "id": 9,
        "tags": {"name": "Andrés Bello"},
        "nodes": [1, 2],
        "geometry": [{}],
    }
    assert vias_desde_respuesta({"elements": [rota, "basura"]}) == []


def test_la_consulta_usa_la_caja_o_el_radio():
    con_caja = overpass.armar_consulta(
        "LAS MONJAS", "ANDRES BELLO", caja=(-71.72, -33.13, -71.53, -32.99), cerca_de=None
    )
    assert con_caja is not None and "(-33.13,-71.72,-32.99,-71.53)" in con_caja
    con_radio = overpass.armar_consulta(
        "LAS MONJAS", "ANDRES BELLO", caja=None, cerca_de=(-33.05, -71.61)
    )
    assert con_radio is not None and "around:" in con_radio
    assert overpass.armar_consulta("LAS MONJAS", "ANDRES BELLO", caja=None, cerca_de=None) is None


# --- Integración con `geocode` ------------------------------------------------------


@pytest.fixture
def overpass_encendido(monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_ENABLED", True)
    monkeypatch.setattr(overpass, "_LIMITER", RateLimiter(0.0))
    monkeypatch.setattr(nominatim, "_LIMITER", RateLimiter(0.0))


def nominatim_las_monjas() -> list[dict[str, Any]]:
    return [
        {
            "lat": "-33.0480",
            "lon": "-71.6160",
            "display_name": "Las Monjas, Valparaíso",
            "addresstype": "road",
            "address": {"city": "Valparaíso"},
        }
    ]


def geocodificar() -> GeocodeResult | None:
    async def correr():
        async with httpx.AsyncClient() as client:
            return await geocode(
                client,
                {"street_1": "LAS MONJAS", "street_2": "ANDRES BELLO", "city": "Valparaíso"},
                limiter=RateLimiter(0.0),
            )

    return asyncio.run(correr())


@respx.mock
def test_geocode_lleva_el_punto_a_la_esquina(overpass_encendido):
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_las_monjas())
    )
    ruta = respx.post(settings.OVERPASS_URLS[0]).mock(
        return_value=httpx.Response(200, json=respuesta(LAS_MONJAS, ANDRES_BELLO))
    )
    punto = geocodificar()
    assert punto is not None
    assert (punto.lat, punto.lon) == (-33.05, -71.615)
    assert punto.precision == "intersection"
    assert punto.provider == "overpass"
    assert "street_2" not in punto.omitted
    assert punto.cruce is not None and punto.cruce["nominatim_lat"] == -33.048
    # La caja de Valparaíso acota la búsqueda.
    assert b"-33.13" in ruta.calls[0].request.content

    # Ida y vuelta por `raw_data._geocoding`.
    guardado = GeocodeResult.desde_dict(punto.as_dict())
    assert guardado is not None and guardado.provider == "overpass"
    assert guardado.cruce == punto.cruce


@respx.mock
def test_si_overpass_falla_queda_el_punto_sobre_la_calle(overpass_encendido):
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_las_monjas())
    )
    for url in settings.OVERPASS_URLS:
        respx.post(url).mock(return_value=httpx.Response(504))
    punto = geocodificar()
    assert punto is not None
    assert (punto.lat, punto.lon) == (-33.048, -71.616)
    assert punto.precision == "street"
    assert punto.provider == "nominatim"


@respx.mock
def test_si_el_primer_servidor_esta_saturado_se_usa_el_segundo(overpass_encendido):
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_las_monjas())
    )
    respx.post(settings.OVERPASS_URLS[0]).mock(return_value=httpx.Response(429))
    respx.post(settings.OVERPASS_URLS[1]).mock(
        return_value=httpx.Response(200, json=respuesta(LAS_MONJAS, ANDRES_BELLO))
    )
    punto = geocodificar()
    assert punto is not None and punto.precision == "intersection"


@respx.mock
def test_con_una_sola_calle_no_se_consulta_overpass(overpass_encendido):
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_las_monjas())
    )
    ruta = respx.post(settings.OVERPASS_URLS[0])

    async def correr():
        async with httpx.AsyncClient() as client:
            return await geocode(
                client, {"street_1": "LAS MONJAS", "city": "Valparaíso"}, limiter=RateLimiter(0.0)
            )

    punto = asyncio.run(correr())
    assert punto is not None and punto.precision == "street"
    assert not ruta.called


@respx.mock
def test_sin_nominatim_pero_con_caja_se_busca_el_cruce_directo(overpass_encendido):
    """Nominatim no reconoce ninguna de las dos; Overpass sí tiene las vías."""
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(settings.OVERPASS_URLS[0]).mock(
        return_value=httpx.Response(200, json=respuesta(LAS_MONJAS, ANDRES_BELLO))
    )
    punto = geocodificar()
    assert punto is not None and punto.precision == "intersection"
    assert punto.cruce is not None and punto.cruce["nominatim_lat"] is None


def test_apagado_no_sale_a_la_red():
    async def correr():
        async with httpx.AsyncClient() as client:
            return await overpass.cruce(client, "A", "B", cerca_de=(-33.0, -71.6))

    assert asyncio.run(correr()) is None
