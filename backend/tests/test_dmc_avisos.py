"""Avisos meteorológicos de la DMC (`datos_AAA.json`).

`DATOS_AAA` es una **captura real** del 16-10-2026 a las 18:10 (hora de la
DMC), recortada a cinco de sus doce avisos y con los contadores ajustados a
ese recorte:

* A561/2026 — tormentas eléctricas, por **regiones** (`04,05,05m,…`).
* A562/2026 — precipitaciones, por **zonas** (`05_Litoral,…,05m_…`), con tabla.
* A564/2026 — viento, por zonas, con tabla de varias regiones.
* A552-1/2026 — altas temperaturas en Magallanes: actualización, fuera.
* A559/2026 — viento en Juan Fernández (`jf`): de la región, pero fuera por
  decisión del proyecto.

El reloj se fija a las 18:30 de Chile de ese día.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.collectors.dmc import aaa_parser, avisos_worker
from app.collectors.dmc.aaa_parser import (
    DMC_KEY,
    alcance_en_region,
    parse_aviso,
    parse_emision,
    parse_vigencia,
    tabla_de_region,
    texto_limpio,
)
from app.collectors.dmc.avisos_worker import DmcAvisosCollector
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EVENT_TO_INCIDENT_TYPE,
    EventSource,
    EventType,
)
from app.models.event import RawEvent
from app.services.meteo_warning_service import item_de_aviso

CHILE = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 10, 16, 18, 30, tzinfo=CHILE).astimezone(UTC)

DATOS_AAA = r"""{
 "Avisos": 5,
 "Alertas": 0,
 "Alarmas": 0,
 "fecha_actualizacion": "2026-10-16 18:10",
 "AAA": [
  {
   "id": "5858",
   "tipo": "Aviso",
   "codigoMeteo": "A552-1/2026",
   "emision": "Viernes 16 de octubre del 2026 a las 18:05 hrs.",
   "fenomeno": "Altas Temperaturas",
   "icono": "wi-thermometer",
   "condicionSinoptica": "Altas presiones",
   "desde": "la tarde del viernes 16 de octubre del 2026",
   "hasta": "la tarde del s&aacute;bado 17 de octubre del 2026",
   "observacion": "",
   "tipoZonaAfecta": "zonas",
   "dataZonaAfecta": "12_Pampa_Patagonica_Norte,12_Pampa_Patagonica_Sur",
   "textoZonaAfecta": "Magallanes (Pampa Patagonica Norte, Pampa Patagonica Sur)",
   "tablaTitulo": "Valores estimados de temperatura en grados Celsius (&deg;C)",
   "tablaHTML": "<table class='tabla1CSS'><colgroup><col span='1' style='background-color: #99ceff;'></colgroup><tr><th colspan='3'>Regi&oacute;n: Magallanes</th></tr><tr><th>Zona</th><th>viernes 16</th><th>s&aacute;bado 17</th></tr><tr><td>Pampa Patagonica Norte</td><td>19-21</td><td>19-21</td></tr><tr><td>Pampa Patagonica Sur</td><td>19-21</td><td>19-21</td></tr></table>",
   "titulo": "Altas Temperaturas en zonas de la regi&oacute;n de Magallanes"
  },
  {
   "id": "5855",
   "tipo": "Aviso",
   "codigoMeteo": "A564/2026",
   "emision": "Viernes 16 de octubre del 2026 a las 12:33 hrs.",
   "fenomeno": "Viento",
   "icono": "wi-strong-wind",
   "condicionSinoptica": "Sistema frontal",
   "desde": "la tarde del martes 20 de octubre del 2026",
   "hasta": "la noche del mi&eacute;rcoles 21 de octubre del 2026",
   "observacion": "",
   "tipoZonaAfecta": "zonas",
   "dataZonaAfecta": "04_Litoral,04_Cordillera_Costa,04_Precordillera,04_Cordillera,05_Litoral,05_Cordillera_Costa,05_Precordillera,05_Cordillera,05m_Cordillera,06_Cordillera",
   "textoZonaAfecta": "Coquimbo (Litoral, Cordillera Costa, Precordillera y valles precordilleranos, Cordillera)<br>Valpara&iacute;so (Litoral, Cordillera Costa, Precordillera y valles precordilleranos, Cordillera)<br>Metropolitana (Cordillera)<br>O&#039;Higgins (Cordillera)",
   "tablaTitulo": "Intensidad estimada del Viento en Km/h",
   "tablaHTML": "<table class='tabla1CSS'><colgroup><col span='1' style='background-color: #99ceff;'></colgroup><tr><th colspan='3'>Regi&oacute;n: Coquimbo</th></tr><tr><th>Zona</th><th>martes 20</th><th>mi&eacute;rcoles 21</th></tr><tr><td>Litoral</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Cordillera Costa</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Precordillera y valles precordilleranos</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Cordillera</td><td>40-60 RACHAS 80</td><td>40-60 RACHAS 80</td></tr><tr><th colspan='3'>Regi&oacute;n: Valpara&iacute;so</th></tr><tr><th>Zona</th><th>martes 20</th><th>mi&eacute;rcoles 21</th></tr><tr><td>Litoral</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Cordillera Costa</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Precordillera y valles precordilleranos</td><td>25-40 RACHAS 50</td><td>25-40 RACHAS 50</td></tr><tr><td>Cordillera</td><td>25-40 RACHAS 60</td><td>25-40 RACHAS 60</td></tr><tr><th colspan='3'>Regi&oacute;n: Metropolitana</th></tr><tr><th>Zona</th><th>martes 20</th><th>mi&eacute;rcoles 21</th></tr><tr><td>Cordillera</td><td>25-40 RACHAS 60</td><td>25-40 RACHAS 60</td></tr><tr><th colspan='3'>Regi&oacute;n: O&#039;Higgins</th></tr><tr><th>Zona</th><th>martes 20</th><th>mi&eacute;rcoles 21</th></tr><tr><td>Cordillera</td><td>25-40 RACHAS 60</td><td>25-40 RACHAS 60</td></tr></table>",
   "titulo": "Viento Normal a Moderado en zonas desde la regi&oacute;n de Coquimbo a la regi&oacute;n de O&#039;Higgins"
  },
  {
   "id": "5854",
   "tipo": "Aviso",
   "codigoMeteo": "A562/2026",
   "emision": "Viernes 16 de octubre del 2026 a las 12:17 hrs.",
   "fenomeno": "Precipitaciones",
   "icono": "wi-showers",
   "condicionSinoptica": "Sistema frontal",
   "desde": "la ma&ntilde;ana del martes 20 de octubre del 2026",
   "hasta": "la ma&ntilde;ana del mi&eacute;rcoles 21 de octubre del 2026",
   "observacion": "",
   "tipoZonaAfecta": "zonas",
   "dataZonaAfecta": "04_Litoral,04_Cordillera_Costa,04_Precordillera,05_Litoral,05_Cordillera_Costa,05_Precordillera,05m_Cordillera_Costa,05m_Valle_Longitudinal,05m_Precordillera,06_Litoral,06_Cordillera_Costa,06_Valle_Longitudinal,06_Precordillera,07_Litoral,07_Cordillera_Costa",
   "textoZonaAfecta": "Coquimbo (Litoral, Cordillera Costa, Precordillera y valles precordilleranos)<br>Valpara&iacute;so (Litoral, Cordillera Costa, Precordillera y valles precordilleranos)<br>Metropolitana (Cordillera Costa, Valle, Precordillera)<br>O&#039;Higgins (Litoral, Cordillera Costa, Valle, Precordillera)<br>Maule (Litoral, Cordillera Costa)",
   "tablaTitulo": "Montos estimados de precipitaciones en mil&iacute;metros (mm)",
   "tablaHTML": "<table class='tabla1CSS'><colgroup><col span='1' style='background-color: #99ceff;'></colgroup><tr><th colspan='2'>Regi&oacute;n: Coquimbo</th></tr><tr><th>Zona</th><th>martes 20</th></tr><tr><td>Litoral</td><td>10-20</td></tr><tr><td>Cordillera Costa</td><td>15-25</td></tr><tr><td>Precordillera y valles precordilleranos</td><td>20-30</td></tr><tr><th colspan='2'>Regi&oacute;n: Valpara&iacute;so</th></tr><tr><th>Zona</th><th>martes 20</th></tr><tr><td>Litoral</td><td>25-35</td></tr><tr><td>Cordillera Costa</td><td>25-35</td></tr><tr><td>Precordillera y valles precordilleranos</td><td>20-30</td></tr><tr><th colspan='2'>Regi&oacute;n: Metropolitana</th></tr><tr><th>Zona</th><th>martes 20</th></tr><tr><td>Cordillera Costa</td><td>20-30</td></tr><tr><td>Valle</td><td>15-25</td></tr><tr><td>Precordillera</td><td>20-30</td></tr><tr><th colspan='2'>Regi&oacute;n: O&#039;Higgins</th></tr><tr><th>Zona</th><th>martes 20</th></tr><tr><td>Litoral</td><td>20-30</td></tr><tr><td>Cordillera Costa</td><td>20-30</td></tr><tr><td>Valle</td><td>15-25</td></tr><tr><td>Precordillera</td><td>25-35</td></tr><tr><th colspan='2'>Regi&oacute;n: Maule</th></tr><tr><th>Zona</th><th>martes 20</th></tr><tr><td>Litoral</td><td>25-35</td></tr><tr><td>Cordillera Costa</td><td>20-30</td></tr></table>",
   "titulo": "Precipitaciones Normales a Moderadas en zonas desde la regi&oacute;n de Coquimbo a la regi&oacute;n del Maule"
  },
  {
   "id": "5852",
   "tipo": "Aviso",
   "codigoMeteo": "A561/2026",
   "emision": "Viernes 16 de octubre del 2026 a las 12:10 hrs.",
   "fenomeno": "Tormentas El&eacute;ctricas",
   "icono": "wi-lightning",
   "condicionSinoptica": "Sistema frontal",
   "desde": "la tarde del lunes 19 de octubre del 2026",
   "hasta": "la noche del martes 20 de octubre del 2026",
   "observacion": "Probabilidad de granizos y fuertes rachas de vientos locales",
   "tipoZonaAfecta": "regiones",
   "dataZonaAfecta": "04,05,05m,06,07,08a,08b",
   "textoZonaAfecta": "Coquimbo, Valpara&iacute;so, Metropolitana, O&#039;Higgins, Maule, &Ntilde;uble y Biob&iacute;o",
   "tablaTitulo": "",
   "tablaHTML": "",
   "titulo": "Probables Tormentas El&eacute;ctricas desde la regi&oacute;n de Coquimbo a la regi&oacute;n del Biob&iacute;o"
  },
  {
   "id": "5850",
   "tipo": "Aviso",
   "codigoMeteo": "A559/2026",
   "emision": "Viernes 16 de octubre del 2026 a las 12:06 hrs.",
   "fenomeno": "Tormentas El&eacute;ctricas",
   "icono": "wi-lightning",
   "condicionSinoptica": "Sistema frontal",
   "desde": "la ma&ntilde;ana del lunes 19 de octubre del 2026",
   "hasta": "la tarde del martes 20 de octubre del 2026",
   "observacion": "Probabilidad de precipitaciones fuertes en un corto per&iacute;odo de tiempo",
   "tipoZonaAfecta": "regiones",
   "dataZonaAfecta": "jf",
   "textoZonaAfecta": "Archipielago Juan Fern&aacute;ndez",
   "tablaTitulo": "",
   "tablaHTML": "",
   "titulo": "Probables Tormentas El&eacute;ctricas en Archipielago Juan Fern&aacute;ndez"
  }
 ]
}"""


# --- Ayudas ------------------------------------------------------------------


def datos() -> dict:
    return json.loads(DATOS_AAA)


def registro(codigo: str) -> dict:
    return next(r for r in datos()["AAA"] if r["codigoMeteo"] == codigo)


def collector() -> DmcAvisosCollector:
    """Instancia sin `__init__`: no toca sesión ni base de datos."""
    instancia = DmcAvisosCollector.__new__(DmcAvisosCollector)
    instancia.url = settings.DMC_AAA_URL
    return instancia


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(avisos_worker, "_ahora", lambda: AHORA)
    return AHORA


def mock_aaa(cuerpo: str | dict, status: int = 200) -> respx.Route:
    texto = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo, ensure_ascii=False)
    return respx.get(settings.DMC_AAA_URL).mock(
        return_value=httpx.Response(
            status, text=texto, headers={"content-type": "application/json"}
        )
    )


def fila(evento) -> RawEvent:
    return RawEvent(
        **{k: v for k, v in evento.to_orm_kwargs().items() if hasattr(RawEvent, k)}
    )


# --- Texto y fechas ------------------------------------------------------------


def test_entidades_html_se_decodifican():
    assert texto_limpio("Tormentas El&eacute;ctricas") == "Tormentas Eléctricas"
    assert texto_limpio("O&#039;Higgins") == "O'Higgins"
    assert texto_limpio("  ") is None


def test_emision_en_hora_chilena():
    instante = parse_emision("Viernes 16 de octubre del 2026 a las 12:10 hrs.")
    assert instante == datetime(2026, 10, 16, 12, 10, tzinfo=CHILE).astimezone(UTC)


@pytest.mark.parametrize(
    ("texto", "extremo", "esperado"),
    [
        ("la tarde del lunes 19 de octubre del 2026", "desde", (19, 12)),
        ("la noche del martes 20 de octubre del 2026", "hasta", (21, 6)),
        ("la ma&ntilde;ana del martes 20 de octubre del 2026", "desde", (20, 6)),
        ("la ma&ntilde;ana del mi&eacute;rcoles 21 de octubre del 2026", "hasta", (21, 12)),
        ("el lunes 19 de octubre del 2026", "desde", (19, 0)),
        ("el lunes 19 de octubre del 2026", "hasta", (20, 0)),
    ],
)
def test_vigencia_por_tramo_del_dia(texto, extremo, esperado):
    momento = parse_vigencia(texto, extremo=extremo)
    local = momento.instante.astimezone(CHILE)
    assert (local.day, local.hour) == esperado
    assert "&" not in momento.texto


def test_vigencia_ilegible_conserva_el_texto():
    momento = parse_vigencia("hasta nuevo aviso", extremo="hasta")
    assert momento.instante is None
    assert momento.texto == "hasta nuevo aviso"


# --- Alcance -------------------------------------------------------------------


def test_por_regiones_toca_toda_la_region():
    alcance = alcance_en_region("regiones", "04,05,05m,06,07,08a,08b")
    assert alcance is not None and alcance.tipo == "region" and alcance.zonas == ()


def test_la_metropolitana_no_es_valparaiso():
    """`05m` empieza con `05`: compararlo por prefijo metería avisos de Santiago."""
    assert alcance_en_region("regiones", "05m,06") is None
    assert alcance_en_region("zonas", "05m_Cordillera,05m_Valle_Longitudinal") is None


def test_zonas_de_la_region_ordenadas_de_costa_a_cordillera():
    alcance = alcance_en_region(
        "zonas", "05_Cordillera,05m_Cordillera,05_Litoral,04_Litoral,05_Cordillera_Costa"
    )
    assert alcance is not None
    assert alcance.zonas == ("Litoral", "Cordillera_Costa", "Cordillera")


def test_las_cuatro_zonas_son_toda_la_region():
    alcance = alcance_en_region(
        "zonas", "05_Litoral,05_Cordillera_Costa,05_Precordillera,05_Cordillera,05m_Cordillera"
    )
    assert alcance is not None and alcance.tipo == "region"


def test_viento_real_en_las_cuatro_zonas_es_toda_la_region():
    aviso = parse_aviso(registro("A564/2026"))
    assert aviso is not None and aviso.alcance.tipo == "region"
    assert aviso.tabla is not None


def test_juan_fernandez_queda_fuera():
    assert parse_aviso(registro("A559/2026")) is None


def test_otra_region_queda_fuera():
    assert parse_aviso(registro("A552-1/2026")) is None


# --- Tabla -----------------------------------------------------------------------


def test_tabla_solo_de_valparaiso():
    original = registro("A564/2026")
    tabla = tabla_de_region(original["tablaHTML"], original["tablaTitulo"])
    assert tabla is not None
    assert tabla.titulo == "Intensidad estimada del Viento en Km/h"
    assert tabla.columnas == ("martes 20", "miércoles 21")
    assert [f.zona for f in tabla.filas] == [
        "Litoral",
        "Cordillera Costa",
        "Precordillera y valles precordilleranos",
        "Cordillera",
    ]
    assert tabla.filas[-1].valores == ("25-40 RACHAS 60", "25-40 RACHAS 60")


def test_sin_tabla_o_sin_la_region():
    assert tabla_de_region("") is None
    assert tabla_de_region("<table><tr><th colspan='2'>Regi&oacute;n: Maule</th></tr>"
                           "<tr><td>Litoral</td><td>1</td></tr></table>") is None


# --- El registro ---------------------------------------------------------------------


def test_tormentas_electricas_reales():
    aviso = parse_aviso(registro("A561/2026"))
    assert aviso is not None
    assert aviso.tipo == "Aviso" and aviso.severidad == 1
    assert aviso.fenomeno == "Tormentas Eléctricas"
    assert aviso.alcance.tipo == "region"
    assert aviso.observacion == "Probabilidad de granizos y fuertes rachas de vientos locales"
    assert aviso.desde.instante.astimezone(CHILE).day == 19
    assert aviso.hasta.instante.astimezone(CHILE) == datetime(2026, 10, 21, 6, 0, tzinfo=CHILE)
    assert aviso.tabla is None


def test_precipitaciones_por_zona_con_tabla():
    aviso = parse_aviso(registro("A562/2026"))
    assert aviso is not None
    assert aviso.alcance.zonas == ("Litoral", "Cordillera_Costa", "Precordillera")
    assert aviso.tabla is not None
    assert aviso.tabla.filas[0].valores == ("25-35",)


def test_registro_sin_id_o_de_tipo_desconocido_no_se_entiende():
    sin_id = registro("A561/2026")
    sin_id.pop("id")
    with pytest.raises(ValueError):
        parse_aviso(sin_id)
    raro = registro("A561/2026")
    raro["tipo"] = "Boletín"
    with pytest.raises(ValueError):
        parse_aviso(raro)


def test_alerta_y_alarma_suben_la_severidad():
    alerta = registro("A561/2026")
    alerta["tipo"] = "Alerta"
    alarma = registro("A561/2026")
    alarma["tipo"] = "Alarma"
    assert parse_aviso(alerta).severidad == 2
    assert parse_aviso(alarma).severidad == 3


# --- Collector -----------------------------------------------------------------


@respx.mock
async def test_fetch_real_se_queda_con_lo_de_la_region(reloj):
    mock_aaa(DATOS_AAA)
    instancia = collector()
    avisos = await instancia.fetch()
    assert sorted(a.codigo for a in avisos) == ["A561/2026", "A562/2026", "A564/2026"]
    assert instancia.warnings == []


@respx.mock
async def test_sin_lista_aaa_falla(reloj):
    mock_aaa({"Avisos": 0, "datos": []})
    with pytest.raises(CollectorError, match="AAA"):
        await collector().fetch()


@respx.mock
async def test_contadores_que_no_calzan_avisan(reloj):
    cuerpo = datos()
    cuerpo["Avisos"] = 9
    mock_aaa(cuerpo)
    instancia = collector()
    avisos = await instancia.fetch()
    assert len(avisos) == 3
    assert any("contadores" in w for w in instancia.warnings)


@respx.mock
async def test_formato_cambiado_avisa_y_entra_lo_legible(reloj):
    cuerpo = datos()
    for r in cuerpo["AAA"]:
        r["zonas"] = r.pop("dataZonaAfecta")
    cuerpo["AAA"].append({"tipo": "Aviso"})
    cuerpo["Avisos"] += 1
    mock_aaa(cuerpo)
    instancia = collector()
    avisos = await instancia.fetch()
    assert avisos == []
    assert any("dataZonaAfecta" in w for w in instancia.warnings)
    assert any("ilegibles" in w for w in instancia.warnings)


def test_normalize_emite_weather_warning_fuera_del_motor(reloj):
    avisos = [parse_aviso(registro("A561/2026"))]
    eventos = collector().normalize(avisos)
    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.source == EventSource.DMC
    assert evento.type == EventType.WEATHER_WARNING
    assert evento.external_id == "dmc:aaa:5852"
    assert evento.lat is None and evento.lon is None
    assert evento.text.startswith("Aviso DMC A561/2026: Probables Tormentas Eléctricas")
    assert evento.timestamp == datetime(2026, 10, 16, 12, 10, tzinfo=CHILE).astimezone(UTC)
    bloque = evento.raw_data[DMC_KEY]
    assert bloque["visto_en"] == AHORA.isoformat()
    assert bloque["alcance"] == "region"
    assert "_source_record" in evento.raw_data
    assert EventType.WEATHER_WARNING not in CORRELATABLE_EVENT_TYPES
    assert EventType.WEATHER_WARNING not in EVENT_TO_INCIDENT_TYPE


def test_emision_futura_se_recorta_a_ahora(reloj):
    futuro = registro("A561/2026")
    futuro["emision"] = "Lunes 19 de octubre del 2026 a las 09:00 hrs."
    evento = collector().normalize([parse_aviso(futuro)])[0]
    assert evento.timestamp == AHORA


# --- Lectura del feed --------------------------------------------------------------


def test_item_del_feed_desde_la_fila(reloj):
    avisos = [parse_aviso(registro(c)) for c in ("A561/2026", "A562/2026")]
    eventos = collector().normalize(avisos)
    tormenta = item_de_aviso(fila(eventos[0]), ahora=AHORA)
    lluvia = item_de_aviso(fila(eventos[1]), ahora=AHORA)
    assert tormenta is not None and lluvia is not None
    assert tormenta.alcance == "region" and tormenta.zonas == []
    assert tormenta.en_curso is False
    assert tormenta.url.startswith("https://archivos.meteochile.gob.cl/")
    assert [z.nombre for z in lluvia.zonas] == [
        "Litoral",
        "Cordillera de la Costa",
        "Precordillera",
    ]
    assert lluvia.tabla is not None and lluvia.tabla.filas[0].zona == "Litoral"


def test_item_en_curso_cuando_ya_empezo(reloj):
    evento = collector().normalize([parse_aviso(registro("A561/2026"))])[0]
    despues = datetime(2026, 10, 19, 15, 0, tzinfo=CHILE).astimezone(UTC)
    item = item_de_aviso(fila(evento), ahora=despues)
    assert item is not None and item.en_curso is True


def test_fila_sin_bloque_dmc_se_omite():
    rota = RawEvent(
        source=EventSource.DMC,
        type=EventType.WEATHER_WARNING,
        timestamp=AHORA,
        text="x",
        raw_data={},
    )
    assert item_de_aviso(rota, ahora=AHORA) is None


def test_registro_conserva_el_original():
    original = registro("A562/2026")
    copia = copy.deepcopy(original)
    aviso = parse_aviso(original)
    assert dict(aviso.raw) == copia
    assert aaa_parser.build_external_id(aviso) == f"dmc:aaa:{copia['id']}"
