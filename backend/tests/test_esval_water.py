"""Capa de cortes de agua (Esval): API + KML del visor, unidos por `sisda`.

Las fixtures son **capturas reales** del 2026-09-23, tomadas desde el navegador
porque el entorno de desarrollo no llega a los dos hosts de Esval:

* `API_CORTES`: `CortesActivos` a las 15:25, tal cual (5 cortes). Incluye uno de
  Aguas del Valle (IV Región) y dos programados que empiezan al día siguiente.
* `REGISTRO_SAN_ANTONIO`: un corte de emergencia de la captura de las 14:48,
  que a las 15:25 ya había terminado y no está en el KML.
* `KML_ZONAS`: `generaKmlZonasCorte.aspx?region=5` a las 15:25, recortado: cada
  anillo quedó con 4 vértices más el de cierre, un solo `<Style>`, y uno de los
  tres sectores de Viña fuera. Las etiquetas, el CDATA y los textos, intactos.

El reloj se fija a las 15:30 de Chile de ese día (`AHORA`), porque qué corte
"ya empezó" depende de la hora y las capturas tienen fecha.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors import registry
from app.collectors.power.outage_parser import records_or_raise
from app.collectors.water import esval_worker
from app.collectors.water.esval_parser import (
    CLAVES_ESPERADAS,
    ESVAL_KEY,
    CorteAgua,
    KmlZonasError,
    build_external_id,
    build_text,
    claves_ausentes,
    comuna_del_corte,
    parse_corte,
    parse_ficha,
    parse_zonas_kml,
    sin_duplicados,
    tipo_legible,
    unir,
    ventana_legible,
)
from app.collectors.water.esval_worker import WATER_CUT_CONFIDENCE, EsvalCollector
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EVENT_TO_INCIDENT_TYPE,
    SOURCE_BASE_CONFIDENCE,
    EventSource,
    EventType,
)
from app.services.correlation.confidence import RULES

#: 15:30 de Chile (UTC-3 en septiembre) del día de la captura.
AHORA = datetime(2026, 9, 23, 18, 30, tzinfo=UTC)

API_CORTES = """{"data":[{"localidad":"06","localidadNombre":"Viña del mar","localidadNombreNormalizado":"VINA DEL MAR","sector":null,"callesAfectadas":"LOS PENSAMIENTOS","fechaInicio":"23-09-2026","fechaFin":"23-09-2026","horaInicio":"11:00","horaTermino":"17:00","motivoCorte":"VIDA UTIL VENCIDA","sisda":"2916567","estaGeoeferenciado":true,"tipoCorte":"Corte emergencia","urlMapa":"http://tupuntodeagua.esval.cl/?sisda=2916567","otros":null,"empresaId":1,"_id":9439},{"localidad":"11","localidadNombre":"Quilpué","localidadNombreNormalizado":"QUILPUE","sector":"Doctor Salas esquina La Obra","callesAfectadas":"Del Arrayan, Del Boldo, Del Canelo, Del Cedrón, Del Laurel, Del Maqui, Del Nogal, Del Platero, Del Roble, Dario Poblete, Doctor Salas, Gabriel González Videla, Granados, La Obra, Las Acacias, Las Pataguas, Las Rosas, Los Copihues, Los Estanques, Los Guind","fechaInicio":"23-09-2026","fechaFin":"24-09-2026","horaInicio":"15:00","horaTermino":"02:00","motivoCorte":"Renovación de infraestructura","sisda":"2912217","estaGeoeferenciado":true,"tipoCorte":"Corte programado","urlMapa":"http://tupuntodeagua.esval.cl/?sisda=2912217","otros":null,"empresaId":1,"_id":9416},{"localidad":"79","localidadNombre":"Rinconada","localidadNombreNormalizado":"RINCONADA","sector":"Calle El caremn / Santo Domingo","callesAfectadas":"Calle El Carmen, 18 de septiembre.","fechaInicio":"24-09-2026","fechaFin":"24-09-2026","horaInicio":"15:00","horaTermino":"20:00","motivoCorte":"Renovación de Redes AP","sisda":"2914979","estaGeoeferenciado":true,"tipoCorte":"Corte programado","urlMapa":"http://tupuntodeagua.esval.cl/?sisda=2914979","otros":"Se adjuntan listado de servicios afectados, poligono del sector y aviso a clientes.","empresaId":1,"_id":9431},{"localidad":"81","localidadNombre":"San Antonio","localidadNombreNormalizado":"SAN ANTONIO","sector":"Blanco Encalada, Jose Arrieta y Pasaje Los Reyes, San Antonio  ","callesAfectadas":"Av. Doctor Luis Reuss entre Domingo Fernández Concha y Manuel Blanco Encalada-21 de mayo","fechaInicio":"24-09-2026","fechaFin":"24-09-2026","horaInicio":"15:00","horaTermino":"21:00","motivoCorte":"NUEVA CONEXION DE INFRAESTRUCTURA ","sisda":"2910226","estaGeoeferenciado":true,"tipoCorte":"Corte programado","urlMapa":"http://tupuntodeagua.esval.cl/?sisda=2910226","otros":null,"empresaId":1,"_id":9387},{"localidad":"05","localidadNombre":"Andacollo","localidadNombreNormalizado":"ANDACOLLO","sector":"El Curque","callesAfectadas":"Gonzalo Arancibia, Bartolomé Santander, Ignacio C. Pinto, Pje Antonio Claret, desde Bellavista hasta Parque el Oasis.","fechaInicio":"24-09-2026","fechaFin":"24-09-2026","horaInicio":"15:00","horaTermino":"20:00","motivoCorte":"Mantención de redes de agua potable","sisda":"6261154","estaGeoeferenciado":true,"tipoCorte":"Corte programado","urlMapa":"http://tupuntodeagua.aguasdelvalle.cl/?sisda=6261154","otros":null,"empresaId":2,"_id":9400}]}"""

REGISTRO_SAN_ANTONIO = {
    "localidad": "81",
    "localidadNombre": "San Antonio",
    "localidadNombreNormalizado": "SAN ANTONIO",
    "sector": None,
    "callesAfectadas": "AV CENTENARIO",
    "fechaInicio": "23-09-2026",
    "fechaFin": "23-09-2026",
    "horaInicio": "13:00",
    "horaTermino": "17:00",
    "motivoCorte": "MATRIZ FILTRANDO",
    "sisda": "2916550",
    "estaGeoeferenciado": True,
    "tipoCorte": "Corte emergencia",
    "urlMapa": "http://tupuntodeagua.esval.cl/?sisda=2916550",
    "otros": "SIN RESPUESTA",
    "empresaId": 1,
    "_id": 9440,
}

KML_ZONAS = """<?xml version='1.0' encoding='UTF-8'?>
<kml xmlns='http://earth.google.com/kml/2.0'>
<Document>
<name>Zonas de Corte KML</name>
<description>Zonas con Interrupción de Suministro de Agua Potable</description>
<Style id='My_Style_Zona_Corte_AMARILLO'>
  <LineStyle>
    <color>ff00befe</color>
    <width>2</width>
  </LineStyle>
  <PolyStyle>
    <color>1400befe</color>
    <fill>1</fill>
    <outline>1</outline>
  </PolyStyle>
