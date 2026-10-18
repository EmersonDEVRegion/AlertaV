"""El estado del tránsito del MTT: subtipos, tramos y lugares (§ tránsito).

Los textos son los tuits reales de @TTIValparaiso del 15 al 17-10-2026, tal
como los guardó Apify, recortados.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import UTC, datetime

import pytest

from app.collectors import hitos, tramos
from app.collectors.traffic import gemini
from app.collectors.traffic import transito_x as tx
from app.collectors.vocabulary import subtipo_mtt
from app.models.enums import EventType
from app.services import osm_cruces_service as svc

AHORA = datetime(2026, 10, 16, 21, 10, tzinfo=UTC)

TUITS: list[tuple[str, str | None]] = [
    (
        "Ahora (17:21) congestión en Av. Jorge Montt desde Rotonda San Martín hasta Escuela "
        "de Operaciones #ViñadelMar, en dirección a Reñaca.",
        "congestion",
    ),
    (
        "Ahora (16:11) Tránsito normal y fluido en Av Errázuriz, #Valparaíso, en toda su "
        "extensión y tanto en dirección a Playa Ancha, como en dirección a Nudo Barón.",
        "normalidad",
    ),
    (
        "Ahora (13:02) Tránsito lento en Av Alessandri, #ViñaDelMar, desde Enlace Santa Inés "
        "hasta Gómez Carreño, en dirección a Rotonda Santa Julia. Tránsito normal en dirección "
        "a Viña.",
        "lento",
    ),
    (
        "Precaución (11:03) Semáforo apagado en intersección de Av Pedro Montt con calle "
        "Morris, comuna de #Valparaíso. Transitar con precaución.",
        "semaforo",
    ),
    (
        "Ahora (08:49) Congestión en salida de Troncal Sur hacia Rotonda Limache y en calle "
        "Limache en dirección al centro de #ViñaDelMar.",
        "congestion",
    ),
    (
        "Actualización (08:39) Incidente superado. Vehículos siniestrados son retirados del "
        "lugar y todas las pistas de Av Uno Norte y calle Quillota, #ViñaDelMar, quedan "
        "habilitadas.",
        "normalidad",
    ),
    ("Ahora (12:25) Semáforos apagados en Simón Bolívar con calle Limache. #ViñadelMar", "semaforo"),
    (
        "Ahora (9:19) Semáforos apagados en Freire con Baden Powell y Acceso a Centro "
        "Comercial. Precaución. #Quilpué",
        "semaforo",
    ),
    ("Ahora (18:06) Congestión en Viana desde Sucre hasta Von Schroeders. #ViñadelMar", "congestion"),
    (
        "Ahora (17:06) Bus en panne en pista central de Av. España, altura Caleta Abarca, en "
        "dirección a #ViñadelMar",
        "pista_ocupada",
    ),
    (
        "Ahora (15:25) Tránsito lento en Avenida España, sector Yolanda, #Valparaíso, en "
        "dirección a Viña del Mar. Considere un leve aumento en los tiempos de viaje.",
        "lento",
    ),
    (
        "Precaución (13:07) Trabajos en calle Limache con El Álamo, #ViñaDelMar, mantienen "
        "ocupada pista derecha en dirección a la costa.",
        "pista_ocupada",
    ),
    (
        "Actualización (13:00) Grupo de personas se mantiene en calle Blanco, #Valparaíso, "
        "entre Ross y Sotomayor. Se mantiene desvío de tránsito en Ross hacia Av Errázuriz.",
        "marcha",
    ),
    (
        "Actualización (12:59) Habilitado tránsito vehicular en Avenida Libertad, "
        "#ViñaDelMar, en ambos sentidos. Tránsito normal y fluido.",
        "normalidad",
    ),
    (
        "Ahora (12:29) Grupo de personas avanza por calle Blanco en dirección a Plaza "
        "Sotomayor, #Valparaíso. Se mantiene congestión en Av Brasil/Blanco desde Pudeto.",
        "marcha",
    ),
    (
        "Ahora (08:05) Tránsito normal y fluido en Avenida Uno Norte, #ViñaDelMar, tanto en "
        "dirección a la costa como hacia el interior.",
        "normalidad",
    ),
    (
        "Ahora (07:40) Trolebus opera normalmente desde las 07:30 horas con recorrido por "
        "Avenida Colón hasta Plaza Aduana, en el centro de la comuna de #Valparaíso.",
        None,
    ),
    (
        "¡BUENOS DÍAS! #BuenViernes 16 de octubre de 2026. Recomendamos revisar las "
        "condiciones técnicas y mecánicas de tu vehículo antes de salir.",
        None,
    ),
]


@pytest.mark.parametrize(("texto", "esperado"), TUITS)
def test_el_subtipo_de_cada_tuit_real(texto, esperado):
    assert subtipo_mtt(texto) == esperado


def test_el_taco_con_tránsito_normal_en_el_otro_sentido_es_taco():
    """El MTT agrega «tránsito normal» como contraste: no cierra nada."""
    assert subtipo_mtt(TUITS[2][0]) == "lento"


def test_separar_reparte_choques_estado_y_ruido():
    textos = [
        "Precaución (11:07) Siniestro vial en Avenida España al llegar a Reloj de Flores.",
        TUITS[8][0],
        TUITS[17][0],
    ]
    avisos = [
        tx.aviso_de_tuit(texto=t, identificador=str(i), publicado=AHORA, cuenta=None, url=None)
        for i, t in enumerate(textos)
    ]
    choques, condiciones, descartados = tx.separar([a for a in avisos if a])
    assert len(choques) == 1
    assert [sub for _, sub in condiciones] == ["congestion"]
    assert descartados == 1


@pytest.mark.parametrize(
    ("subtipo", "tipo", "prefijo"),
    [
        ("congestion", EventType.TRAFFIC_CONDITION, "mtt:transito:x:"),
        ("semaforo", EventType.TRAFFIC_CONDITION, "mtt:transito:x:"),
        ("normalidad", EventType.TRAFFIC_CONDITION, "mtt:transito:x:"),
        ("pista_ocupada", EventType.ROAD_CLOSURE, "mtt:closure:x:"),
        ("marcha", EventType.ROAD_CLOSURE, "mtt:closure:x:"),
    ],
)
def test_tipo_e_identificador_por_subtipo(subtipo, tipo, prefijo):
    aviso = tx.aviso_de_tuit(texto="x", identificador="9", publicado=AHORA, cuenta=None, url=None)
    assert tx.tipo_de(subtipo) is tipo
    assert tx.external_id_condicion(aviso, subtipo) == f"{prefijo}9"


def test_el_evento_lleva_el_tramo_y_su_linea():
    aviso = tx.aviso_de_tuit(
        texto=TUITS[8][0], identificador="77", publicado=AHORA, cuenta="TTIValparaiso", url="u"
    )
    extraccion = {
        "via": "Viana",
        "desde": "Sucre",
        "hasta": "Von Schroeders",
        "punto": None,
        "sentido": None,
        "ciudad": "ViñadelMar",
        "toda_la_via": False,
    }
    linea = {"type": "LineString", "coordinates": [[-71.552, -33.026], [-71.5615, -33.0248]]}
    ubicacion = tramos.Ubicacion(linea, -33.0254, -71.5567, tramos.TRAMO, 880.0)
    evento = tx.evento_condicion(aviso, "congestion", extraccion, ubicacion, [], ahora=AHORA)
    assert evento.type is EventType.TRAFFIC_CONDITION
    assert (evento.lat, evento.lon) == (-33.0254, -71.5567)
    mtt = evento.raw_data["_mtt"]
    assert mtt["tipo"] == "congestion"
    assert (mtt["via"], mtt["desde"], mtt["hasta"]) == ("Viana", "Sucre", "Von Schroeders")
    assert mtt["linea"] == linea
    assert mtt["ubicacion"] == {"metodo": "tramo", "largo_m": 880}
    assert evento.raw_data["comuna"] == "Viña del Mar"


def test_sin_ubicacion_el_aviso_entra_igual_y_anota_lo_que_falto():
    aviso = tx.aviso_de_tuit(
        texto=TUITS[0][0], identificador="5", publicado=AHORA, cuenta=None, url=None
    )
    evento = tx.evento_condicion(
        aviso, "congestion", {"via": "Av. Jorge Montt"}, None, ["Escuela de Operaciones"],
        ahora=AHORA,
    )
    assert evento.lat is None and evento.lon is None
    assert evento.raw_data["_mtt"]["linea"] is None
    assert evento.raw_data["_mtt"]["sin_ubicar"] == ["Escuela de Operaciones"]


# --- Lo que extrae Gemini -------------------------------------------------------


def test_el_tramo_con_desde_y_hasta():
    raw = json.dumps(
        {"via": "Viana", "desde": "Sucre", "hasta": "Von Schroeders", "punto": None,
         "sentido": None, "ciudad": "ViñadelMar", "toda_la_via": False}
    )
    assert gemini.parse_tramo(raw) == {
        "via": "Viana", "desde": "Sucre", "hasta": "Von Schroeders", "punto": None,
        "sentido": None, "ciudad": "ViñadelMar", "toda_la_via": False,
    }


def test_el_punto_pierde_la_preposicion():
    raw = json.dumps(
        {"via": "Av. España", "desde": None, "hasta": None, "punto": "altura Caleta Abarca",
         "sentido": "ViñadelMar", "ciudad": None, "toda_la_via": None}
    )
    tramo = gemini.parse_tramo(raw)
    assert tramo is not None
    assert tramo["punto"] == "Caleta Abarca"
    assert tramo["toda_la_via"] is False


def test_un_solo_extremo_no_es_tramo_y_queda_como_punto():
    raw = json.dumps(
        {"via": "Av Brasil", "desde": "Pudeto", "hasta": None, "punto": None,
         "sentido": None, "ciudad": "Valparaíso", "toda_la_via": False}
    )
    tramo = gemini.parse_tramo(raw)
    assert tramo is not None
    assert (tramo["desde"], tramo["hasta"], tramo["punto"]) == (None, None, "Pudeto")


@pytest.mark.parametrize("raw", ["no es json", "[1, 2]", json.dumps({"via": None})])
def test_sin_via_no_hay_tramo(raw):
    assert gemini.parse_tramo(raw) is None


# --- Comunas y lugares ----------------------------------------------------------


@pytest.mark.parametrize(
    ("ciudad", "esperado"),
    [
        ("ViñadelMar", ["vina del mar"]),
        ("Viña del Mar", ["vina del mar"]),
        ("Valparaíso", ["valparaiso"]),
        ("Quilpué", ["quilpue"]),
        (None, list(tramos.GRAN_VALPARAISO)),
        ("Mordor", list(tramos.GRAN_VALPARAISO)),
    ],
)
def test_la_ciudad_del_aviso_se_lee_como_comuna(ciudad, esperado):
    assert tramos.comunas_de(ciudad) == esperado


def test_el_nombre_canonico_de_la_comuna():
    assert tramos.nombre_de_comuna(["vina del mar"]) == "Viña del Mar"
    assert tramos.nombre_de_comuna(list(tramos.GRAN_VALPARAISO)) is None


def test_los_lugares_del_nomenclator():
    assert hitos.buscar("Caleta Abarca", ["vina del mar"]) is not None
    assert hitos.buscar("sector Yolanda", ["valparaiso"]) is not None
    assert hitos.buscar("Yolanda", ["vina del mar"]) is None, "otra comuna"
    assert hitos.buscar("Escuela de Operaciones", ["vina del mar"]) is None


def test_cada_lugar_del_nomenclator_tiene_fuente_y_cae_en_la_region():
    datos = json.loads(hitos.ARCHIVO.read_text(encoding="utf-8"))
    for fila in datos["hitos"]:
        assert fila["fuente"]
        assert -34.0 < fila["lat"] < -32.0
        assert -72.0 < fila["lon"] < -70.0


# --- El tramo por las esquinas ----------------------------------------------------

#: Esquinas reales de Viana (índice de OSM, 17-10-2026), de este a oeste.
SUCRE = (-33.02607, -71.55201)
COUSINO = (-33.02599, -71.55359)
QUINTA = (-33.02590, -71.55428)
ETCHEVERS = (-33.02567, -71.55579)
VILLANELO = (-33.02545, -71.55722)
VON_SCHROEDERS = (-33.02482, -71.56155)
LIMACHE = (-33.02984, -71.53766)
LA_MARINA = (-33.02369, -71.56826)


def test_las_esquinas_entre_los_extremos_y_en_orden():
    esquinas = [VILLANELO, LIMACHE, COUSINO, LA_MARINA, ETCHEVERS, QUINTA]
    assert tramos.ordenar_por_tramo(SUCRE, VON_SCHROEDERS, esquinas) == [
        COUSINO,
        QUINTA,
        ETCHEVERS,
        VILLANELO,
    ]
    assert tramos.ordenar_por_tramo(VON_SCHROEDERS, SUCRE, esquinas) == [
        VILLANELO,
        ETCHEVERS,
        QUINTA,
        COUSINO,
    ]


def test_extremos_iguales_no_dan_tramo():
    assert tramos.ordenar_por_tramo(SUCRE, SUCRE, [COUSINO]) == []


# --- osm_vias: el script y la carga -------------------------------------------------


def test_las_vias_se_unen_por_nombre_y_comuna():
    pytest.importorskip("shapely")
    from scripts import osm_cruces as script

    nombres = [("Viana",), ("Viana",), ("Avenida Libertad", "Libertad")]
    trazos = [
        [(-71.552, -33.026), (-71.556, -33.0256)],
        [(-71.556, -33.0256), (-71.5615, -33.0248)],
        [(-71.551, -33.020), (-71.551, -33.010)],
    ]
    filas = list(script.vias(nombres, trazos, lambda lon, lat: ["vina del mar"]))
    viana = [f for f in filas if f[0] == "Viana"]
    assert len(viana) == 1, "las dos piezas son una sola vía"
    nombre, ultima, comuna, wkt = viana[0]
    assert (ultima, comuna) == ("viana", "vina del mar")
    assert wkt.startswith("MULTILINESTRING")
    assert {f[0] for f in filas} == {"Viana", "Avenida Libertad"}, (
        "«Avenida Libertad» y «Libertad» son la misma vía"
    )


def _gzip(filas: list[list[str]]) -> bytes:
    texto = io.StringIO()
    csv.writer(texto).writerows(filas)
    return gzip.compress(texto.getvalue().encode("utf-8"))


def test_las_filas_de_vias_se_leen():
    datos = _gzip(
        [
            list(svc.COLUMNAS_VIAS),
            ["Viana", "viana", "vina del mar", "MULTILINESTRING((-71.552 -33.026,-71.56 -33.025))"],
        ]
    )
    assert list(svc.iterar_vias(datos)) == [
        ("Viana", "viana", ["vina del mar"], "MULTILINESTRING((-71.552 -33.026,-71.56 -33.025))")
    ]


@pytest.mark.parametrize(
    "filas",
    [
        [["nombre", "otra"], ["a", "b"]],
        [list(svc.COLUMNAS_VIAS), ["Viana", "viana", "vina del mar", "POINT(1 2)"]],
    ],
)
def test_un_archivo_de_vias_roto_se_rechaza(filas):
    with pytest.raises(svc.IndiceInvalidoError):
        list(svc.iterar_vias(_gzip(filas)))
