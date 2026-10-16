"""El índice de cruces: cómo se arma (script) y cómo se elige y lee el release."""

from __future__ import annotations

import csv
import gzip
import io

import pytest

from app.collectors.traffic import gemini
from app.services import osm_cruces_service as svc
from scripts import osm_cruces as script

# --- El script --------------------------------------------------------------------


def test_nombres_de_una_via_sin_repetidos():
    tags = {"name": "Avenida Uno Norte", "alt_name": "1 Norte;Uno Norte", "ref": "F-30-E"}
    assert script.nombres_de(tags) == ("Avenida Uno Norte", "F-30-E")


def test_los_cruces_salen_de_los_nodos_compartidos():
    nombres = [("Arlegui",), ("Traslaviña",), ("Arlegui",), ("Quillota", "Ruta 60")]
    # Nodo 1: Arlegui con Traslaviña. Nodo 2: Arlegui sigue en otro tramo (la
    # misma calle no es un cruce). Nodo 3: fuera de toda comuna.
    vias_de_nodo = {1: [0, 1], 2: [0, 2], 3: [1, 3], 4: [0]}
    posicion = {1: (-33.0245, -71.553), 2: (-33.0246, -71.552), 3: (-40.0, -73.0), 4: (-33.0, -71.5)}

    def comunas_de(lon, lat):
        return ["vina del mar"] if lat > -34 else []

    filas = list(script.cruces(nombres, vias_de_nodo, posicion, comunas_de))
    assert filas == [
        (1, -33.0245, -71.553, "Arlegui", "Traslaviña", "arlegui", "traslavina", "vina del mar")
    ]


def test_los_alias_de_ambas_vias_dan_todas_las_combinaciones():
    nombres = [("Avenida Uno Norte",), ("Quillota", "Ruta 60")]
    filas = list(script.cruces(nombres, {7: [0, 1]}, {7: (-33.0, -71.55)}, lambda *_: ["vina del mar"]))
    assert {(f[3], f[4]) for f in filas} == {
        ("Avenida Uno Norte", "Quillota"),
        ("Avenida Uno Norte", "Ruta 60"),
    }


# --- El release ------------------------------------------------------------------


def test_se_elige_el_release_mas_nuevo_con_el_archivo():
    releases = [
        {"tag_name": "v1.0", "assets": [{"name": "osm_cruces.csv.gz", "browser_download_url": "x"}]},
        {
            "tag_name": "osm-cruces-2026-10-03-0717",
            "body": "Índice…\n\ndatos_de: 2026-10-02T20:21:02Z\nfilas: 1",
            "assets": [{"name": "osm_cruces.csv.gz", "browser_download_url": "https://a/oct"}],
        },
        {
            "tag_name": "osm-cruces-2026-11-03-0717",
            "draft": True,
            "assets": [{"name": "osm_cruces.csv.gz", "browser_download_url": "https://a/nov"}],
        },
        {"tag_name": "osm-cruces-2026-09-03-0717", "assets": [{"name": "otro.txt"}]},
    ]
    assert svc.ultimo_release(releases) == (
        "osm-cruces-2026-10-03-0717",
        "https://a/oct",
        "2026-10-02T20:21:02Z",
    )
    assert svc.ultimo_release([]) is None
    assert svc.ultimo_release({"message": "rate limited"}) is None


def comprimir(filas: list[list[str]], encabezado=svc.COLUMNAS) -> bytes:
    texto = io.StringIO()
    escritor = csv.writer(texto)
    escritor.writerow(encabezado)
    escritor.writerows(filas)
    return gzip.compress(texto.getvalue().encode("utf-8"))


def test_las_filas_se_leen_con_sus_comunas():
    datos = comprimir(
        [["1", "-33.0245", "-71.553", "Arlegui", "Traslaviña", "arlegui", "traslavina", "valparaiso|vina del mar"]]
    )
    assert list(svc.iterar_filas(datos)) == [
        (1, -33.0245, -71.553, "Arlegui", "Traslaviña", "arlegui", "traslavina", ["valparaiso", "vina del mar"])
    ]


@pytest.mark.parametrize(
    "datos",
    [
        b"no es gzip",
        comprimir([], encabezado=("nodo", "lat")),
        comprimir([["1", "-33", "-71", "A", "B", "a", "b"]]),
        comprimir([["x", "-33", "-71", "A", "B", "a", "b", "c"]]),
        gzip.compress(b"nodo,lat")[:-6],
    ],
)
def test_un_archivo_roto_se_rechaza(datos):
    with pytest.raises(svc.IndiceInvalidoError):
        list(svc.iterar_filas(datos))


# --- Tildes que Gemini devuelve como caracteres de control -------------------------


def test_las_tildes_dañadas_se_recuperan_del_aviso():
    aviso = "151 * NAVÍO SAN MARTÍN / CARLOS WOOSTER * CLAVE 6-1"
    assert gemini.reparar_controles("NAV\x10O SAN MART\x10N", aviso) == "NAVÍO SAN MARTÍN"
    assert gemini.reparar_controles("CARLOS WOOSTER", aviso) == "CARLOS WOOSTER"
    assert gemini.reparar_controles("NAV\x10O", "otro texto") == "NAVO"
    assert gemini.reparar_controles(None, aviso) is None


@pytest.mark.parametrize(
    ("valor", "aviso", "esperado"),
    [
        ("AVENIDA G3MEZ CARRE1O", "Clave 4-1 AVENIDA GÓMEZ CARREÑO / PACIFICO U-21", "AVENIDA GÓMEZ CARREÑO"),
        ("VICU1A MACKENNA", "Clave 5-1 VICUÑA MACKENNA / ANDRES BELLO U-63", "VICUÑA MACKENNA"),
        ("MXICO", "91 * MÉXICO / W. PORVENIR * CLAVE 6-1", "MÉXICO"),
        ("\x19LVAREZ", "Clave 2-1 ÁLVAREZ / VON SCHROEDERS U-12", "ÁLVAREZ"),
        # Lo que está bien no se toca, aunque cambie mayúsculas o tildes.
        ("TRASLAVINA", "Clave 4-3 ARLEGUI / TRASLAVIÑA U-12", "TRASLAVINA"),
        ("Las Araucarias", "📍 Las Araucarias Altura, altura 1400 · Sector 505", "Las Araucarias"),
        # Lo que el modelo agregó y no se parece a nada del aviso, tampoco.
        ("Viña del Mar", "Clave 4-3 ARLEGUI / TRASLAVIÑA U-12", "Viña del Mar"),
    ],
)
def test_los_nombres_cambiados_se_recuperan_del_aviso(valor, aviso, esperado):
    assert gemini.reparar_controles(valor, aviso) == esperado


def test_el_despacho_decodificado_sale_con_las_tildes_del_aviso():
    aviso = "151 * NAVÍO SAN MARTÍN / CARLOS WOOSTER * CLAVE 6-1"
    crudo = (
        '{"clave": "6-1", "significado": "Incendio", "street_1": "NAV\\u0010O SAN MART\\u0010N",'
        ' "street_2": "CARLOS WOOSTER", "city": null, "resumen": ""}'
    )
    decoded = gemini.parse_dispatch_response(crudo, source_handle="@CGI_CBV", original=aviso)
    assert decoded is not None and decoded["street_1"] == "NAVÍO SAN MARTÍN"
