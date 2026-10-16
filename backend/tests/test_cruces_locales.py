"""El índice local de cruces: nombres, búsqueda y su lugar en la geocodificación.

Los nombres son los de despachos reales de octubre de 2026; las coordenadas
son aproximadas: lo que se prueba es la lógica, no los datos de OSM.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from app.collectors import cruces_locales, nominatim
from app.collectors.nominatim import RateLimiter, geocode
from app.collectors.traffic import bomberos_10_4_worker as worker
from app.core import database
from app.core.config import settings
from app.core.nombres_via import nombre_nucleo, nombres_calzan, parecidos, ultima_palabra

# (nombre_a, nombre_b, lat, lon, comunas)
ARLEGUI = ("Arlegui", "Traslaviña", -33.02450, -71.55300, ["vina del mar"])
# Avenida Yungay de San Felipe tiene dos calzadas: dos nodos, una esquina.
YUNGAY_1 = ("Avenida Yungay", "Santo Domingo", -32.75023, -70.71990, ["san felipe"])
YUNGAY_2 = ("Santo Domingo", "Avenida Yungay", -32.75030, -70.71950, ["san felipe"])
# «PRAT / YUNGAY» existe en Quillota y en San Felipe.
PRAT_QUILLOTA = ("Arturo Prat", "Yungay", -32.88100, -71.24900, ["quillota"])
PRAT_SAN_FELIPE = ("Arturo Prat", "Avenida Yungay", -32.75250, -70.72100, ["san felipe"])
# Avenida Alemania cruza dos veces a una «Guillermo Rivera», lejos una de otra.
ALEMANIA_A = ("Avenida Alemania", "Guillermo Rivera", -33.04600, -71.61500, ["valparaiso"])
ALEMANIA_B = ("Guillermo Rivera", "Avenida Alemania", -33.04200, -71.62500, ["valparaiso"])
# OSM la escribe «Aiquina»; la central de Los Andes, «Ayquina».
AIQUINA = ("Avenida Pascual Baburizza", "Aiquina", -32.83500, -70.59800, ["los andes"])
# Una esquina de Concón, jurisdicción del CBVM junto con Viña.
CONCON = ("Avenida Borgoño", "Los Pescadores", -32.92800, -71.52000, ["concon"])

TODAS = [ARLEGUI, YUNGAY_1, YUNGAY_2, PRAT_QUILLOTA, PRAT_SAN_FELIPE, ALEMANIA_A, ALEMANIA_B, AIQUINA, CONCON]


# --- Nombres ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fuente", "osm"),
    [
        ("UNO NORTE", "1 Norte"),
        ("PASAJE DOS", "Pasaje 2"),
        ("TRASLAVINA", "Traslaviña"),
        ("21 DE MAYO", "Avenida 21 de Mayo"),
        ("PRAT", "Arturo Prat"),
    ],
)
def test_nombres_que_calzan(fuente, osm):
    assert nombres_calzan(fuente, osm)
    assert ultima_palabra(fuente) == ultima_palabra(osm)


def test_la_ultima_palabra_ignora_tipo_de_via_y_vacias():
    assert ultima_palabra("AVENIDA LIBERTADOR BERNARDO O'HIGGINS") == "higgins"
    assert ultima_palabra("Las Monjas") == "monjas"
    assert ultima_palabra("Avenida") is None
    assert nombre_nucleo("Calle Once") == ("11",)


@pytest.mark.parametrize(
    ("fuente", "limpia"),
    [
        ("1 PONIENTE 640", "1 PONIENTE"),
        ("ALVAREZ 1150", "ALVAREZ"),
        ("CALLE 13", "CALLE 13"),
        ("RUTA 160", "RUTA 160"),
        ("1500", "1500"),
    ],
)
def test_la_numeracion_pegada_se_saca_de_la_fuente(fuente, limpia):
    from app.core.nombres_via import sin_numeracion

    assert sin_numeracion(fuente) == limpia


def test_las_erratas_del_tipo_de_via_no_cuentan():
    assert nombres_calzan("Avendia Argentina Oriente", "Avenida Argentina Oriente")


def test_parecidos_acepta_errores_de_tipeo_y_no_nombres_cortos():
    assert parecidos("AYQUINA", "Aiquina", 0.85) is not None
    assert parecidos("RIO AYSEN", "Río Aisén", 0.85) is not None
    assert parecidos("LIRA", "Lima", 0.5) is None  # muy corto para adivinar
    assert parecidos("VALENZUELA", "Avenida Los Carrera", 0.85) is None


# --- Búsqueda -----------------------------------------------------------------------


class _Sesion:
    """Imita la consulta: filtra por comuna y por última palabra, como el SQL."""

    def __init__(self, filas: list[tuple[Any, ...]], llamadas: list[dict[str, Any]]):
        self.filas = filas
        self.llamadas = llamadas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _consulta, params):
        self.llamadas.append(params)
        return [
            (a, b, lat, lon, comunas)
            for a, b, lat, lon, comunas in self.filas
            if set(comunas) & set(params["comunas"])
            and (ultima_palabra(a) in params["ultimas"] or ultima_palabra(b) in params["ultimas"])
        ]


@pytest.fixture
def indice(monkeypatch):
    """El índice encendido, con `TODAS` como contenido. Devuelve las consultas hechas."""
    llamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "CRUCES_LOCALES_ENABLED", True)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Sesion(TODAS, llamadas))
    monkeypatch.setattr(nominatim, "_LIMITER", RateLimiter(0.0))
    return llamadas


def buscar(c1, c2, comunas):
    return asyncio.run(cruces_locales.buscar(c1, c2, comunas))


def test_una_esquina(indice):
    [cruce] = buscar("ARLEGUI", "TRASLAVINA", ["Viña del Mar"])
    assert (cruce.lat, cruce.lon) == (-33.0245, -71.553)
    assert (cruce.via_1, cruce.via_2) == ("Arlegui", "Traslaviña")
    assert cruce.metodo == cruces_locales.METODO_NODO
    assert indice[0]["comunas"] == ["vina del mar"]


def test_las_dos_calzadas_son_la_misma_esquina(indice):
    [cruce] = buscar("AVENIDA YUNGAY", "SANTO DOMINGO", ["San Felipe"])
    assert cruce.nodos == 2
    assert cruce.lat == pytest.approx(-32.750265)


def test_la_comuna_separa_las_homonimas(indice):
    [cruce] = buscar("PRAT", "YUNGAY", ["Quillota"])
    assert cruce.comunas == ("quillota",)
    [cruce] = buscar("PRAT", "YUNGAY", ["San Felipe"])
    assert cruce.comunas == ("san felipe",)


def test_dos_esquinas_lejanas_se_devuelven_las_dos(indice):
    cruces = buscar("AVENIDA ALEMANIA", "GUILLERMO RIVERA", ["Valparaíso"])
    assert len(cruces) == 2
    cerca = cruces_locales.mas_cercano(cruces, (-33.0419, -71.6251), maximo_m=1500)
    assert cerca is not None and cerca.lat == -33.042
    assert cruces_locales.mas_cercano(cruces, (-32.9, -71.5), maximo_m=1500) is None


def test_un_error_de_tipeo_se_acepta_si_la_otra_calle_calza(indice):
    [cruce] = buscar("AVENIDA PASCUAL BABURIZZA", "AYQUINA", ["Los Andes"])
    assert cruce.metodo == cruces_locales.METODO_PARECIDO
    assert cruce.parecido is not None and cruce.parecido >= 0.85


@pytest.mark.parametrize(
    ("c1", "c2", "comunas"),
    [
        ("ARLEGUI", "TRASLAVINA", []),  # sin comuna no se busca
        ("ARLEGUI", None, ["Viña del Mar"]),
        ("AVENIDA SAN MARTÍN", "SAN MARTIN", ["Viña del Mar"]),  # la misma calle
        ("ARLEGUI", "TRASLAVINA", ["Valparaíso"]),  # otra comuna
    ],
)
def test_lo_que_no_se_busca_o_no_esta(indice, c1, c2, comunas):
    assert buscar(c1, c2, comunas) == []


def test_apagado_no_consulta(indice, monkeypatch):
    monkeypatch.setattr(settings, "CRUCES_LOCALES_ENABLED", False)
    assert buscar("ARLEGUI", "TRASLAVINA", ["Viña del Mar"]) == []
    assert indice == []


def test_una_base_caida_no_rompe_la_geocodificacion(monkeypatch):
    class _Rota:
        async def __aenter__(self):
            raise OSError("sin base")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(settings, "CRUCES_LOCALES_ENABLED", True)
    monkeypatch.setattr(database, "AsyncSessionLocal", _Rota)
    assert buscar("ARLEGUI", "TRASLAVINA", ["Viña del Mar"]) == []


# --- En `geocode` -------------------------------------------------------------------


def geocodificar(calles: dict[str, Any], comuna: str | None = None):
    async def correr():
        async with httpx.AsyncClient() as client:
            return await geocode(client, calles, limiter=RateLimiter(0.0), comuna=comuna)

    return asyncio.run(correr())


@respx.mock
def test_con_una_esquina_no_se_llama_a_nominatim(indice):
    ruta = respx.get(url__startswith=settings.NOMINATIM_URL)
    punto = geocodificar({"street_1": "ARLEGUI", "street_2": "TRASLAVINA"}, comuna="Viña del Mar")
    assert punto is not None
    assert (punto.lat, punto.lon) == (-33.0245, -71.553)
    assert punto.precision == "intersection"
    assert punto.provider == "osm_local"
    assert punto.comuna == "Viña del Mar"
    assert "street_2" not in punto.omitted
    assert not ruta.called
    guardado = nominatim.GeocodeResult.desde_dict(punto.as_dict())
    assert guardado is not None and guardado.provider == "osm_local"


@respx.mock
def test_con_dos_esquinas_nominatim_desempata(indice):
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "-33.0425",
                    "lon": "-71.6240",
                    "addresstype": "road",
                    "address": {"city": "Valparaíso"},
                }
            ],
        )
    )
    punto = geocodificar(
        {"street_1": "AVENIDA ALEMANIA", "street_2": "GUILLERMO RIVERA"}, comuna="Valparaíso"
    )
    assert punto is not None and punto.provider == "osm_local"
    assert (punto.lat, punto.lon) == (-33.042, -71.625)
    assert punto.cruce is not None
    assert punto.cruce["candidatos"] == 2
    assert punto.cruce["nominatim_lat"] == pytest.approx(-33.0425)


@respx.mock
def test_numeracion_con_numero_en_osm(monkeypatch):
    monkeypatch.setattr(nominatim, "_LIMITER", RateLimiter(0.0))
    ruta = respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "-32.8301",
                    "lon": "-70.6012",
                    "addresstype": "building",
                    "address": {"house_number": "1400", "road": "Las Araucarias", "town": "Los Andes"},
                }
            ],
        )
    )
    punto = geocodificar(
        {"street_1": "Las Araucarias", "numero": "1400"}, comuna="Los Andes"
    )
    assert punto is not None and punto.precision == nominatim.PRECISION_ADDRESS
    assert "Las Araucarias 1400" in ruta.calls[0].request.url.params["q"]


@respx.mock
def test_numeracion_que_osm_no_tiene_cae_a_la_calle(monkeypatch):
    monkeypatch.setattr(nominatim, "_LIMITER", RateLimiter(0.0))
    respx.get(url__startswith=settings.NOMINATIM_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "-32.8301",
                    "lon": "-70.6012",
                    "addresstype": "road",
                    "address": {"road": "Las Araucarias", "town": "Los Andes"},
                }
            ],
        )
    )
    punto = geocodificar({"street_1": "Las Araucarias", "numero": "1400"}, comuna="Los Andes")
    assert punto is not None and punto.precision == "street"


# --- En los despachos ---------------------------------------------------------------


def despacho(cuenta: str, c1: str, c2: str | None) -> worker.Dispatch:
    return worker.Dispatch(
        key="5-1",
        address=None,
        occurred_at=None,
        commune=None,
        raw_text=f"Clave 5-1 {c1} / {c2 or '.'} U-63",
        cuenta=cuenta,
        decoded={"street_1": c1, "street_2": c2, "city": None},
    )


@respx.mock
def test_la_jurisdiccion_entera_se_busca_de_una_vez(indice):
    """Una esquina de Concón del CBVM no pasa primero por Nominatim en Viña."""
    ruta = respx.get(url__startswith=settings.NOMINATIM_URL)
    salida, resueltos = asyncio.run(
        worker.geocode_dispatches(
            [despacho("@CBVM132", "AVENIDA BORGOÑO", "LOS PESCADORES")], max_geocodes=5
        )
    )
    assert resueltos == 1
    punto = salida[0].point
    assert punto is not None and punto.provider == "osm_local"
    assert punto.comuna == "Concón"
    assert not ruta.called
    assert indice[0]["comunas"] == ["concon", "vina del mar"]


def test_numeracion_de_los_andes():
    aviso = "🚨 Despacho de emergencia — 07:53 h 10-6-1 · 10-6-1 (escape de gas) 📍 Las Araucarias Altura, altura 1400 · Sector 505 🚒 B-1, H-2"
    salida = worker.anotar_numeracion({"street_1": "Las Araucarias", "street_2": None}, aviso)
    assert salida["street_1"] == "Las Araucarias" and salida["numero"] == "1400"

    salida = worker.anotar_numeracion(
        {"street_1": "Ramón Trincado Altura 5", "street_2": None},
        "📍 Ramón Trincado Altura 5 · Sector 104 🚒 B-1",
    )
    assert salida == {"street_1": "Ramón Trincado", "street_2": None, "numero": "5"}

    # Con transversal manda la esquina: no hay número.
    salida = worker.anotar_numeracion(
        {"street_1": "Avenida Pascual Baburizza", "street_2": "Ayquina"},
        "📍 Avenida Pascual Baburizza / Ayquina · Sector 302",
    )
    assert "numero" not in salida
    # Una calle con cifras en el nombre no pierde nada.
    salida = worker.anotar_numeracion({"street_1": "AVENIDA 21 DE MAYO"}, "CLAVE 6-1 AVENIDA 21 DE MAYO M-34")
    assert salida == {"street_1": "AVENIDA 21 DE MAYO"}
