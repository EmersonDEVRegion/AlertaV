"""El incendio de Miraflores Alto: dos fuentes, un solo lugar.

El 2026-09-03 un incendio estructural en Miraflores Alto (Viña del Mar) llegó
por dos fuentes y el mapa lo dibujó en dos puntos, ninguno correcto:

* Pura Noticia: "Incendio en Viña del Mar: … fuego que consumió una casa en el
  sector de Miraflores Alto". El extractor leyó `"Viña del Mar:"` como calle y
  Nominatim devolvió la ciudad entera: pin en la plaza.
* El tuit: "… hasta calle once, en el sector de Miraflores Alto". El extractor
  leyó `"el"` como calle: pin a 2,5 km, en otro sector.

Este archivo fija las piezas que hacen que las dos señales apunten al mismo
lugar: el sector sale del texto (`lugares`), el extractor deja de devolver
artículos y comunas como calles, y el geocodificador verifica la calle contra el
sector o, si no hay calle, geocodifica el sector.

Las coordenadas de las respuestas falsas de Nominatim son **de prueba**: no son
las del sector real y no deben copiarse a ninguna parte.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from app.collectors import nominatim
from app.collectors.lugares import (
    anotar_sector,
    clave_sector,
    comuna_en_texto,
    sector_en_texto,
    sectores_compatibles,
)
from app.collectors.nominatim import (
    PRECISION_COMUNA,
    PRECISION_SECTOR,
    PRECISION_STREET,
    RateLimiter,
    build_queries,
    build_sector_query,
    geocode,
    precision_de,
    sector_matches,
)
from app.collectors.traffic.transporteinforma_worker import extract_streets_heuristic
from app.core.config import settings

PURA_NOTICIA = (
    "Incendio en Viña del Mar: equipos de emergencia combatieron fuego que "
    "consumió una casa en el sector de Miraflores Alto"
)
TUIT = (
    "Un incendio estructural declarado moviliza a esta hora a unidades del Cuerpo "
    "de Bomberos de Viña del Mar hasta calle once, en el sector de Miraflores "
    "Alto. De acuerdo a información preliminar, se trata de una vivienda que se "
    "encuentra completamente envuelta en llamas, existiendo además riesgo de "
    "propagación hacia inmuebles colindantes."
)

#: Puntos de PRUEBA. Ver el docstring del módulo.
PUNTO_SECTOR = ("-33.0301", "-71.5202")
PUNTO_CALLE_EN_SECTOR = ("-33.0312", "-71.5190")
PUNTO_OTRO_SECTOR = ("-33.0330", "-71.5720")
PUNTO_CIUDAD = ("-33.0245", "-71.5518")

NOMINATIM_URL = settings.NOMINATIM_URL


def _como_float(lat_lon: tuple[str, str]) -> tuple[float, float]:
    return (float(lat_lon[0]), float(lat_lon[1]))


def _resultado(
    lat_lon: tuple[str, str],
    *,
    addresstype: str,
    name: str,
    suburb: str | None = None,
    city: str = "Viña del Mar",
) -> dict[str, Any]:
    address: dict[str, Any] = {"city": city}
    if suburb:
        address["suburb"] = suburb
    return {
        "lat": lat_lon[0],
        "lon": lat_lon[1],
        "name": name,
        "display_name": f"{name}, {city}",
        "addresstype": addresstype,
        "osm_type": "way",
        "importance": 0.3,
        "address": address,
    }


def _nominatim_falso(respuestas: dict[str, list[dict[str, Any]]], pedidas: list[str]):
    """Responde según el `q` de la consulta; lo no previsto devuelve []."""

    def responder(request: httpx.Request) -> httpx.Response:
        consulta = request.url.params["q"]
        pedidas.append(consulta)
        return httpx.Response(200, json=respuestas.get(consulta, []))

    return responder


@pytest.fixture(autouse=True)
def sin_espera(monkeypatch):
    monkeypatch.setattr(nominatim, "_LIMITER", RateLimiter(0.0))


def _geocodificar(streets: dict[str, Any]):
    async def correr():
        async with httpx.AsyncClient() as client:
            return await geocode(client, streets, limiter=RateLimiter(0.0))

    return asyncio.run(correr())


# --- El sector sale del texto --------------------------------------------------


def test_las_dos_fuentes_nombran_el_mismo_sector() -> None:
    assert sector_en_texto(PURA_NOTICIA).nombre == "Miraflores Alto"
    assert sector_en_texto(TUIT).nombre == "Miraflores Alto"


@pytest.mark.parametrize(
    ("texto", "nombre", "comuna"),
    [
        # Mayúsculas: sin una minúscula que marque el final, el nombre se corta
        # en la comuna y en las palabras de `_CORTES`.
        (
            "INCENDIO EN SECTOR MIRAFLORES ALTO DE VIÑA DEL MAR BOMBEROS TRABAJAN",
            "MIRAFLORES ALTO",
            "Viña del Mar",
        ),
        # En cerro, villa y población la palabra es parte del nombre.
        ("Choque en el cerro Barón deja dos lesionados", "Cerro Barón", None),
        ("Derrumbe en Cerro Los Placeres, Valparaíso", "Cerro Los Placeres", None),
        ("Fuego en población Santa Julia", "Población Santa Julia", None),
        # El artículo con mayúscula es parte del nombre; en minúscula, no.
        ("Incendio en el sector El Olivar", "El Olivar", None),
        ("Choque en el sector de la Villa Dulce", "Villa Dulce", None),
    ],
)
def test_formas_de_nombrar_un_sector(texto: str, nombre: str, comuna: str | None) -> None:
    sector = sector_en_texto(texto)
    assert sector is not None
    assert sector.nombre == nombre
    assert sector.comuna == comuna


@pytest.mark.parametrize(
    "texto",
    [
        "Incendio en Villa Alemana",  # una comuna, no un sector
        "Bomberos del sector acudieron al llamado",  # minúscula: no es un nombre
        "Corte de luz en el sector alto de la ciudad",
        "Emergencia en el sector Poniente de Valparaíso",  # sólo una orientación
    ],
)
def test_lo_que_no_es_un_sector(texto: str) -> None:
    assert sector_en_texto(texto) is None


def test_la_clave_exige_comuna_y_distingue_alto_de_bajo() -> None:
    """Sin comuna no hay clave: unir por "Miraflores" a secas sería inventar la
    corroboración. Y Alto y Bajo son sectores distintos, a un kilómetro."""
    assert clave_sector("Miraflores Alto", None) is None
    assert clave_sector("Miraflores Alto", "Viña del Mar") == "vina del mar|miraflores alto"
    assert clave_sector("MIRAFLORES ALTO", "VIÑA DEL MAR") == "vina del mar|miraflores alto"
    assert clave_sector("Miraflores Alto", "Viña del Mar") != clave_sector(
        "Miraflores Bajo", "Viña del Mar"
    )


def test_la_compatibilidad_es_laxa_con_alto_y_estricta_con_el_nombre() -> None:
    """Es la comparación de la guarda contra `address.suburb`, que puede decir
    "Miraflores" donde la prensa dice "sector Miraflores Alto"."""
    assert sectores_compatibles("Miraflores Alto", "Miraflores")
    assert not sectores_compatibles("Miraflores Alto", "Recreo")
    assert not sectores_compatibles("Santa Inés", "Santa Julia")


def test_las_dos_fuentes_quedan_con_la_misma_clave() -> None:
    """Es lo que el motor compara para unirlas aunque sus puntos no se toquen."""
    nota = anotar_sector({"city": "Viña del Mar", "city_origen": "categoria"}, PURA_NOTICIA)
    tuit = anotar_sector({}, TUIT)

    assert nota["sector_clave"] == tuit["sector_clave"] == "vina del mar|miraflores alto"
    # En el tuit la comuna no la dio el extractor: sale del texto, y se anota.
    assert tuit["city"] == "Viña del Mar"
    assert tuit["city_origen"] == "texto"


def test_anotar_sector_no_corrige_la_comuna_del_extractor() -> None:
    """Llena un hueco, no pisa: si el extractor dijo Valparaíso, manda él."""
    anotado = anotar_sector({"street_1": "Av. Alemania", "city": "Valparaíso"}, TUIT)
    assert anotado["city"] == "Valparaíso"
    assert anotado["sector_clave"] == "valparaiso|miraflores alto"


def test_sin_sector_la_extraccion_no_cambia() -> None:
    original = {"street_1": "Av. España", "city": "Viña del Mar"}
    assert anotar_sector(original, "Choque en Av. España, Viña del Mar") == original


def test_comuna_en_texto_se_sigue_importando_desde_la_prensa() -> None:
    from app.collectors.news import local_news_worker

    assert local_news_worker.comuna_en_texto is comuna_en_texto


# --- El extractor deja de devolver artículos y comunas como calles -------------


def test_la_nota_de_pura_noticia_ya_no_tiene_calle() -> None:
    """Antes: `street_1 = "Viña del Mar:"`, que Nominatim resolvía a la ciudad."""
    assert extract_streets_heuristic(PURA_NOTICIA) is None


def test_el_tuit_encuentra_la_calle_detras_del_hasta() -> None:
    """Antes: `street_1 = "el"` (de "en el sector de…")."""
    streets = extract_streets_heuristic(TUIT)
    assert streets is not None
    assert streets["street_1"] == "calle once"
    assert streets["reference"] == "Miraflores Alto"


def test_la_comuna_de_una_preposicion_anterior_se_conserva() -> None:
    streets = extract_streets_heuristic(
        "Incendio en Viña del Mar: bomberos trabajan en calle Álvarez"
    )
    assert streets["street_1"] == "calle Álvarez"
    assert streets["city"] == "Viña del Mar"


def test_el_extremo_de_un_tramo_no_es_el_lugar() -> None:
    """"desde X hasta Y" es un tramo: el `hasta` no se usa como lugar."""
    tramo = "Tránsito suspendido desde Av. España hasta Av. Argentina"
    assert extract_streets_heuristic(tramo) is None


# --- Precisión y guarda de sector ---------------------------------------------


@pytest.mark.parametrize(
    ("payload", "esperada"),
    [
        ({"addresstype": "city"}, PRECISION_COMUNA),
        ({"addresstype": "suburb"}, PRECISION_SECTOR),
        ({"addresstype": "road"}, PRECISION_STREET),
        ({"place_rank": 16}, PRECISION_COMUNA),
        ({"place_rank": 20}, PRECISION_SECTOR),
        ({"place_rank": 26}, PRECISION_STREET),
        # Sin ninguno de los dos, como las respuestas que ya tenían los tests:
        # no se degrada por un campo que no se pidió.
        ({}, PRECISION_STREET),
    ],
)
def test_precision_de_un_resultado(payload: dict[str, Any], esperada: str) -> None:
    assert precision_de(payload) == esperada


def test_la_guarda_de_sector() -> None:
    en_recreo = {"address": {"city": "Viña del Mar", "suburb": "Recreo"}}
    en_miraflores = {"address": {"city": "Viña del Mar", "suburb": "Miraflores"}}
    sin_zona = {"address": {"city": "Viña del Mar", "road": "Calle Once"}}

    assert sector_matches(en_recreo, "Miraflores Alto") is False
    assert sector_matches(en_miraflores, "Miraflores Alto") is True
    # Lo que no se sabe pasa; se descarta lo que está demostradamente en otra parte.
    assert sector_matches(sin_zona, "Miraflores Alto") is True
    assert sector_matches(en_recreo, None) is True


def test_con_sector_la_primera_consulta_lo_nombra() -> None:
    streets = {"street_1": "calle once", "city": "Viña del Mar", "sector": "Miraflores Alto"}
    assert build_queries(streets)[0] == (
        "calle once, Miraflores Alto, Viña del Mar, Región de Valparaíso"
    )
    # Sin sector, las consultas de siempre.
    assert build_queries({"street_1": "calle once", "city": "Viña del Mar"})[0] == (
        "calle once, Viña del Mar, Región de Valparaíso"
    )
    # Si la "calle" ya es el sector, no se lo nombra dos veces.
    assert build_queries(
        {"street_1": "Miraflores Alto", "city": "Viña del Mar", "sector": "Miraflores Alto"}
    )[0] == "Miraflores Alto, Viña del Mar, Región de Valparaíso"


def test_el_sector_solo_exige_comuna() -> None:
    assert build_sector_query({"sector": "Miraflores Alto"}) is None
    assert build_sector_query({"sector": "Miraflores Alto", "city": "Viña del Mar"}) == (
        "Miraflores Alto, Viña del Mar, Región de Valparaíso"
    )


# --- El geocodificador --------------------------------------------------------

SECTOR_EN_OSM = {
    "Miraflores Alto, Viña del Mar, Región de Valparaíso": [
        _resultado(PUNTO_SECTOR, addresstype="suburb", name="Miraflores Alto"),
    ],
}


@respx.mock
def test_una_calle_de_otro_sector_se_descarta_y_se_usa_el_sector() -> None:
    """El tuit: la única "calle once" que OSM conoce está en Recreo."""
    en_recreo = _resultado(
        PUNTO_OTRO_SECTOR, addresstype="road", name="Calle Once", suburb="Recreo"
    )
    pedidas: list[str] = []
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {
                "calle once, Viña del Mar, Región de Valparaíso": [en_recreo],
                "calle once, Región de Valparaíso": [en_recreo],
                **SECTOR_EN_OSM,
            },
            pedidas,
        )
    )

    punto = _geocodificar(
        {"street_1": "calle once", "city": "Viña del Mar", "sector": "Miraflores Alto"}
    )

    assert punto is not None
    assert (punto.lat, punto.lon) == _como_float(PUNTO_SECTOR)
    assert punto.precision == PRECISION_SECTOR
    assert punto.sector == "Miraflores Alto"
    assert punto.omitted == ("street_1",)
    assert pedidas[-1] == "Miraflores Alto, Viña del Mar, Región de Valparaíso"


@respx.mock
def test_una_calle_dentro_del_sector_gana_al_sector() -> None:
    en_miraflores = _resultado(
        PUNTO_CALLE_EN_SECTOR, addresstype="road", name="Calle Once", suburb="Miraflores"
    )
    pedidas: list[str] = []
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {
                "calle once, Miraflores Alto, Viña del Mar, Región de Valparaíso": [
                    en_miraflores
                ],
                **SECTOR_EN_OSM,
            },
            pedidas,
        )
    )

    punto = _geocodificar(
        {"street_1": "calle once", "city": "Viña del Mar", "sector": "Miraflores Alto"}
    )

    assert (punto.lat, punto.lon) == _como_float(PUNTO_CALLE_EN_SECTOR)
    assert punto.precision == PRECISION_STREET
    assert punto.zonas == ("Miraflores",)
    assert len(pedidas) == 1, "resolvió a la primera: no se gasta el limitador"


@respx.mock
def test_la_ciudad_entera_no_es_una_calle() -> None:
    """La nota de Pura Noticia antes del arreglo del extractor: "Viña del Mar"
    como calle devolvía el nodo de la ciudad, y el mapa lo dibujaba en la plaza."""
    ciudad = _resultado(PUNTO_CIUDAD, addresstype="city", name="Viña del Mar")
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {
                "Viña del Mar, Viña del Mar, Región de Valparaíso": [ciudad],
                "Viña del Mar, Región de Valparaíso": [ciudad],
            },
            [],
        )
    )

    assert _geocodificar({"street_1": "Viña del Mar", "city": "Viña del Mar"}) is None


@respx.mock
def test_sin_calle_se_geocodifica_el_sector_con_una_sola_peticion() -> None:
    pedidas: list[str] = []
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(dict(SECTOR_EN_OSM), pedidas)
    )

    punto = _geocodificar({"city": "Viña del Mar", "sector": "Miraflores Alto"})

    assert (punto.lat, punto.lon) == _como_float(PUNTO_SECTOR)
    assert punto.precision == PRECISION_SECTOR
    assert pedidas == ["Miraflores Alto, Viña del Mar, Región de Valparaíso"]


@respx.mock
def test_una_avenida_que_se_llama_como_el_sector_no_es_el_sector() -> None:
    avenida = _resultado(PUNTO_OTRO_SECTOR, addresstype="road", name="Miraflores Alto")
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {"Miraflores Alto, Viña del Mar, Región de Valparaíso": [avenida]}, []
        )
    )

    assert _geocodificar({"city": "Viña del Mar", "sector": "Miraflores Alto"}) is None


@respx.mock
def test_un_sector_homonimo_de_otra_comuna_se_descarta() -> None:
    otra_comuna = _resultado(
        PUNTO_OTRO_SECTOR, addresstype="suburb", name="Miraflores Alto", city="Quilpué"
    )
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {"Miraflores Alto, Viña del Mar, Región de Valparaíso": [otra_comuna]}, []
        )
    )

    assert _geocodificar({"city": "Viña del Mar", "sector": "Miraflores Alto"}) is None


# --- Las dos fuentes, de punta a punta ----------------------------------------


@respx.mock
def test_la_nota_y_el_tuit_caen_en_el_mismo_punto() -> None:
    """El caso del 2026-09-03 completo, con los textos reales y el extractor de
    reglas (sin Gemini): las dos señales terminan en el mismo punto y con la
    misma clave de sector, que es lo que el motor necesita para unirlas."""
    from app.collectors.news.local_news_worker import geocode_noticia
    from app.collectors.social.instagram_apify_worker import geocode_text

    en_recreo = _resultado(
        PUNTO_OTRO_SECTOR, addresstype="road", name="Calle Once", suburb="Recreo"
    )
    respx.get(url__startswith=NOMINATIM_URL).mock(
        side_effect=_nominatim_falso(
            {
                "calle once, Viña del Mar, Región de Valparaíso": [en_recreo],
                "calle once, Región de Valparaíso": [en_recreo],
                **SECTOR_EN_OSM,
            },
            [],
        )
    )

    async def correr():
        async with httpx.AsyncClient() as client:
            nota = await geocode_noticia(
                PURA_NOTICIA, comuna_hint="Viña del Mar", geo_client=client
            )
            tuit = await geocode_text(TUIT, geo_client=client)
            return nota, tuit

    (calles_nota, punto_nota), (calles_tuit, punto_tuit) = asyncio.run(correr())

    assert punto_nota is not None and punto_tuit is not None
    assert (punto_nota.lat, punto_nota.lon) == (punto_tuit.lat, punto_tuit.lon)
    assert calles_nota["sector_clave"] == calles_tuit["sector_clave"]
    # Lo que el tuit sí decía de más queda escrito, aunque el punto no lo use.
    assert calles_tuit["street_1"] == "calle once"
    assert punto_tuit.omitted == ("street_1", "reference")
