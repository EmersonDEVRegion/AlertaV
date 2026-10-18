"""Área afectada de los cortes, sus barrios de OSM y los programados de Chilquinta.

El caso real es la orden `1718636-1` de Chilquinta (Rodelillo y Santos Ossa,
Valparaíso, 17-10-2026, 1 912 clientes): 1 718 vértices guardados en
`fixtures/chilquinta_1718636.json`. Su visor dibuja la envolvente convexa
(2,44 km²); la mancha de AlertaV sigue a los clientes.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import zipfile
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
import respx

from app.collectors.power.base_worker import area_de_vertices
from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.chilquinta_worker import (
    JSONP_CALLBACK,
    ChilquintaCollector,
    programado_a_la_vista,
)
from app.collectors.power.kmz_parser import parse_kmz
from app.collectors.power.outage_parser import parse_outage
from app.collectors.sectores_osm import sectores
from app.core.areas import (
    MAX_VERTICES,
    contiene,
    mancha,
    multipoligono,
    poligonos_de,
    unir,
    vertices_de_segmentos,
)
from app.core.config import settings
from app.services.water_cut_service import area_de_sectores

CASO = json.loads(
    (Path(__file__).parent / "fixtures" / "chilquinta_1718636.json").read_text(encoding="utf-8")
)
VERTICES = [tuple(v) for v in CASO["vertices"]]
PIN = tuple(CASO["pin"])
CHILQUINTA_URL = "https://mapainterrupciones.chilquinta.cl/dt/results_006.js"
VINA = (-33.0245, -71.5518)


def _km2(area) -> float:
    """Área aproximada en km² (plano local), restando agujeros."""
    total = 0.0
    for poligono in poligonos_de(area):
        for k, anillo in enumerate(poligono):
            lat0 = anillo[0][1]
            kx = 111_320 * math.cos(math.radians(lat0))
            ky = 110_574
            s = 0.5 * abs(
                sum(
                    (a[0] * kx) * (b[1] * ky) - (b[0] * kx) * (a[1] * ky)
                    for a, b in pairwise(anillo)
                )
            )
            total += -s if k else s
    return total / 1e6


def _vertices(area) -> int:
    return sum(len(anillo) for poligono in poligonos_de(area) for anillo in poligono)


# --- La mancha ----------------------------------------------------------------


class TestMancha:
    def test_la_orden_de_rodelillo_mide_la_mitad_que_la_envolvente(self) -> None:
        area = mancha(VERTICES)
        assert area is not None and area["type"] == "MultiPolygon"
        # La envolvente convexa del visor de Chilquinta mide 2,44 km².
        assert 1.0 < _km2(area) < 1.6
        assert _vertices(area) <= MAX_VERTICES
        # Casi todos los clientes quedan dentro (los bordes simplificados pueden
        # dejar alguno a pocos metros afuera).
        dentro = sum(contiene(area, lat, lon) for lat, lon in VERTICES[::20])
        assert dentro >= 0.95 * len(VERTICES[::20])

    def test_un_punto_es_un_circulo_del_radio(self) -> None:
        area = mancha([VINA])
        assert contiene(area, *VINA)
        assert not contiene(area, VINA[0] + 0.002, VINA[1])  # ~220 m al norte
        assert 0.007 < _km2(area) < 0.016  # π·60² ≈ 0,011 km²

    def test_dos_calles_lejanas_son_dos_manchas(self) -> None:
        calle_1 = [(-33.06 + 0.0002 * i, -71.57) for i in range(30)]
        calle_2 = [(-33.05, -71.55 + 0.0002 * j) for j in range(10)]
        area = mancha(calle_1 + calle_2)
        assert len(poligonos_de(area)) == 2
        assert not contiene(area, -33.055, -71.56)

    def test_un_anillo_de_clientes_deja_el_centro_afuera(self) -> None:
        kx = 111_320 * math.cos(math.radians(33))
        anillo = [
            (-33.0 + 500 * math.sin(a) / 110_574, -71.5 + 500 * math.cos(a) / kx)
            for a in (k * 2 * math.pi / 400 for k in range(400))
        ]
        area = mancha(anillo)
        assert len(poligonos_de(area)[0]) == 2  # exterior y agujero
        assert not contiene(area, -33.0, -71.5)

    def test_sin_puntos_no_hay_area(self) -> None:
        assert mancha([]) is None
        assert area_de_vertices(()) is None

    def test_la_cache_devuelve_la_misma_area(self) -> None:
        assert area_de_vertices(tuple(VERTICES)) is area_de_vertices(tuple(VERTICES))


class TestPoligonos:
    def test_un_anillo_abierto_se_cierra_y_uno_degenerado_se_descarta(self) -> None:
        area = multipoligono([[[-71.6, -33.1], [-71.5, -33.1], [-71.5, -33.0]], [[1, 2], [1, 2]]])
        assert area == {
            "type": "MultiPolygon",
            "coordinates": [[[[-71.6, -33.1], [-71.5, -33.1], [-71.5, -33.0], [-71.6, -33.1]]]],
        }
        assert multipoligono([]) is None

    def test_simplificar_quita_vertices_sin_cambiar_la_forma(self) -> None:
        # Un cuadrado de ~1 km con un vértice cada ~20 m en cada lado.
        esquinas = [(-71.6, -33.1), (-71.59, -33.1), (-71.59, -33.09), (-71.6, -33.09)]
        anillo = []
        for k, (x1, y1) in enumerate(esquinas):
            x2, y2 = esquinas[(k + 1) % 4]
            anillo += [[x1 + (x2 - x1) * i / 50, y1 + (y2 - y1) * i / 50] for i in range(50)]
        anillo.append(anillo[0])
        area = multipoligono([anillo], tolerancia_m=5)
        assert _vertices(area) <= 6
        assert contiene(area, -33.095, -71.595)
        assert 0.8 < _km2(area) < 1.2

    def test_unir_junta_polygon_y_multipolygon(self) -> None:
        cuadrado = [[0, 0], [1, 0], [1, 1], [0, 0]]
        unido = unir(
            [
                {"type": "Polygon", "coordinates": [cuadrado]},
                {"type": "MultiPolygon", "coordinates": [[cuadrado], [cuadrado]]},
                None,
                {"type": "Point", "coordinates": [0, 0]},
            ]
        )
        assert unido is not None and len(unido["coordinates"]) == 3
        assert unir([None]) is None

    def test_los_vertices_de_chilquinta_se_leen_en_los_dos_niveles(self) -> None:
        segmentos = [
            [{"latitud_min": "-33.1", "longitud_min": "-71.6"}, {"latitud_min": "x"}],
            {"latitud_min": -33.2, "longitud_min": -71.7},
        ]
        assert vertices_de_segmentos(segmentos) == [(-33.1, -71.6), (-33.2, -71.7)]
        assert vertices_de_segmentos("nada") == []


# --- Barrios de OSM ------------------------------------------------------------


class TestSectores:
    def test_rodelillo_encabeza_los_barrios_de_la_orden(self) -> None:
        nombres = sectores(clientes=VERTICES, pin=PIN)
        assert nombres[0] == "Rodelillo"
        assert 1 < len(nombres) <= 5
        # El cerro vecino no entra por un poste suelto.
        assert "Cerro Larraín" not in nombres

    def test_con_solo_el_poligono_entran_los_lugares_de_adentro(self) -> None:
        nombres = sectores(area=mancha(VERTICES), pin=PIN)
        assert nombres and all(isinstance(n, str) for n in nombres)
        assert "Cerro Larraín" not in nombres

    def test_sin_area_ni_clientes_no_hay_nombres(self) -> None:
        assert sectores() == []
        assert sectores(area={"type": "Point", "coordinates": [0, 0]}) == []


# --- Programados de Chilquinta --------------------------------------------------


def _chile(texto: str) -> datetime:
    """Hora de pared chilena de octubre (UTC−3) → UTC."""
    return datetime.fromisoformat(texto + "-03:00").astimezone(UTC)


class TestProgramados:
    @pytest.mark.parametrize(
        ("ahora", "visible"),
        [
            ("2026-10-16T22:00:00", False),  # la víspera
            ("2026-10-17T05:59:00", False),  # de madrugada no se avisa
            ("2026-10-17T06:00:00", True),
            ("2026-10-17T13:59:00", True),
            ("2026-10-17T19:30:00", True),  # pasado el fin, si aún lo listan
            ("2026-10-17T20:01:00", False),  # más de 6 h después del fin
        ],
    )
    def test_se_ve_el_dia_en_que_termina(self, ahora: str, visible: bool) -> None:
        assert programado_a_la_vista(_chile("2026-10-17T14:00:00"), _chile(ahora)) is visible

    def test_uno_que_termina_de_madrugada_se_ve_desde_medianoche(self) -> None:
        fin = _chile("2026-10-17T04:00:00")
        assert programado_a_la_vista(fin, _chile("2026-10-17T01:00:00"))
        assert not programado_a_la_vista(None, _chile("2026-10-17T01:00:00"))

    def test_entra_marcado_una_vez_por_comuna_y_el_de_otro_dia_no(self) -> None:
        base = {"orden": "10212907", "tipo": "dx", "etr": "17-10-2026 14:00:00", "latitud": ""}
        ordenes = [
            {**base, "comuna": "QUILPUE", "cant_clientes": "14"},
            {**base, "comuna": "VILLA ALEMANA", "cant_clientes": "18"},
            {**base, "orden": "10213336", "comuna": "OLMUE", "etr": "25-10-2026 15:00:00"},
            {"orden": "1718636-1", "tipo": "inter", "comuna": "VALPARAISO_ORIENTE"},
        ]
        instancia = ChilquintaCollector.__new__(ChilquintaCollector)
        salida = instancia.solo_programados_de_hoy(ordenes, ahora=_chile("2026-10-17T09:00:00"))

        assert [o.get("id") for o in salida] == [
            "10212907:QUILPUE",
            "10212907:VILLA ALEMANA",
            None,
        ]
        assert [o.get("_programado") for o in salida] == [True, True, None]

    def test_el_programado_viaja_hasta_la_senal(self) -> None:
        instancia = ChilquintaCollector.__new__(ChilquintaCollector)
        instancia.bbox = settings.region_bbox
        registro = {
            "orden": "10212907",
            "tipo": "dx",
            "etr": "17-10-2026 14:00:00",
            "comuna": "QUILPUE",
            "cant_clientes": "14",
            "latitud": VINA[0],
            "longitud": VINA[1],
            "id": "10212907:QUILPUE",
            "_programado": True,
        }
        corte = parse_outage(registro)
        assert corte is not None and corte.programado
        [evento] = instancia.normalize([corte])
        assert evento.external_id == "chilquinta:10212907:QUILPUE"
        assert evento.raw_data["programado"] is True
        assert evento.raw_data["_outage"]["programado"] is True
        assert evento.text.startswith("Corte programado (CHILQUINTA)")
        assert "termina 17-10 14:00 h" in evento.text


@respx.mock
def test_la_orden_de_chilquinta_sale_con_su_area_y_sus_barrios() -> None:
    orden = {
        "orden": "1718636-1",
        "etr": "17-10-2026 11:00:41",
        "latitud": str(PIN[0]),
        "longitud": str(PIN[1]),
        "cant_clientes": "1912",
        "tipo": "inter",
        "comuna": "VALPARAISO_ORIENTE",
        "segmentos": [
            [{"latitud_min": str(lat), "longitud_min": str(lon)} for lat, lon in VERTICES]
        ],
    }
    cuerpo = {"headers": {}, "exception": None, "original": {"ordenes": [orden]}}
    respx.get(url__startswith=CHILQUINTA_URL).mock(
        return_value=httpx.Response(200, text=f"{JSONP_CALLBACK}({json.dumps(cuerpo)})")
    )
    instancia = ChilquintaCollector.__new__(ChilquintaCollector)
    instancia.bbox = settings.region_bbox
    instancia.url = CHILQUINTA_URL

    [evento] = instancia.normalize(asyncio.run(instancia.fetch()))

    # El pin sigue siendo el de la empresa; el área es la de los clientes.
    assert (evento.lat, evento.lon) == pytest.approx(PIN)
    assert evento.raw_data["area"]["type"] == "MultiPolygon"
    assert 1.0 < _km2(evento.raw_data["area"]) < 1.6
    assert evento.raw_data["sectores"][0] == "Rodelillo"
    assert evento.raw_data["programado"] is False


# --- CGE ----------------------------------------------------------------------

KML_CGE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>
<Placemark>
  <styleUrl>#dext.confirmed-outage</styleUrl><name/>
  <description><![CDATA[Clientes afectados: 17<br/>{restauracion}]]></description>
  <MultiGeometry>
    <Point><coordinates>-71.5518,-33.0245</coordinates></Point>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -71.553,-33.026 -71.550,-33.026 -71.550,-33.023 -71.553,-33.023 -71.553,-33.026
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </MultiGeometry>
</Placemark>
</Folder></Document></kml>""".replace(
    "{restauracion}", "Restauraci&#243;n estimada: 17/10/2026 21:00 hrs"
)


