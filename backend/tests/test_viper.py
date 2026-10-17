"""El punto de un despacho leído de su imagen de Viper, sin red.

Un «mundo» sintético hace de mosaicos de OSM: textura con bordes, servida por
`respx` en la URL de los mosaicos. La imagen de Viper se arma recortando ese
mundo alrededor de un punto conocido, con el encabezado blanco, el marcador en
el centro y el pie, como las reales. Lo que se prueba es que `calzar` encuentre
ese punto desde una semilla corrida, y que todo lo demás falle hacia el punto
que el despacho ya tenía.
"""

from __future__ import annotations

import asyncio
import io
import re

import httpx
import numpy as np
import pytest
import respx
from PIL import Image

from app.collectors import osm_tiles, viper
from app.collectors.nominatim import GeocodeResult
from app.collectors.overpass import distancia_m
from app.collectors.traffic import bomberos_10_4_worker as worker
from app.core.config import settings
from app.services.apify_webhook_service import tweet_imagen

# Esquina de Edwards con Aldunate, Valparaíso: el marcador de la imagen real.
VERDAD = (-33.047738, -71.619744)
LADO = 12  # mosaicos por lado del mundo sintético


def _mundo(semilla: int = 7) -> tuple[np.ndarray, int, int]:
    """Textura con bordes en todas las escalas, centrada en `VERDAD`."""
    rng = np.random.default_rng(semilla)
    n = LADO * osm_tiles.TAMANO
    base = rng.random((n // 8, n // 8))
    textura = np.kron(base, np.ones((8, 8)))
    textura += 0.5 * np.kron(rng.random((n // 32, n // 32)), np.ones((32, 32)))
    imagen = 60 + 170 * (textura - textura.min()) / (textura.max() - textura.min())
    gx, gy = viper.a_pixeles(*VERDAD)
    tx0 = int(gx // osm_tiles.TAMANO) - LADO // 2
    ty0 = int(gy // osm_tiles.TAMANO) - LADO // 2
    return imagen.astype(np.uint8), tx0, ty0


def _png(arreglo: np.ndarray) -> bytes:
    salida = io.BytesIO()
    Image.fromarray(arreglo).save(salida, format="PNG")
    return salida.getvalue()


def _servir_mosaicos(mundo: np.ndarray, tx0: int, ty0: int) -> respx.Route:
    patron = re.compile(r"/18/(\d+)/(\d+)\.png$")

    def responder(request: httpx.Request) -> httpx.Response:
        x, y = map(int, patron.search(request.url.path).groups())
        i, j = y - ty0, x - tx0
        if not (0 <= i < LADO and 0 <= j < LADO):
            return httpx.Response(200, content=_png(np.full((256, 256), 242, np.uint8)))
        t = osm_tiles.TAMANO
        return httpx.Response(200, content=_png(mundo[i * t : (i + 1) * t, j * t : (j + 1) * t]))

    return respx.get(url__regex=r"https://tile\.openstreetmap\.org/18/\d+/\d+\.png").mock(
        side_effect=responder
    )


def _imagen_viper(mundo: np.ndarray, tx0: int, ty0: int, centro=VERDAD, ancho=1080) -> bytes:
    """Encabezado blanco con texto, mapa con el marcador al centro y pie."""
    alto_mapa = 1034
    gx, gy = viper.a_pixeles(*centro)
    x0 = round(gx) - tx0 * osm_tiles.TAMANO - ancho // 2
    y0 = round(gy) - ty0 * osm_tiles.TAMANO - alto_mapa // 2
    mapa = mundo[y0 : y0 + alto_mapa, x0 : x0 + ancho].copy()
    yy, xx = np.mgrid[0:alto_mapa, 0:ancho]
    mapa[(yy - alto_mapa / 2) ** 2 + (xx - ancho / 2) ** 2 < 55**2] = 30  # marcador
    encabezado = np.full((226, ancho), 255, np.uint8)
    encabezado[40:90, 30:400] = 20  # «CLAVE 4-1»
    pie = np.full((90, ancho), 255, np.uint8)
    pie[20:70, 20:120] = 40  # «72»
    return _png(np.vstack([encabezado, mapa, pie]))


@pytest.fixture
def mundo():
    osm_tiles.reiniciar()
    yield _mundo()
    osm_tiles.reiniciar()


def _desplazado(punto, metros_norte: float, metros_este: float):
    lat, lon = punto
    return (lat + metros_norte / 111_320, lon + metros_este / (111_320 * np.cos(np.radians(lat))))


# --- El calce ------------------------------------------------------------------


@respx.mock
def test_calza_desde_una_semilla_corrida(mundo):
    imagen_mundo, tx0, ty0 = mundo
    _servir_mosaicos(imagen_mundo, tx0, ty0)
    _, mapa, _ = viper.leer(_imagen_viper(imagen_mundo, tx0, ty0))
    semilla = _desplazado(VERDAD, 120, -90)

    async def correr():
        async with httpx.AsyncClient() as client:
            return await viper.calzar(client, mapa, semilla, 250)

    calce = asyncio.run(correr())
    assert calce is not None
    assert distancia_m(VERDAD, (calce.lat, calce.lon)) < 1.5
    assert calce.ncc >= settings.VIPER_NCC_MIN
    assert calce.margen >= settings.VIPER_MARGEN_MIN
    assert 100 < calce.distancia_semilla_m < 200


@respx.mock
def test_una_imagen_de_otro_lugar_no_calza(mundo):
    imagen_mundo, tx0, ty0 = mundo
    _servir_mosaicos(imagen_mundo, tx0, ty0)
    otro, _, _ = _mundo(semilla=99)
    _, mapa, _ = viper.leer(_imagen_viper(otro, tx0, ty0))

    async def correr():
        async with httpx.AsyncClient() as client:
            return await viper.calzar(client, mapa, VERDAD, 250)

    assert asyncio.run(correr()) is None


def test_lo_que_no_es_una_imagen_de_viper_se_descarta(mundo):
    imagen_mundo, tx0, ty0 = mundo
    assert viper.leer(b"no es una imagen") is None
    chica = _imagen_viper(imagen_mundo, tx0, ty0, ancho=544)  # la versión chica de X
    assert viper.leer(chica) is None
    blanca = _png(np.full((1350, 1080), 255, np.uint8))
    assert viper.leer(blanca) is None


def test_el_recorte_deja_fuera_encabezado_y_pie(mundo):
    imagen_mundo, tx0, ty0 = mundo
    _, mapa, fila = viper.leer(_imagen_viper(imagen_mundo, tx0, ty0))
    assert fila == 226
    assert mapa.shape == (1034, 1080)


def test_sólo_imagenes_de_x():
    assert (
        viper.url_de_imagen("https://pbs.twimg.com/media/HUwC8BSXgAAxtCz.jpg")
        == "https://pbs.twimg.com/media/HUwC8BSXgAAxtCz.jpg?name=orig"
    )
    assert viper.url_de_imagen("http://pbs.twimg.com/media/x.jpg") is None
    assert viper.url_de_imagen("https://ejemplo.cl/x.jpg") is None
    assert viper.url_de_imagen(None) is None


def test_la_foto_del_tuit_sale_del_campo_media():
    item = {
        "media": [
            {
                "type": "photo",
                "mediaUrl": "https://pbs.twimg.com/media/HUwC8BSXgAAxtCz.jpg",
                "url": "https://t.co/8VFGQpqujP",
            }
        ]
    }
    assert tweet_imagen(item) == "https://pbs.twimg.com/media/HUwC8BSXgAAxtCz.jpg"
    otro_actor = {"extendedEntities": {"media": [{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/a.jpg"}]}}
    assert tweet_imagen(otro_actor) == "https://pbs.twimg.com/media/a.jpg"
    assert tweet_imagen({"media": [{"type": "video", "mediaUrl": "https://pbs.twimg.com/v.jpg"}]}) is None
    assert tweet_imagen({"text": "sin foto"}) is None


# --- Los despachos ---------------------------------------------------------------


def _despacho(punto: GeocodeResult | None) -> worker.Dispatch:
    return worker.Dispatch(
        key="4-1",
        address=None,
        occurred_at=None,
        commune=None,
        raw_text="72 * EDWARDS / ALDUNATE * CLAVE 4-1",
        cuenta="@CGI_CBV",
        decoded={"street_1": "EDWARDS", "street_2": "ALDUNATE", "city": None},
        point=punto,
        imagen_url="https://pbs.twimg.com/media/prueba.jpg",
    )


def _esquina() -> GeocodeResult:
    lat, lon = _desplazado(VERDAD, 40, 30)
    return GeocodeResult(lat=lat, lon=lon, precision="intersection", provider="osm_local", comuna="Valparaíso")


@pytest.fixture
def viper_encendido(monkeypatch, mundo):
    monkeypatch.setattr(settings, "VIPER_ENABLED", True)
    imagen_mundo, tx0, ty0 = mundo
    respx.get("https://pbs.twimg.com/media/prueba.jpg?name=orig").mock(
        return_value=httpx.Response(200, content=_imagen_viper(imagen_mundo, tx0, ty0))
    )
    return _servir_mosaicos(imagen_mundo, tx0, ty0)


@respx.mock
def test_el_marcador_reemplaza_a_la_esquina(viper_encendido):
    salida, ubicados = asyncio.run(viper.ubicar_por_imagen([_despacho(_esquina())], max_imagenes=5))
    assert ubicados == 1
    punto = salida[0].point
    assert punto is not None
    assert punto.provider == "viper" and punto.precision == "despacho"
    assert distancia_m(VERDAD, (punto.lat, punto.lon)) < 1.5
    assert punto.comuna == "Valparaíso"
    assert punto.viper is not None and punto.viper["semilla_provider"] == "osm_local"
    guardado = GeocodeResult.desde_dict(punto.as_dict())
    assert guardado is not None and guardado.viper == punto.viper


@respx.mock
def test_con_el_tope_diario_queda_el_punto_que_tenia(viper_encendido, monkeypatch):
    monkeypatch.setattr(settings, "VIPER_MAX_MOSAICOS_DIA", 3)
    esquina = _esquina()
    salida, ubicados = asyncio.run(viper.ubicar_por_imagen([_despacho(esquina)], max_imagenes=5))
    assert ubicados == 0
    assert salida[0].point == esquina


@respx.mock
def test_una_imagen_que_no_baja_no_rompe_el_lote(viper_encendido):
    respx.get("https://pbs.twimg.com/media/prueba.jpg?name=orig").mock(
        return_value=httpx.Response(404)
    )
    esquina = _esquina()
    salida, ubicados = asyncio.run(viper.ubicar_por_imagen([_despacho(esquina)], max_imagenes=5))
    assert ubicados == 0 and salida[0].point == esquina


def test_apagado_no_hace_nada():
    despacho = _despacho(_esquina())
    assert asyncio.run(viper.ubicar_por_imagen([despacho], max_imagenes=5)) == ([despacho], 0)


def test_el_despacho_tiene_el_mejor_rango_de_precision():
    from app.repositories.incident_repository import ubicacion_de

    punto = ubicacion_de(
        [(-33.0, -71.0, 1.0, "despacho"), (-33.1, -71.1, 1.0, "intersection"), (-33.2, -71.2, 1.0, None)]
    )
    assert punto is not None and punto.precision == "despacho" and punto.lat == -33.0


# --- Una sola calle: se busca a lo largo de ella --------------------------------


def _despacho_una_calle() -> worker.Dispatch:
    return worker.Dispatch(
        key="1-1",
        address=None,
        occurred_at=None,
        commune="Valparaíso",
        raw_text="U-11 * CALLE LA JARILLA * CLAVE 1-1",
        cuenta="@CGI_CBV",
        decoded={"street_1": "CALLE LA JARILLA", "street_2": None, "city": None},
        point=None,
        imagen_url="https://pbs.twimg.com/media/prueba.jpg",
    )


@respx.mock
def test_una_sola_calle_se_ubica_con_las_esquinas_de_esa_calle(viper_encendido, monkeypatch):
    """Antes: sin transversal no había punto, sin punto no había semilla, y la
    imagen —que tiene el lugar exacto— se descartaba."""
    from app.collectors import cruces_locales

    pedidas = []

    async def esquinas(calle, comunas, *, separacion_m, maximo):
        pedidas.append((calle, tuple(comunas)))
        # La primera esquina está lejos; la segunda, a 150 m del marcador.
        return [_desplazado(VERDAD, 1500, 0), _desplazado(VERDAD, 120, 90)]

    monkeypatch.setattr(cruces_locales, "puntos_de_calle", esquinas)
    salida, ubicados = asyncio.run(
        viper.ubicar_por_imagen([_despacho_una_calle()], max_imagenes=5)
    )
    assert pedidas == [("CALLE LA JARILLA", ("Valparaíso",))]
    assert ubicados == 1
    punto = salida[0].point
    assert punto is not None and punto.provider == "viper"
    assert distancia_m(VERDAD, (punto.lat, punto.lon)) < 1.5


@respx.mock
def test_una_calle_sin_esquinas_en_el_indice_queda_sin_punto(viper_encendido, monkeypatch):
    from app.collectors import cruces_locales

    async def ninguna(*_args, **_kwargs):
        return []

    monkeypatch.setattr(cruces_locales, "puntos_de_calle", ninguna)
    monkeypatch.setattr(settings, "VIPER_MAX_LECTURAS_POR_ENTREGA", 0)
    salida, ubicados = asyncio.run(
        viper.ubicar_por_imagen([_despacho_una_calle()], max_imagenes=5)
    )
    assert ubicados == 0 and salida[0].point is None