</Style>
<Placemark>
<id_login>2</id_login>
<id_wf>10771</id_wf>
<nombre_wf><![CDATA[CORTE PROGRAMADO SAN ANTONIO]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>613676</id_evento_wf>
<num_sisda_wf>2910226</num_sisda_wf>
<lat_wf>-33.58571326</lat_wf>
<lon_wf>-71.60955332</lon_wf>
<x_wf>257817.07534716</x_wf>
<y_wf>6280725.62473531</y_wf>
<localidad_wf><![CDATA[SAN ANTONIO]]></localidad_wf>
<nombre_comuna_wf><![CDATA[SAN ANTONIO]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>Av. Doctor Luis Reuss entre Domingo Fernández Concha y Manuel Blanco Encalada-21 de mayo</td></tr><tr><th>Nombre:</th><td>CORTE PROGRAMADO SAN ANTONIO</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>24-09-2026 15:00</td></tr><tr><th>Fin (*):</th><td>24-09-2026 21:00</td></tr><tr><th>Localidad:</th><td>SAN ANTONIO</td></tr><tr><th>Categoría:</th><td>Corte Programado</td></tr><tr><th>Motivo:</th><td>Mejoramiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>12584</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-33.58571325689510</lat_sector>
<lon_sector>-71.60955335826460</lon_sector>
<x_sector>257817.07534716</x_sector>
<y_sector>6280725.62473531</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>32503409</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-71.60720691021080,-33.58480282437650,0 -71.60721868956970,-33.58468879114420,0 -71.60687686188420,-33.58457149067790,0 -71.60694759453470,-33.58433562372940,0 -71.60720691021080,-33.58480282437650,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
<id_login>2</id_login>
<id_wf>10798</id_wf>
<nombre_wf><![CDATA[CORTE PROGRAMADO QUILPUE]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>616930</id_evento_wf>
<num_sisda_wf>2912217</num_sisda_wf>
<lat_wf>-33.05574019</lat_wf>
<lon_wf>-71.43465675</lon_wf>
<x_wf>272679.70533597</x_wf>
<y_wf>6339898.31828519</y_wf>
<localidad_wf><![CDATA[QUILPUE]]></localidad_wf>
<nombre_comuna_wf><![CDATA[QUILPUE]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>SECTOR DOCTOR SALAS ESQUINA LA OBRA</td></tr><tr><th>Nombre:</th><td>CORTE PROGRAMADO QUILPUE</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>23-09-2026 15:00</td></tr><tr><th>Fin (*):</th><td>24-09-2026 02:00</td></tr><tr><th>Localidad:</th><td>QUILPUE</td></tr><tr><th>Categoría:</th><td>Corte Programado</td></tr><tr><th>Motivo:</th><td>Mejoramiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>14247</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-33.05574745382340</lat_sector>
<lon_sector>-71.43464359966560</lon_sector>
<x_sector>272680.95567699</x_sector>
<y_sector>6339897.54085636</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>28002851</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-71.43099698697440,-33.05665172424750,0 -71.43101531415230,-33.05653163284530,0 -71.43129357920440,-33.05649588258530,0 -71.43196589629920,-33.05620346877370,0 -71.43099698697440,-33.05665172424750,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
<id_login>2</id_login>
<id_wf>10814</id_wf>
<nombre_wf><![CDATA[Corte programado Putaendo Rda. De Silva.]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>622529</id_evento_wf>
<num_sisda_wf>2914979</num_sisda_wf>
<lat_wf>-32.66660849</lat_wf>
<lon_wf>-70.71383121</lon_wf>
<x_wf>339291.54151938</x_wf>
<y_wf>6384374.34355119</y_wf>
<localidad_wf><![CDATA[PUTAENDO]]></localidad_wf>
<nombre_comuna_wf><![CDATA[PUTAENDO]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>Calle el Carmen, 18 de sept y Rinconada de Silva.</td></tr><tr><th>Nombre:</th><td>Corte programado Putaendo Rda. De Silva.</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>24-09-2026 15:00</td></tr><tr><th>Fin (*):</th><td>24-09-2026 20:00</td></tr><tr><th>Localidad:</th><td>PUTAENDO</td></tr><tr><th>Categoría:</th><td>Corte Programado</td></tr><tr><th>Motivo:</th><td>Mejoramiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>11487</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-32.66627048424170</lat_sector>
<lon_sector>-70.71152095574040</lon_sector>
<x_sector>339507.60249785</x_sector>
<y_sector>6384415.31618479</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>26002175</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-70.71237433729520,-32.66730424145680,0 -70.70918896749860,-32.66803632818920,0 -70.70691363345810,-32.66896715590470,0 -70.706711072856,-32.66832173616320,0 -70.71237433729520,-32.66730424145680,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
<id_login>2</id_login>
<id_wf>10814</id_wf>
<nombre_wf><![CDATA[Corte programado Putaendo Rda. De Silva.]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>622529</id_evento_wf>
<num_sisda_wf>2914979</num_sisda_wf>
<lat_wf>-32.66660849</lat_wf>
<lon_wf>-70.71383121</lon_wf>
<x_wf>339291.54151938</x_wf>
<y_wf>6384374.34355119</y_wf>
<localidad_wf><![CDATA[PUTAENDO]]></localidad_wf>
<nombre_comuna_wf><![CDATA[PUTAENDO]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>Calle el Carmen, 18 de sept y Rinconada de Silva.</td></tr><tr><th>Nombre:</th><td>Corte programado Putaendo Rda. De Silva.</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>24-09-2026 15:00</td></tr><tr><th>Fin (*):</th><td>24-09-2026 20:00</td></tr><tr><th>Localidad:</th><td>PUTAENDO</td></tr><tr><th>Categoría:</th><td>Corte Programado</td></tr><tr><th>Motivo:</th><td>Mejoramiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>50828</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-32.66735041990050</lat_sector>
<lon_sector>-70.71890352751380</lon_sector>
<x_sector>338817.17778911</x_sector>
<y_sector>6384284.38765512</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>26002191</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-70.71716832885050,-32.663844697728,0 -70.71726133508520,-32.66373327469210,0 -70.71785220174430,-32.66361665315090,0 -70.71806979534150,-32.66373595096390,0 -70.71716832885050,-32.663844697728,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
<id_login>2</id_login>
<id_wf>10823</id_wf>
<nombre_wf><![CDATA[CORTE DE EMERGENCIA - VIÑA DEL MAR]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>625737</id_evento_wf>
<num_sisda_wf>2916567</num_sisda_wf>
<lat_wf>-33.00267790</lat_wf>
<lon_wf>-71.50911443</lon_wf>
<x_wf>265585.35539194</x_wf>
<y_wf>6345619.40149238</y_wf>
<localidad_wf><![CDATA[VINA DEL MAR]]></localidad_wf>
<nombre_comuna_wf><![CDATA[VINA DEL MAR]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>LOS PENSAMIENTOS</td></tr><tr><th>Nombre:</th><td>CORTE DE EMERGENCIA - VIÑA DEL MAR</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>23-09-2026 11:00</td></tr><tr><th>Fin (*):</th><td>23-09-2026 17:00</td></tr><tr><th>Localidad:</th><td>VINA DEL MAR</td></tr><tr><th>Categoría:</th><td>Corte No Programado</td></tr><tr><th>Motivo:</th><td>Mantenimiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>9515</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-33.00372307716680</lat_sector>
<lon_sector>-71.50771347874830</lon_sector>
<x_sector>265719.04306001</x_sector>
<y_sector>6345506.61182878</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>40005499</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-71.50607109417210,-33.00567529210450,0 -71.50563700281550,-33.005296754539,0 -71.505564228399,-33.00537239679490,0 -71.50455728506270,-33.00449449198620,0 -71.50607109417210,-33.00567529210450,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
<Placemark>
<id_login>2</id_login>
<id_wf>10823</id_wf>
<nombre_wf><![CDATA[CORTE DE EMERGENCIA - VIÑA DEL MAR]]></nombre_wf>
<id_estado_wf>4</id_estado_wf>
<estado_wf>EN CURSO</estado_wf>
<id_evento_wf>625737</id_evento_wf>
<num_sisda_wf>2916567</num_sisda_wf>
<lat_wf>-33.00267790</lat_wf>
<lon_wf>-71.50911443</lon_wf>
<x_wf>265585.35539194</x_wf>
<y_wf>6345619.40149238</y_wf>
<localidad_wf><![CDATA[VINA DEL MAR]]></localidad_wf>
<nombre_comuna_wf><![CDATA[VINA DEL MAR]]></nombre_comuna_wf>
<region_wf>5</region_wf>
<tooltip_wf><![CDATA[<tr><th style='width:60px;'>Donde:</th><td>LOS PENSAMIENTOS</td></tr><tr><th>Nombre:</th><td>CORTE DE EMERGENCIA - VIÑA DEL MAR</td></tr><tr><th>Estado:</th><td>En Curso</td></tr><tr><th>Inicio (*):</th><td>23-09-2026 11:00</td></tr><tr><th>Fin (*):</th><td>23-09-2026 17:00</td></tr><tr><th>Localidad:</th><td>VINA DEL MAR</td></tr><tr><th>Categoría:</th><td>Corte No Programado</td></tr><tr><th>Motivo:</th><td>Mantenimiento de infraestructura</td></tr><tr><th>Suministro Alternativo:</th><td>No</td></tr><tr><td colspan="2" style="font-size:70%;">(*) Valor Referencial estimado</td></tr>]]></tooltip_wf>
<id_sector>15403</id_sector>
<id_estado_sector>1</id_estado_sector>
<estado_sector>Zona con Corte</estado_sector>
<color_estado_sector>AZUL</color_estado_sector>
<lat_sector>-32.99884247701970</lat_sector>
<lon_sector>-71.50817139129320</lon_sector>
<x_sector>265663.33787486</x_sector>
<y_sector>6346046.86731151</y_sector>
<tooltip_sector><![CDATA[<tr><th>Nombre Sector:</th><td>40005036</td></tr>]]></tooltip_sector>
<styleUrl>#My_Style_Zona_Corte_AZUL</styleUrl>
  <Polygon>
    <extrude>1</extrude>
    <tessellate>1</tessellate>
    <outerBoundaryIs>
      <LinearRing>
        <coordinates>
-71.50780839772610,-32.99730040375760,0 -71.50819137408470,-32.99732463051430,0 -71.50851861121450,-32.99791700337760,0 -71.50883619205060,-32.99930731596620,0 -71.50780839772610,-32.99730040375760,0
        </coordinates>
      </LinearRing>
    </outerBoundaryIs>
  </Polygon>
</Placemark>
</Document>
</kml>
"""

#: Página de error de IIS, que es lo que sirve el visor cuando algo falla.
HTML_DE_ERROR = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN">\r\n<html><head>'
    "<title>500 - Internal server error.</title></head><body></body></html>"
)


# --- Ayudas ------------------------------------------------------------------


def collector() -> EsvalCollector:
    """Instancia sin `__init__`: no toca sesión ni base de datos."""
    instancia = EsvalCollector.__new__(EsvalCollector)
    instancia.api_url = settings.ESVAL_CORTES_URL
    instancia.kml_url = settings.ESVAL_ZONAS_KML_URL
    instancia.bbox = settings.region_bbox
    return instancia


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(esval_worker, "_ahora", lambda: AHORA)
    return AHORA


def mock_api(cuerpo: str | dict | list, status: int = 200) -> respx.Route:
    texto = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
    return respx.get(settings.ESVAL_CORTES_URL).mock(
        return_value=httpx.Response(
            status, text=texto, headers={"content-type": "text/plain; charset=utf-8"}
        )
    )


def mock_kml(cuerpo: str = KML_ZONAS, status: int = 200) -> respx.Route:
    return respx.get(settings.ESVAL_ZONAS_KML_URL).mock(
        return_value=httpx.Response(
            status, text=cuerpo, headers={"content-type": "text/html; charset=utf-8"}
        )
    )


def api_con(*registros: dict) -> dict:
    return {"data": list(registros)}


def registros_reales() -> list[dict]:
    return json.loads(API_CORTES)["data"]


# --- La API tal como es -------------------------------------------------------


def test_la_api_real_es_un_sobre_data_sin_coordenadas():
    """El pedido original asumía una lista con `lat`/`lon`. No es ninguna de las dos."""
    payload = json.loads(API_CORTES)
    assert isinstance(payload, dict)
    registros = records_or_raise(payload, company="esval", url="x")
    assert len(registros) == 5

    cortes = [parse_corte(r) for r in registros]
    assert all(c is not None for c in cortes)
    assert all(c.lat is None and c.lon is None for c in cortes if c)
    assert claves_ausentes(registros) == []


def test_fechas_en_hora_chilena():
    """11:00 de pared en septiembre (UTC-3) son las 14:00 UTC."""
    vina = parse_corte(registros_reales()[0])
    assert vina is not None
    assert vina.inicio == datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    assert vina.fin == datetime(2026, 9, 23, 20, 0, tzinfo=UTC)


def test_fin_que_cruza_la_medianoche():
    """Quilpué, 15:00 → 02:00 del día siguiente, con `fechaFin` correcta."""
    quilpue = parse_corte(registros_reales()[1])
    assert quilpue is not None
    assert quilpue.fin == datetime(2026, 9, 24, 5, 0, tzinfo=UTC)

    # Y si la fuente repite la fecha de inicio en el fin, se corrige sola.
    mal_fechado = parse_corte({**registros_reales()[1], "fechaFin": "23-09-2026"})
    assert mal_fechado is not None
    assert mal_fechado.fin == datetime(2026, 9, 24, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("fecha", "hora", "esperado"),
    [
        ("23-09-2026", "11:00", datetime(2026, 9, 23, 14, 0, tzinfo=UTC)),
        ("23/09/2026", "11:00:00", datetime(2026, 9, 23, 14, 0, tzinfo=UTC)),
        ("2026-09-23", "11.00", datetime(2026, 9, 23, 14, 0, tzinfo=UTC)),
        ("2026-09-23T11:00:00", None, datetime(2026, 9, 23, 14, 0, tzinfo=UTC)),
        ("23-09-2026 11:00", None, datetime(2026, 9, 23, 14, 0, tzinfo=UTC)),
        ("23-09-2026", None, datetime(2026, 9, 23, 3, 0, tzinfo=UTC)),
        ("31-02-2026", "11:00", None),
        ("mañana", "11:00", None),
    ],
)
def test_formatos_de_fecha(fecha, hora, esperado):
    corte = parse_corte({"sisda": "1", "fechaInicio": fecha, "horaInicio": hora})
    assert corte is not None
    assert corte.inicio == esperado


def test_tipo_de_corte():
    assert tipo_legible("Corte emergencia") == "emergencia"
    assert tipo_legible("Corte programado") == "programado"
    assert tipo_legible("Corte No Programado") == "no programado"
    assert tipo_legible(None) is None


def test_coordenadas_propias_si_algun_dia_las_trae():
    """La heurística pedida: hoy no encuentra nada, pero lee lo plausible."""
    plano = parse_corte({"sisda": "1", "latitud": "-33.04", "longitud": "-71.60"})
    assert plano is not None
    assert (plano.lat, plano.lon) == (-33.04, -71.60)

    # Un par en orden GeoJSON se reconoce igual: los rangos de Chile no se solapan.
    invertido = parse_corte({"sisda": "1", "coordenadas": "-71.60,-33.04"})
    assert invertido is not None
    assert (invertido.lat, invertido.lon) == (-33.04, -71.60)

    objeto = parse_corte({"sisda": "1", "coordenadas": {"lat": -33.04, "lng": -71.60}})
    assert objeto is not None
    assert (objeto.lat, objeto.lon) == (-33.04, -71.60)

    # Fuera de Chile no es una coordenada: es otro campo mal leído.
    basura = parse_corte({"sisda": "1", "lat": "12", "lon": "40"})
    assert basura is not None
    assert basura.lat is None


def test_registro_que_no_identifica_nada_no_es_un_corte():
    assert parse_corte({"foo": 1}) is None
    assert parse_corte("texto") is None
    assert parse_corte(None) is None


# --- KML del visor -------------------------------------------------------------


def test_kml_real_se_agrupa_por_sisda():
    zonas = parse_zonas_kml(KML_ZONAS)
    assert set(zonas) == {"2910226", "2912217", "2914979", "2916567"}

    vina = zonas["2916567"]
    assert (vina.lat, vina.lon) == (-33.0026779, -71.50911443)
    assert vina.comuna == "VINA DEL MAR"
    assert vina.region == "5"
    assert [s.sector_id for s in vina.sectores] == ["9515", "15403"]
    # Orden GeoJSON (lon, lat), 5 decimales, anillo cerrado.
    anillo = vina.sectores[0].anillo
    assert anillo[0] == (-71.50607, -33.00568)
    assert anillo[0] == anillo[-1]


def test_ficha_del_tooltip():
    vina = parse_zonas_kml(KML_ZONAS)["2916567"]
    assert vina.ficha["categoria"] == "Corte No Programado"
    assert vina.ficha["suministro_alternativo"] == "No"
    assert vina.ficha["donde"] == "LOS PENSAMIENTOS"
    assert vina.ficha["inicio"] == "23-09-2026 11:00"
    assert parse_ficha(None) == {}


def test_kml_acepta_bytes():
    assert set(parse_zonas_kml(KML_ZONAS.encode("utf-8"))) == set(parse_zonas_kml(KML_ZONAS))


@pytest.mark.parametrize("cuerpo", [HTML_DE_ERROR, "<kml><Placemark>", ""])
def test_kml_que_no_es_kml_falla_legible(cuerpo):
    with pytest.raises(KmlZonasError):
        parse_zonas_kml(cuerpo)


# --- Unión y comuna ---------------------------------------------------------------


def test_rinconada_es_putaendo():
    """El caso que obliga a sacar la comuna del KML y no de `localidadNombre`."""
    rinconada = parse_corte(registros_reales()[2])
    assert rinconada is not None
    assert rinconada.localidad == "Rinconada"

    [con_zona] = unir([rinconada], parse_zonas_kml(KML_ZONAS))
    assert comuna_del_corte(con_zona) == "Putaendo"

    # Sin KML no hay forma de saberlo: queda la comuna homónima. Documentado.
    assert comuna_del_corte(CorteAgua(corte=rinconada, zona=None)) == "Rinconada"


def test_la_comuna_sale_con_su_nombre_canonico():
    [vina] = unir([parse_corte(registros_reales()[0])], parse_zonas_kml(KML_ZONAS))
    assert comuna_del_corte(vina) == "Viña del Mar"


# --- Identidad --------------------------------------------------------------------


def test_external_id_es_el_sisda():
    vina = parse_corte(registros_reales()[0])
    assert vina is not None
    assert build_external_id(vina) == "esval:corte:2916567"


def test_external_id_sin_sisda():
    sin_sisda = parse_corte({**registros_reales()[0], "sisda": None})
    assert sin_sisda is not None
    assert build_external_id(sin_sisda) == "esval:corte:id:9439"

    sin_nada = parse_corte({**registros_reales()[0], "sisda": None, "_id": None})
    assert sin_nada is not None
    clave = build_external_id(sin_nada)
    assert clave.startswith("esval:corte:h:")
    # Determinista, y sin depender de nada que venga del KML.
    otra = parse_corte({**registros_reales()[0], "sisda": "", "_id": ""})
    assert otra is not None
    assert build_external_id(otra) == clave


def test_sisda_repetido_gana_el_inicio_mas_reciente():
    viejo = parse_corte({**registros_reales()[0], "fechaInicio": "22-09-2026"})
    nuevo = parse_corte(registros_reales()[0])
    assert viejo is not None and nuevo is not None
    unicos, repetidos = sin_duplicados([viejo, nuevo])
    assert repetidos == 1
    assert unicos == [nuevo]


# --- Texto ------------------------------------------------------------------------


def test_texto_del_evento():
    [vina] = unir([parse_corte(registros_reales()[0])], parse_zonas_kml(KML_ZONAS))
    assert build_text(vina, "Viña del Mar") == (
        "Corte de agua (Esval, emergencia) — Viña del Mar — LOS PENSAMIENTOS — "
        "23-09 11:00 a 17:00 — Vida util vencida"
    )


def test_texto_con_calles_largas_y_ventana_de_dos_dias():
    [quilpue] = unir([parse_corte(registros_reales()[1])], {})
    texto = build_text(quilpue, "Quilpué")
    assert texto.startswith("Corte de agua (Esval, programado) — Quilpué — Del Arrayan")
    assert "…" in texto
    assert "23-09 15:00 a 24-09 02:00" in texto
    assert texto.endswith("Renovación de infraestructura")


def test_ventana_legible_con_un_solo_extremo():
    inicio = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    assert ventana_legible(inicio, None) == "desde 23-09 11:00"
    assert ventana_legible(None, inicio) == "hasta 23-09 11:00"
    assert ventana_legible(None, None) is None


# --- Collector: la corrida completa con las capturas ---------------------------


@respx.mock
def test_fetch_real_filtra_empresa_y_futuros_y_une_el_kml(reloj):
    """A las 15:30: Viña (11:00) y Quilpué (15:00) ya empezaron. El resto no entra.

    Fuera: Andacollo (Aguas del Valle, empresa 2) y los dos programados de
    mañana (Rinconada/Putaendo y San Antonio).
    """
    api = mock_api(API_CORTES)
    kml = mock_kml()
    instancia = collector()

    cortes = asyncio.run(instancia.fetch())

    assert sorted(c.corte.sisda for c in cortes) == ["2912217", "2916567"]
    assert all(c.zona is not None for c in cortes)
    assert all(c.punto is not None for c in cortes)
    assert instancia.warnings == []

    # Las cabeceras que se pidieron, y un UA que dice quién es.
    pedido = api.calls.last.request
    assert pedido.headers["XCodempresa"] == "1"
    assert pedido.headers["Referer"] == "https://ov.esval.cl/"
    assert "AlertaV" in pedido.headers["User-Agent"]
    assert pedido.headers["Accept"].startswith("application/json")

    # El KML se pide una sola vez, con la región y la caja entera.
    assert kml.call_count == 1
    params = kml.calls.last.request.url.params
    assert params["region"] == "5"
    caja = settings.region_bbox
    assert params["bbox"] == f"{caja.west},{caja.south},{caja.east},{caja.north}"


@respx.mock
def test_normalize_real(reloj):
    mock_api(API_CORTES)
    mock_kml()
    instancia = collector()
    eventos = {e.external_id: e for e in instancia.normalize(asyncio.run(instancia.fetch()))}

    assert set(eventos) == {"esval:corte:2916567", "esval:corte:2912217"}
    vina = eventos["esval:corte:2916567"]
    assert vina.source is EventSource.ESVAL
    assert vina.type is EventType.WATER_CUT
    assert vina.confidence == WATER_CUT_CONFIDENCE == 1.0
    # El corte existe desde su inicio, no desde que lo vimos.
    assert vina.timestamp == datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    assert (vina.lat, vina.lon) == (-33.0026779, -71.50911443)
    assert vina.raw_data["comuna"] == "Viña del Mar"
    assert vina.raw_data["company"] == "esval"
    assert vina.raw_data["_source_record"]["sisda"] == "2916567"

    detalle = vina.raw_data[ESVAL_KEY]
    assert detalle["tipo"] == "emergencia"
    assert detalle["programado"] is False
    assert detalle["origen_del_punto"] == "kml"
    assert detalle["visor"]["categoria"] == "Corte No Programado"
    assert len(detalle["sectores"]) == 2
    assert detalle["sectores"][0]["anillo"][0] == [-71.50607, -33.00568]
    assert detalle["fin"] == "2026-09-23T20:00:00+00:00"
    assert detalle["visto_en"] == AHORA.isoformat()

    quilpue = eventos["esval:corte:2912217"]
    assert quilpue.raw_data["comuna"] == "Quilpué"
    assert quilpue.raw_data[ESVAL_KEY]["programado"] is True


@respx.mock
def test_los_programados_entran_cuando_empiezan(monkeypatch):
    """Mañana a las 15:05, Rinconada/Putaendo y San Antonio ya son cortes vigentes."""
    monkeypatch.setattr(esval_worker, "_ahora", lambda: datetime(2026, 9, 24, 18, 5, tzinfo=UTC))
    mock_api(API_CORTES)
    mock_kml()
    instancia = collector()
    cortes = asyncio.run(instancia.fetch())
    assert sorted(c.corte.sisda for c in cortes) == ["2910226", "2912217", "2914979", "2916567"]
    putaendo = next(c for c in cortes if c.corte.sisda == "2914979")
    assert comuna_del_corte(putaendo) == "Putaendo"


@respx.mock
def test_sin_cortes_vigentes_no_se_pide_el_kml(monkeypatch):
    monkeypatch.setattr(esval_worker, "_ahora", lambda: datetime(2026, 9, 23, 12, 0, tzinfo=UTC))
    mock_api(API_CORTES)
    kml = mock_kml()
    instancia = collector()
    assert asyncio.run(instancia.fetch()) == []
    assert kml.call_count == 0
    assert instancia.warnings == []


# --- Degradaciones: el KML ------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, text="error"),
        httpx.Response(200, text=HTML_DE_ERROR, headers={"content-type": "text/html"}),
        httpx.Response(200, text="<kml><Placemark>"),
    ],
)
def test_kml_caido_los_cortes_entran_sin_coordenadas(reloj, respuesta):
    mock_api(API_CORTES)
    respx.get(settings.ESVAL_ZONAS_KML_URL).mock(return_value=respuesta)
    instancia = collector()

    cortes = asyncio.run(instancia.fetch())
    eventos = instancia.normalize(cortes)

    assert sorted(e.external_id for e in eventos) == ["esval:corte:2912217", "esval:corte:2916567"]
    assert all(e.lat is None and e.lon is None for e in eventos)
    assert all(e.text for e in eventos)
    # Sin zona, la comuna sale de la API.
    assert {e.raw_data["comuna"] for e in eventos} == {"Viña del Mar", "Quilpué"}
    assert any("KML" in aviso for aviso in instancia.warnings)


@respx.mock
def test_kml_sin_ninguno_de_los_cortes_avisa(reloj):
    mock_api(API_CORTES)
    mock_kml(KML_ZONAS.replace("2916567", "1").replace("2912217", "2"))
    instancia = collector()
    cortes = asyncio.run(instancia.fetch())
    assert len(cortes) == 2
    assert any("ninguno de los 2 cortes" in aviso for aviso in instancia.warnings)


@respx.mock
def test_un_corte_sin_zona_es_normal(reloj):
    """San Antonio (emergencia de las 13:00) no está en el KML de las 15:25."""
    mock_api(api_con(*registros_reales(), REGISTRO_SAN_ANTONIO))
    mock_kml()
    instancia = collector()
    cortes = asyncio.run(instancia.fetch())
    san_antonio = next(c for c in cortes if c.corte.sisda == "2916550")
    assert san_antonio.zona is None
    assert instancia.warnings == []

    [evento] = [e for e in instancia.normalize(cortes) if e.external_id == "esval:corte:2916550"]
    assert evento.lat is None
    assert evento.raw_data["comuna"] == "San Antonio"
    assert "Matriz filtrando" in (evento.text or "")


# --- Degradaciones: la API ------------------------------------------------------


@respx.mock
def test_esquema_desconocido_no_revienta_y_loguea_el_primer_registro(reloj, caplog):
    """La directiva: si las claves no coinciden, al log con el primer objeto."""
    mock_api({"data": [{"folio": "X-1", "comunaAfectada": "Quilpué", "desde": "hoy"}]})
    kml = mock_kml()
    instancia = collector()

    with caplog.at_level(logging.WARNING, logger=esval_worker.__name__):
        cortes = asyncio.run(instancia.fetch())

    assert cortes == []
    assert kml.call_count == 0
    assert any("esquema de CortesActivos cambió" in aviso for aviso in instancia.warnings)
    assert any("ilegibles" in aviso for aviso in instancia.warnings)
    mensajes = [r.getMessage() for r in caplog.records]
    assert any('"folio": "X-1"' in m and "Primer registro" in m for m in mensajes)


@respx.mock
def test_lista_de_cosas_que_no_son_objetos(reloj, caplog):
    mock_api({"data": [1, 2, 3]})
    instancia = collector()
    with caplog.at_level(logging.WARNING, logger=esval_worker.__name__):
        assert asyncio.run(instancia.fetch()) == []
    assert any("ilegibles" in aviso for aviso in instancia.warnings)
    assert any("Primer registro: 1" in r.getMessage() for r in caplog.records)


@respx.mock
def test_una_clave_renombrada_avisa_pero_entra_lo_legible(reloj):
    """Si `fechaInicio` pasara a llamarse `inicioCorte`, el corte entra fechado al
    verlo, y la corrida queda `partial` diciendo qué clave falta."""
    renombrados = [
        {("inicioCorte" if k == "fechaInicio" else k): v for k, v in r.items()}
        for r in registros_reales()
    ]
    mock_api(api_con(*renombrados))
    mock_kml()
    instancia = collector()
    cortes = asyncio.run(instancia.fetch())

    assert any("faltan fechaInicio" in aviso for aviso in instancia.warnings)
    assert any("sin fecha de inicio legible" in aviso for aviso in instancia.warnings)
    # Andacollo sigue fuera (empresa 2); los cuatro de Esval entran.
    assert len(cortes) == 4
    eventos = instancia.normalize(cortes)
    assert all(e.timestamp == AHORA for e in eventos)


@respx.mock
def test_la_lista_como_raiz_tambien_sirve(reloj):
    mock_api(registros_reales())
    mock_kml()
    assert len(asyncio.run(collector().fetch())) == 2


@respx.mock
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(200, json={"foo": 1, "bar": 2}),
        httpx.Response(200, text=HTML_DE_ERROR, headers={"content-type": "text/html"}),
        httpx.Response(404, text="not found"),
    ],
)
def test_sin_api_no_hay_corrida(reloj, respuesta):
    """Sin una lista reconocible, `failed` con mensaje legible; nunca cero y `success`."""
    respx.get(settings.ESVAL_CORTES_URL).mock(return_value=respuesta)
    with pytest.raises(CollectorError):
        asyncio.run(collector().fetch())


# --- Invariantes de normalize ------------------------------------------------------


def test_normalize_mantiene_los_invariantes_aunque_fetch_ya_filtre(reloj):
    zonas = parse_zonas_kml(KML_ZONAS)
    cortes = unir([c for c in map(parse_corte, registros_reales()) if c], zonas)
    eventos = collector().normalize(cortes)
    # Andacollo (empresa 2) y los dos de mañana no pasan, se llame como se llame.
    assert sorted(e.external_id for e in eventos) == ["esval:corte:2912217", "esval:corte:2916567"]


def test_zona_de_otra_region_se_descarta(reloj):
    zonas = parse_zonas_kml(
        KML_ZONAS.replace("<region_wf>5</region_wf>", "<region_wf>4</region_wf>")
    )
    vina = parse_corte(registros_reales()[0])
    assert collector().normalize(unir([vina], zonas)) == []


def test_sin_empresa_ni_zona_entra_solo_si_la_comuna_es_de_la_region(reloj):
    sin_empresa = {k: v for k, v in registros_reales()[0].items() if k != "empresaId"}
    andacollo = {k: v for k, v in registros_reales()[4].items() if k != "empresaId"}
    andacollo["fechaInicio"] = "23-09-2026"
    cortes = unir([parse_corte(sin_empresa), parse_corte(andacollo)], {})
    eventos = collector().normalize(cortes)
    assert [e.external_id for e in eventos] == ["esval:corte:2916567"]


# --- Aislamiento y registro ----------------------------------------------------------


def test_water_cut_es_contexto_fuera_del_motor():
    assert EventType.WATER_CUT not in CORRELATABLE_EVENT_TYPES
    assert EventType.WATER_CUT not in EVENT_TO_INCIDENT_TYPE
    assert SOURCE_BASE_CONFIDENCE[EventSource.ESVAL] == 1.0
    regla = RULES[EventSource.ESVAL]
    assert (regla.min_weight, regla.max_weight, regla.ceiling) == (0.0, 0.0, 0.0)
    assert not regla.confirming


def test_el_collector_esta_registrado_con_su_cadencia():
    assert registry.COLLECTORS["esval_cortes_agua"] is EsvalCollector
    assert EsvalCollector.poll_interval_seconds() == settings.ESVAL_POLL_INTERVAL_SECONDS


def test_run_params_no_filtra_cabeceras():
    params = collector().run_params()
    assert "XCodempresa" not in json.dumps(params)
    assert params["kml_params"]["region"] == "5"


def test_las_cabeceras_son_latin1():
    """httpx rechaza cabeceras fuera de latin-1 antes de abrir la conexión."""
    for valor in collector().api_headers().values():
        valor.encode("latin-1")


def test_claves_esperadas_estan_en_la_captura_real():
    presentes = set(registros_reales()[0])
    assert set(CLAVES_ESPERADAS) <= presentes


def test_migracion_0014_encadena_con_la_0013():
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0014_esval_cortes_agua.py"
    spec = importlib.util.spec_from_file_location("m0014", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.revision == "0014_esval_cortes_agua"
    assert modulo.down_revision == "0013_gbv_vehiculos"