def _kmz(kml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr("tmp/dext/afectaciones_cge.kml", kml.encode())
    return buffer.getvalue()


class TestCge:
    def test_el_punto_publicado_manda_y_el_poligono_es_el_area(self) -> None:
        [registro] = parse_kmz(_kmz(KML_CGE))
        # El punto que puso CGE, no el promedio con los vértices del sector.
        assert (registro["lat"], registro["lon"]) == (-33.0245, -71.5518)
        assert registro["_kml_style"] == "#dext.confirmed-outage"

        corte = parse_outage(registro)
        assert corte is not None
        assert corte.area is not None and contiene(corte.area, -33.0245, -71.5518)
        assert corte.affected_clients == 17

    def test_la_restauracion_estimada_se_lee(self) -> None:
        corte = parse_outage(parse_kmz(_kmz(KML_CGE))[0])
        assert corte is not None
        # 21:00 de Chile en octubre (UTC−3) = 00:00 UTC del 18.
        assert corte.restoration_at == datetime(2026, 10, 18, 0, 0, tzinfo=UTC)

    @respx.mock
    def test_la_senal_de_cge_lleva_el_area(self) -> None:
        respx.route(url__startswith="https://cge.test").mock(
            return_value=httpx.Response(200, content=_kmz(KML_CGE))
        )
        instancia = CgeCollector.__new__(CgeCollector)
        instancia.bbox = settings.region_bbox
        instancia.url = "https://cge.test/mapa_cge.kmz"

        [evento] = instancia.normalize(asyncio.run(instancia.fetch()))
        assert evento.raw_data["area"]["type"] == "MultiPolygon"
        assert evento.raw_data["restoration_at"] == "2026-10-18T00:00:00+00:00"
        assert isinstance(evento.raw_data["sectores"], list)


# --- Esval --------------------------------------------------------------------


def test_los_sectores_de_esval_salen_como_un_multipolygon() -> None:
    anillo = [[-71.53, -32.98], [-71.52, -32.98], [-71.52, -32.97], [-71.53, -32.98]]
    area = area_de_sectores([{"id": "8692", "anillo": anillo}, {"id": "x"}, "basura"])
    assert area is not None and len(area["coordinates"]) == 1
    assert contiene(area, -32.979, -71.522)
    assert area_de_sectores(None) is None
