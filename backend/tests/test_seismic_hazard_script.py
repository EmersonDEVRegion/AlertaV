"""Extractor one-off del mapa de amenaza sísmica.

El script corre a mano una vez cada varios años, así que no tiene la red de
seguridad que sí tienen los collectors: nadie va a mirar `collector_runs` para
descubrir que algo salió mal. Lo que produzca se queda en el repositorio y se
sirve en producción tal cual.

De ahí el sesgo de estos tests: casi todos verifican que el script **falle**
cuando el dato no es el esperado, en vez de escribir un artefacto plausible y
equivocado. Un GeoJSON con geometría perfecta y propiedades vacías se ve en el
mapa como «acá la amenaza es baja», que es indistinguible de la verdad.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from scripts.fetch_seismic_hazard import (
    CSN_HAZARD_CSV_URL,
    BoundingBox,
    ExtractionStats,
    GridPoint,
    HazardExtractionError,
    build_feature_collection,
    build_row_parser,
    cell_polygon,
    extract,
    infer_grid_step,
    parse_row,
    resolve_bbox,
    split_row,
    validate_header,
    write_output,
)

#: Cabecera literal del CSV del CSN, verificada contra el archivo real.
CABECERA = (
    "lon,lat,PGA-0.1,SA(0.3)-0.1,SA(1.0)-0.1,SA(3.0)-0.1,"
    "PGA-0.02,SA(0.3)-0.02,SA(1.0)-0.02,SA(3.0)-0.02"
)

#: Paso real de la grilla, medido sobre filas consecutivas del archivo del CSN.
PASO = 0.04497

#: Caja de la V Región, la misma de `settings.REGION_*`.
VALPO = BoundingBox(west=-72.0, south=-33.8, east=-69.8, north=-32.0)


def fila(lon: float, lat: float, pga: float = 0.34) -> str:
    return f"{lon:.5f},{lat:.5f},{pga},0.75,0.30,0.07,0.68,1.50,0.60,0.14"


def csv_de_chile() -> str:
    """Grilla que va de Arica a Aysén. La V Región es una fracción."""
    filas = [CABECERA]
    lon = -73.5
    while lon <= -69.5:
        lat = -40.0
        while lat <= -17.0:
            filas.append(fila(lon, lat))
            lat += PASO
        lon += PASO
    return "\n".join(filas) + "\n"


# --- Validación de la cabecera -----------------------------------------------


def test_reconoce_la_cabecera_real_del_csn():
    columnas = validate_header(split_row(CABECERA))

    assert columnas["PGA-0.1"] == "pga_475"
    assert columnas["PGA-0.02"] == "pga_2475"
    assert len(columnas) == 8


def test_sin_coordenadas_falla_fuerte():
    with pytest.raises(HazardExtractionError, match="coordenadas"):
        validate_header(["PGA-0.1", "SA(0.3)-0.1"])


def test_sin_la_variable_principal_falla_fuerte():
    """Un artefacto sin PGA se vería como una capa gris uniforme.

    Es decir: indistinguible de «acá la amenaza es baja». Mejor no generarlo.
    """
    with pytest.raises(HazardExtractionError, match="PGA-0.1"):
        validate_header(["lon", "lat", "otra_cosa"])


def test_una_columna_secundaria_ausente_no_es_fatal():
    """Se exporta con lo que haya: SA(3.0) es útil pero no imprescindible."""
    recortada = "lon,lat,PGA-0.1,PGA-0.02"
    columnas = validate_header(split_row(recortada))

    assert "PGA-0.1" in columnas
    assert "SA(3.0)-0.1" not in columnas


def test_un_csv_vacio_falla():
    with pytest.raises(HazardExtractionError, match="vacío"):
        build_row_parser("")


# --- Filtro espacial: el requisito central -----------------------------------


def parsear(linea: str, bbox: BoundingBox = VALPO) -> GridPoint | None:
    columnas, indices, lon_idx, lat_idx = build_row_parser(CABECERA)
    return parse_row(
        split_row(linea),
        bbox=bbox,
        columnas=columnas,
        indices=indices,
        lon_idx=lon_idx,
        lat_idx=lat_idx,
    )


@pytest.mark.parametrize(
    ("lon", "lat", "lugar"),
    [
        (-71.62, -33.05, "Valparaíso"),
        (-71.55, -33.02, "Viña del Mar"),
        (-70.60, -32.80, "Los Andes"),
        (-71.61, -32.39, "Pichidangui"),
    ],
)
def test_conserva_lo_que_cae_en_la_region(lon, lat, lugar):
    assert parsear(fila(lon, lat)) is not None, lugar


@pytest.mark.parametrize(
    ("lon", "lat", "lugar"),
    [
        (-71.23, -29.85, "La Serena"),
        (-70.74, -34.17, "Rancagua"),
        (-73.05, -36.83, "Concepción"),
        (-70.91, -53.15, "Punta Arenas"),
        (-70.40, -23.65, "Antofagasta"),
    ],
)
def test_descarta_lo_que_cae_fuera(lon, lat, lugar):
    assert parsear(fila(lon, lat)) is None, lugar


def test_la_caja_regional_es_un_rectangulo_y_no_el_poligono_de_la_region():
    """Santiago cae DENTRO de `REGION_*`, y conviene saberlo.

    La caja es un rectángulo que envuelve la V Región, así que muerde el borde
    poniente de la Metropolitana. Para esta capa no hace daño —la amenaza sísmica
    de la cuenca de Santiago es contexto legítimo para quien mira Valparaíso— y
    recortarla más sería perder el litoral. Pero está fijado acá para que nadie
    lo descubra pensando que el filtro está roto.

    Si algún día hiciera falta el recorte exacto, la pieza que falta es la capa
    de polígonos comunales, la misma que espera el Paso B del motor.
    """
    assert parsear(fila(-70.65, -33.45)) is not None, "Santiago entra en la caja"
    assert parsear(fila(-70.74, -34.17)) is None, "Rancagua ya no"


@pytest.mark.parametrize(
    ("lon", "lat"),
    [
        (-72.0, -33.8),  # esquina suroeste
        (-69.8, -32.0),  # esquina noreste
        (-72.0, -32.0),
        (-69.8, -33.8),
    ],
)
def test_los_bordes_de_la_caja_entran(lon, lat):
    """`contains` es inclusivo en los cuatro lados.

    Excluirlos dejaría una franja de una celda sin pintar en todo el perímetro,
    que en un mapa se ve como un borde deshilachado y no como un criterio.
    """
    assert parsear(fila(lon, lat)) is not None


def test_una_fila_ilegible_se_distingue_de_una_fuera_de_region():
    """Dos cosas muy distintas que un solo contador confundiría.

    «Fuera de la caja» es el filtro funcionando. «Ilegible» es una señal de que
    el formato cambió, y esconderla tras la estadística de recorte haría que un
    CSV corrupto pasara por un recorte agresivo.
    """
    assert parsear(fila(-71.23, -29.85)) is None  # La Serena, fuera: devuelve None

    with pytest.raises(ValueError):
        parsear("no,son,numeros,0.3,0.7,0.3,0.07,0.6,1.5,0.6")


# --- Geometría de las celdas -------------------------------------------------


def test_infiere_el_paso_de_la_grilla_desde_los_datos():
    """Un paso hardcodeado que no coincida deja franjas o solapes.

    Ninguna de las dos cosas parece un error al mirar el mapa: parecen el mapa.
    """
    latitudes = [-33.0 + i * PASO for i in range(20)]
    assert infer_grid_step(latitudes, fallback=0.045) == pytest.approx(PASO, abs=1e-6)


def test_el_paso_usa_la_mediana_y_no_la_media():
    """Un hueco en la cobertura no debe engordar todas las celdas.

    La grilla del CSN está definida en una proyección métrica y convertida a
    geográficas, así que no es perfectamente regular. Un solo salto grande
    arrastraría la media.
    """
    con_hueco = [0.0, 0.045, 0.090, 0.135, 5.0]  # un salto enorme al final
    assert infer_grid_step(con_hueco, fallback=0.045) == pytest.approx(0.045)


def test_el_paso_cae_al_valor_por_defecto_si_no_hay_datos():
    assert infer_grid_step([], fallback=0.045) == 0.045
    assert infer_grid_step([-33.0], fallback=0.045) == 0.045


def test_la_celda_esta_centrada_en_el_nodo():
    punto = GridPoint(lon=-71.5, lat=-33.0)
    anillo = cell_polygon(punto, PASO, PASO)[0]

    lons = [vertice[0] for vertice in anillo]
    lats = [vertice[1] for vertice in anillo]

    assert min(lons) == pytest.approx(-71.5 - PASO / 2, abs=1e-5)
    assert max(lons) == pytest.approx(-71.5 + PASO / 2, abs=1e-5)
    assert min(lats) == pytest.approx(-33.0 - PASO / 2, abs=1e-5)
    assert max(lats) == pytest.approx(-33.0 + PASO / 2, abs=1e-5)


def test_el_anillo_se_cierra_como_exige_la_rfc_7946():
    """Sin repetir el primer vértice, varios renderizadores no calculan interior."""
    anillo = cell_polygon(GridPoint(lon=-71.5, lat=-33.0), PASO, PASO)[0]

    assert len(anillo) == 5
    assert anillo[0] == anillo[-1]


# --- El artefacto ------------------------------------------------------------


def coleccion_de_prueba() -> dict:
    puntos = [
        GridPoint(lon=-71.5, lat=-33.0, values={"pga_475": 0.34, "pga_2475": 0.68}),
        GridPoint(lon=-71.5 + PASO, lat=-33.0, values={"pga_475": 0.35}),
        GridPoint(lon=-71.5, lat=-33.0 + PASO, values={"pga_475": 0.33}),
    ]
    return build_feature_collection(puntos, bbox=VALPO, stats=ExtractionStats())


def test_produce_un_featurecollection_valido():
    coleccion = coleccion_de_prueba()

    assert coleccion["type"] == "FeatureCollection"
    assert len(coleccion["features"]) == 3
    for feature in coleccion["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Polygon"


def test_cada_celda_conserva_su_centro_y_sus_valores():
    feature = coleccion_de_prueba()["features"][0]

    assert feature["properties"]["lon"] == pytest.approx(-71.5)
    assert feature["properties"]["lat"] == pytest.approx(-33.0)
    assert feature["properties"]["pga_475"] == pytest.approx(0.34)


def test_el_artefacto_lleva_su_propia_procedencia():
    """Va a vivir años en el repositorio sin que nadie lo toque.

    Quien tenga que decidir si sigue vigente no debería reconstruirlo leyendo
    git: el archivo dice de qué modelo salió, de qué URL y cuándo.
    """
    metadata = coleccion_de_prueba()["metadata"]

    assert metadata["model"] == "MASCSN26"
    assert metadata["source_url"] == CSN_HAZARD_CSV_URL
    assert metadata["bbox"] == list(VALPO.as_tuple())
    assert "generated_at" in metadata
    assert "estatica" in metadata["note"].lower()


def test_una_caja_sin_nodos_falla_en_vez_de_escribir_un_archivo_vacio():
    """Un GeoJSON de cero features es un artefacto que miente por omisión."""
    with pytest.raises(HazardExtractionError, match="ningún nodo"):
        build_feature_collection([], bbox=VALPO, stats=ExtractionStats())


def test_la_escritura_es_atomica(tmp_path: Path):
    """Un fallo a mitad no puede dejar un JSON truncado que el frontend parsee."""
    destino = tmp_path / "geo" / "amenaza.json"
    write_output(coleccion_de_prueba(), destino)

    assert destino.exists()
    assert not list(destino.parent.glob("*.tmp")), "no debe quedar el temporal"
    assert json.loads(destino.read_text(encoding="utf-8"))["type"] == "FeatureCollection"


def test_reescribir_deja_el_archivo_consistente(tmp_path: Path):
    destino = tmp_path / "amenaza.json"
    write_output(coleccion_de_prueba(), destino)
    primero = destino.read_text(encoding="utf-8")

    write_output(coleccion_de_prueba(), destino)
    segundo = json.loads(destino.read_text(encoding="utf-8"))

    assert primero  # el primero era válido
    assert len(segundo["features"]) == 3


# --- Flujo completo ----------------------------------------------------------


@respx.mock
def test_extrae_la_region_de_una_grilla_de_todo_chile():
    """La prueba que resume el objetivo del script."""
    respx.get(CSN_HAZARD_CSV_URL).mock(
        return_value=httpx.Response(200, text=csv_de_chile())
    )

    coleccion, stats = asyncio.run(
        extract(url=CSN_HAZARD_CSV_URL, bbox=VALPO, timeout=30, retries=1)
    )

    assert stats.rows_read > 40_000, "el CSV de origen cubre el país"
    assert stats.points_kept < 2_500, "la V Región es una fracción"
    assert stats.rows_discarded > stats.points_kept * 10
    assert stats.rows_malformed == 0
    assert len(coleccion["features"]) == stats.points_kept

    # Ninguna celda puede haber escapado al recorte.
    for feature in coleccion["features"]:
        assert VALPO.contains(
            feature["properties"]["lat"], feature["properties"]["lon"]
        )


@respx.mock
def test_una_fila_truncada_no_pierde_el_archivo():
    """Le pasó a la inspección inicial de esta fuente: el fetch se cortó a media fila."""
    cuerpo = "\n".join(
        [
            CABECERA,
            fila(-71.5, -33.0),
            "-71.55,-33.02,0.3421567,0.486",  # cortada
            fila(-71.6, -33.1),
            "-71.65,",  # cortada al principio
        ]
    )
    respx.get(CSN_HAZARD_CSV_URL).mock(return_value=httpx.Response(200, text=cuerpo))

    coleccion, stats = asyncio.run(
        extract(url=CSN_HAZARD_CSV_URL, bbox=VALPO, timeout=30, retries=1)
    )

    # La fila con coordenadas legibles pero valores truncados SÍ entra: sus
    # coordenadas son válidas y las variables que faltan se omiten.
    assert stats.points_kept == 3
    assert stats.rows_malformed == 1
    assert len(coleccion["features"]) == 3


@respx.mock
def test_un_404_no_se_reintenta_y_dice_qué_hacer():
    """Un 404 significa que el CSN movió el archivo. Reintentar sólo retrasa."""
    ruta = respx.get(CSN_HAZARD_CSV_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(HazardExtractionError, match="CSN_HAZARD_CSV_URL"):
        asyncio.run(extract(url=CSN_HAZARD_CSV_URL, bbox=VALPO, timeout=5, retries=3))

    assert ruta.call_count == 1


@respx.mock
def test_un_5xx_se_reintenta_y_puede_recuperarse():
    """Los servidores universitarios fallan de forma transitoria."""
    respx.get(CSN_HAZARD_CSV_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text=f"{CABECERA}\n{fila(-71.5, -33.0)}"),
        ]
    )

    coleccion, _ = asyncio.run(
        extract(url=CSN_HAZARD_CSV_URL, bbox=VALPO, timeout=5, retries=2)
    )
    assert len(coleccion["features"]) == 1


@respx.mock
def test_la_red_agotada_falla_de_forma_legible():
    respx.get(CSN_HAZARD_CSV_URL).mock(side_effect=httpx.ConnectError("sin DNS"))

    with pytest.raises(HazardExtractionError, match="intentos"):
        asyncio.run(extract(url=CSN_HAZARD_CSV_URL, bbox=VALPO, timeout=1, retries=1))


# --- Aislamiento del pipeline en vivo ----------------------------------------


def test_el_script_no_importa_nada_del_pipeline_de_recoleccion():
    """La restricción explícita: no toca el CRON de cinco minutos.

    Se comprueba sobre el código fuente y no sobre `sys.modules`, porque un
    import indirecto a través de otro test contaminaría la medición.
    """
    import ast

    fuente = Path("scripts/fetch_seismic_hazard.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)

    # Se inspeccionan los `import` reales y no el texto: el docstring del módulo
    # menciona `app.collectors` justamente para explicar que NO lo importa, y una
    # búsqueda por substring lo confundiría con un import.
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)

    prohibidos = [
        modulo
        for modulo in importados
        if modulo.startswith(("app.collectors", "app.services", "app.repositories"))
    ]
    assert prohibidos == [], f"el script importa el pipeline: {prohibidos}"

    # Lo único que sí comparte con la aplicación es la caja del territorio.
    assert {m for m in importados if m.startswith("app.")} <= {"app.core.config"}


def test_el_script_no_esta_registrado_como_collector():
    from app.collectors.registry import available_collectors

    disponibles = available_collectors()
    assert not any("amenaza" in nombre or "hazard" in nombre for nombre in disponibles)


def test_la_caja_por_defecto_sale_de_la_configuracion_de_la_app():
    """Una segunda definición del territorio derivaría de la primera."""
    import argparse

    from app.core.config import settings

    bbox = resolve_bbox(argparse.Namespace(bbox=None))

    assert bbox.west == settings.REGION_WEST
    assert bbox.south == settings.REGION_SOUTH
    assert bbox.east == settings.REGION_EAST
    assert bbox.north == settings.REGION_NORTH


def test_la_caja_se_puede_sobrescribir_por_cli():
    import argparse

    bbox = resolve_bbox(argparse.Namespace(bbox=[-75.0, -40.0, -68.0, -30.0]))
    assert bbox.as_tuple() == (-75.0, -40.0, -68.0, -30.0)


# --- El montaje estático que sirve el artefacto ------------------------------


def test_la_app_sirve_el_directorio_estatico():
    from app.main import app

    montajes = [ruta.path for ruta in app.routes if getattr(ruta, "path", "") == "/static"]
    assert montajes == ["/static"]
