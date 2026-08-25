"""El camino KMZ de CGE: descompresión en memoria, parseo del KML y filtro espacial.

Por qué este archivo existe aparte de `test_power_collectors`
--------------------------------------------------------------
CGE dejó de compartir transporte con Chilquinta. Chilquinta consulta un JSON por
POST con API key; CGE descarga un archivo de Google Earth y lo abre en memoria.
Lo que se prueba acá —ZIP, XML, HTML dentro de `<description>`— no tiene nada
que ver con lo que se prueba allá, y mezclarlos habría dejado un módulo donde la
mitad de los ayudantes no aplican a la mitad de los tests.

Qué garantizan estos tests y qué no
------------------------------------
El KMZ real no se pudo descargar desde el entorno de verificación, así que estos
archivos son **construidos a mano** con la estructura que define la
especificación de KML. Eso los hace buenos para lo que sí cubren —el orden de
las coordenadas, los namespaces, el HTML escapado dos veces, los miles
chilenos— y no los convierte en garantía de que el archivo de CGE encaje al
primer intento.

Lo que sí garantizan es que **cuando no encaje, falle de forma legible**: que un
HTML servido por el CDN se reporte como «no es un ZIP» y no como un error de
JSON, y que un Placemark raro se descarte sin llevarse la corrida por delante.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.kmz_parser import (
    KmzFormatError,
    describe_kmz,
    extract_kml,
    parse_description,
    parse_kmz,
    parse_placemarks,
    plain_text,
)
from app.collectors.power.outage_parser import parse_outage
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource

#: Viña del Mar. Dentro de la V Región.
VINA_LAT, VINA_LON = -33.0245, -71.5518

#: Plantilla mínima de KML. El namespace por defecto está puesto a propósito:
#: ElementTree devuelve las etiquetas expandidas y todo el parser depende de
#: buscar por nombre local. Sin namespace en los tests, ese camino no se
#: ejercitaría nunca y el fallo aparecería recién en producción.
KML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Afectaciones CGE</name>
    <Folder>
      <name>Cortes</name>
      {placemarks}
    </Folder>
  </Document>
</kml>
"""


def placemark(
    *,
    name: str = "Corte 1",
    lon: float = VINA_LON,
    lat: float = VINA_LAT,
    description: str = "",
    coordinates: str | None = None,
) -> str:
    """Un `<Placemark>` de punto. `coordinates` permite geometrías arbitrarias.

    Ojo con el orden: `lon,lat`. Es el de la especificación KML y el que el
    parser tiene que respetar; escribirlo al revés acá haría pasar un test que
    en producción mandaría todos los cortes al océano Índico.
    """
    coords = coordinates if coordinates is not None else f"{lon},{lat},0"
    bloque_desc = f"<description><![CDATA[{description}]]></description>" if description else ""
    return f"""
      <Placemark>
        <name>{name}</name>
        {bloque_desc}
        <Point><coordinates>{coords}</coordinates></Point>
      </Placemark>"""


def build_kml(*placemarks: str) -> bytes:
    return KML_TEMPLATE.format(placemarks="\n".join(placemarks)).encode()


def build_kmz(kml: bytes, *, nombre: str = "doc.kml", extras: dict | None = None) -> bytes:
    """Un KMZ en memoria. Igual que lo construye cualquier exportador."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr(nombre, kml)
        for ruta, contenido in (extras or {}).items():
            archivo.writestr(ruta, contenido)
    return buffer.getvalue()


def collector() -> CgeCollector:
    """Instancia sin `__init__`: no toca sesión ni base de datos."""
    instancia = CgeCollector.__new__(CgeCollector)
    instancia.bbox = settings.region_bbox
    instancia.url = "https://cge.test/mapa_cge.kmz"
    return instancia


# --- Descompresión en memoria ------------------------------------------------


def test_el_kmz_se_abre_sin_tocar_el_disco():
    """`io.BytesIO` + `zipfile`: el archivo nunca existe como fichero."""
    kml = build_kml(placemark())
    assert extract_kml(build_kmz(kml)) == kml


def test_se_prefiere_doc_kml_cuando_hay_varios_miembros():
    """`doc.kml` es el nombre de la especificación; los iconos se ignoran."""
    correcto = build_kml(placemark(name="el bueno"))
    kmz = build_kmz(
        correcto,
        extras={
            "files/icono.png": b"\x89PNG fake",
            "files/otro.kml": build_kml(placemark(name="el otro")),
        },
    )
    assert b"el bueno" in extract_kml(kmz)


def test_sirve_el_unico_kml_aunque_no_se_llame_doc():
    """Varios exportadores nombran el KML como el mapa. Sigue siendo el único."""
    kml = build_kml(placemark())
    assert extract_kml(build_kmz(kml, nombre="mapa_cge.kml")) == kml


def test_un_html_no_es_un_kmz_y_lo_dice():
    """El modo de fallo más probable: el CDN devuelve la página del portal.

    El mensaje tiene que hablar de ZIP, no de JSON ni de XML. Es lo que queda
    escrito en `collector_runs.error` y lo que decide si alguien busca el
    problema en el sitio correcto.
    """
    with pytest.raises(KmzFormatError, match="ZIP"):
        extract_kml(b"<!DOCTYPE html><html><body>Portal CGE</body></html>")


def test_un_zip_sin_kml_falla_enumerando_lo_que_traia():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archivo:
        archivo.writestr("leeme.txt", b"nada util")
    with pytest.raises(KmzFormatError, match=r"leeme\.txt"):
        extract_kml(buffer.getvalue())


def test_un_kml_vacio_no_pasa_por_valido():
    with pytest.raises(KmzFormatError, match="vacío"):
        extract_kml(build_kmz(b"   "))


def test_describe_kmz_distingue_html_de_zip():
    """El diagnóstico que acompaña al error. Ver `describe_shape` para el gemelo JSON."""
    assert "no son un ZIP" in describe_kmz(b"<html>portal</html>")
    assert "Portal" in describe_kmz(b"<html>Portal CGE</html>")
    assert "doc.kml" in describe_kmz(build_kmz(build_kml(placemark())))


def test_un_xml_roto_es_fallo_duro_y_no_silencio():
    """CGE cambió el formato: eso necesita a una persona, no un `success` vacío."""
    with pytest.raises(KmzFormatError, match="XML"):
        parse_placemarks(b"<kml><Document><Placemark></kml>")


# --- Coordenadas -------------------------------------------------------------


def test_las_coordenadas_kml_son_lon_lat_y_no_al_reves():
    """El error clásico de este formato, y el más caro de encontrar.

    Invertirlo no lanza ninguna excepción: produce coordenadas perfectamente
    válidas en el océano Índico, el filtro de bounding box las descarta y el
    síntoma es «CGE no reporta nunca». Este test es la única barrera contra eso.
    """
    registros = parse_kmz(build_kmz(build_kml(placemark())))

    assert len(registros) == 1
    assert registros[0]["lat"] == pytest.approx(VINA_LAT)
    assert registros[0]["lon"] == pytest.approx(VINA_LON)
    # Y la comprobación que importa: cae en Chile, no en el Índico.
    assert -34 < registros[0]["lat"] < -32
    assert -72 < registros[0]["lon"] < -71


def test_un_poligono_se_reduce_a_su_centroide():
    """Un sector afectado es un polígono; el mapa necesita un punto."""
    anillo = (
        "-71.6,-33.1,0 -71.4,-33.1,0 -71.4,-32.9,0 -71.6,-32.9,0 -71.6,-33.1,0"
    )
    registros = parse_kmz(build_kmz(build_kml(placemark(coordinates=anillo))))

    assert registros[0]["lat"] == pytest.approx(-33.02, abs=0.05)
    assert registros[0]["lon"] == pytest.approx(-71.52, abs=0.05)


def test_un_placemark_sin_coordenadas_se_omite_sin_tumbar_el_resto():
    """Leyendas y marcadores decorativos: se saltan, no rompen la corrida."""
    kml = build_kml(
        "<Placemark><name>Leyenda</name></Placemark>",
        placemark(name="Corte real"),
    )
    registros = parse_kmz(build_kmz(kml))

    assert len(registros) == 1
    assert registros[0]["_kml_name"] == "Corte real"


def test_el_namespace_de_kml_no_estorba():
    """2.0, 2.1, 2.2 y las extensiones `gx:` de Google se leen igual.

    El parser busca por nombre local justamente para no mantener una lista de
    namespaces que CGE puede cambiar sin avisar.
    """
    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://earth.google.com/kml/2.0">'
        "<Document>"
        f"{placemark()}"
        "</Document></kml>"
    ).encode()

    assert len(parse_placemarks(kml)) == 1


def test_el_kml_con_declaracion_de_codificacion_se_parsea():
    """La trampa de `ET.fromstring` con `str`.

    Un KML decodificado a texto y pasado a ElementTree lanza «Unicode strings
    with encoding declaration are not supported». Por eso `extract_kml` devuelve
    bytes. Este test falla si alguien "simplifica" ese retorno a `str`.
    """
    kml = extract_kml(build_kmz(build_kml(placemark(name="Ñuñoa Alto"))))
    assert isinstance(kml, bytes)
    assert parse_placemarks(kml)[0]["_kml_name"] == "Ñuñoa Alto"


# --- Lectura de la descripción HTML ------------------------------------------


def test_los_miles_chilenos_no_se_leen_como_decimales():
    """«1.250 clientes» son mil doscientos cincuenta, no uno coma veinticinco.

    `as_float("1.250")` devuelve 1.25 y el conteo se guardaría como 1. Es un
    dato que se le muestra al ciudadano durante una emergencia.
    """
    campos = parse_description("Clientes afectados: 1.250")
    assert campos["clientes_afectados"] == "1250"

    corte = parse_outage({"lat": VINA_LAT, "lon": VINA_LON, **campos})
    assert corte is not None
    assert corte.affected_clients == 1250


def test_un_decimal_de_verdad_se_rechaza_en_vez_de_truncarse():
    """Ante la duda, ningún dato es mejor que un dato equivocado."""
    assert "clientes_afectados" not in parse_description("Clientes afectados: 1.25")


def test_el_horario_de_reposicion_se_lee_como_hora_chilena():
    """Y no como UTC. Cuatro horas de error mostrarían como cumplida una
    reposición que todavía no ocurrió."""
    campos = parse_description("Horario de reposición: 20-08-2026 18:30 hrs.")
    assert campos["hora_reposicion"] == "20-08-2026 18:30"

    corte = parse_outage({"lat": VINA_LAT, "lon": VINA_LON, **campos})
    assert corte is not None
    # Agosto en Chile es UTC-4 (horario de invierno): 18:30 local = 22:30 UTC.
    assert corte.restoration_at == datetime(2026, 8, 20, 22, 30, tzinfo=UTC)


def test_una_hora_sin_fecha_se_ancla_al_dia_chileno():
    """CGE publica reposiciones sólo con la hora, dando el día por supuesto.

    Sin resolverla, `parse_timestamp` no la reconoce y el campo se pierde entero.
    """
    ahora = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)  # 16:00 en Chile
    campos = parse_description("Horario de reposición: 18:30", now=ahora)
    assert campos["hora_reposicion"] == "20-08-2026 18:30"


def test_una_hora_ya_muy_pasada_se_lee_como_del_dia_siguiente():
    ahora = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)  # 02:00 en Chile
    campos = parse_description("Horario de reposición: 18:30", now=ahora)
    assert campos["hora_reposicion"] == "21-08-2026 18:30"


def test_una_reposicion_recien_vencida_sigue_siendo_de_hoy():
    """Holgura de seis horas: un corte atrasado es lo habitual durante el evento."""
    ahora = datetime(2026, 8, 20, 23, 0, tzinfo=UTC)  # 19:00 en Chile
    campos = parse_description("Horario de reposición: 18:30", now=ahora)
    assert campos["hora_reposicion"] == "20-08-2026 18:30"


def test_sin_informacion_es_ausencia_y_no_un_texto():
    """Un corte recién abierto no tiene hora de reposición. Es normal, no un fallo."""
    for marcador in ("Sin información", "POR DEFINIR", "N/A", "-"):
        campos = parse_description(f"Horario de reposición: {marcador}")
        assert "hora_reposicion" not in campos, marcador


def test_el_html_doblemente_escapado_se_resuelve():
    """El caso real: el KML guarda `&lt;br&gt;` y dentro quedan más entidades.

    Con un solo `unescape`, «Reposici&oacute;n» no lo reconoce ninguna expresión
    regular y el campo desaparece en silencio.
    """
    crudo = (
        "&lt;b&gt;Clientes afectados:&lt;/b&gt; 340&lt;br&gt;"
        "&lt;b&gt;Horario de reposici&amp;oacute;n:&lt;/b&gt; 21-08-2026 09:00"
    )
    campos = parse_description(crudo)
    assert campos["clientes_afectados"] == "340"
    assert campos["hora_reposicion"] == "21-08-2026 09:00"


def test_una_tabla_html_deja_un_campo_por_linea():
    """Sin cortar por `</tr>`, la captura hasta fin de línea se traga el campo siguiente."""
    tabla = (
        "<table>"
        "<tr><td>Comuna</td><td>Quillota</td></tr>"
        "<tr><td>Clientes afectados</td><td>512</td></tr>"
        "<tr><td>Horario de reposición</td><td>20-08-2026 21:00</td></tr>"
        "</table>"
    )
    assert "Quillota" in plain_text(tabla).splitlines()[0]

    campos = parse_description(tabla)
    assert campos["comuna"] == "Quillota"
    assert campos["clientes_afectados"] == "512"
    assert campos["hora_reposicion"] == "20-08-2026 21:00"


def test_una_descripcion_ilegible_no_lanza():
    """Todo campo ausente produce ausencia. Ninguna excepción."""
    assert parse_description("") == {}
    assert parse_description("<div>bienvenido al mapa</div>") == {}


def test_extended_data_cubre_lo_que_la_descripcion_no_trae():
    """Si CGE estructura los datos, se usan: es el lugar correcto del formato."""
    kml = build_kml(
        """
      <Placemark>
        <name>Corte Limache</name>
        <ExtendedData>
          <Data name="clientes_afectados"><value>87</value></Data>
          <Data name="comuna"><value>Limache</value></Data>
        </ExtendedData>
        <Point><coordinates>-71.27,-33.01,0</coordinates></Point>
      </Placemark>"""
    )
    registro = parse_kmz(build_kmz(kml))[0]
    corte = parse_outage(registro)

    assert corte is not None
    assert corte.affected_clients == 87
    assert corte.commune == "Limache"


# --- El collector completo ---------------------------------------------------


@respx.mock
def test_el_filtro_espacial_rechaza_los_cortes_fuera_de_la_v_region():
    """El requisito intacto: `self.bbox.contains(lat, lon)` antes de guardar nada.

    CGE publica su zona de concesión completa —llega hasta Aysén—, así que sin
    este filtro entrarían cortes de medio país.
    """
    kml = build_kml(
        placemark(name="Viña del Mar", description="Clientes afectados: 120"),
        placemark(name="Puerto Montt", lon=-72.94, lat=-41.47),
        placemark(name="Antofagasta", lon=-70.40, lat=-23.65),
        placemark(name="Punta Arenas", lon=-70.91, lat=-53.15),
    )
    respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, content=build_kmz(kml))
    )

    cortes = asyncio.run(collector().fetch())

    assert len(cortes) == 1, "sólo el de Viña del Mar sobrevive al recorte"
    assert cortes[0].affected_clients == 120


@respx.mock
def test_el_camino_completo_emite_una_senal_de_cge():
    """KMZ → `EventCreate`, con la fuente y los campos del producto."""
    kml = build_kml(
        placemark(
            name="Sector Recreo",
            description=(
                "<b>Comuna:</b> Viña del Mar<br>"
                "<b>Clientes afectados:</b> 1.430<br>"
                "<b>Horario de reposición:</b> 20-08-2026 18:30 hrs."
            ),
        )
    )
    respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, content=build_kmz(kml))
    )

    instancia = collector()
    eventos = instancia.normalize(asyncio.run(instancia.fetch()))

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.source is EventSource.CGE
    assert evento.raw_data["company"] == "cge"
    assert evento.raw_data["affected_clients"] == 1430
    assert evento.raw_data["restoration_at"] == "2026-08-20T22:30:00+00:00"
    assert evento.external_id.startswith("cge:")


@respx.mock
def test_un_html_del_portal_falla_diciendo_que_no_es_un_zip():
    """El diagnóstico correcto: el problema es el transporte, no el JSON."""
    respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, html="<html><body>Portal CGE</body></html>")
    )

    with pytest.raises(CollectorError) as fallo:
        asyncio.run(collector().fetch())

    assert "ZIP" in str(fallo.value)
    assert "json" not in str(fallo.value).lower()


@respx.mock
def test_un_kmz_vacio_es_un_fallo_y_no_una_noche_tranquila():
    respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, content=b"")
    )

    with pytest.raises(CollectorError, match="vacío"):
        asyncio.run(collector().fetch())


@respx.mock
def test_un_kmz_sin_placemarks_avisa_pero_no_tumba_la_corrida():
    """Puede no haber cortes, o puede haber cambiado el formato. Se avisa.

    No se falla: un `failed` cada cinco minutos por una noche tranquila es ruido
    que enseña a ignorar la traza.
    """
    respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, content=build_kmz(build_kml()))
    )

    instancia = collector()
    cortes = asyncio.run(instancia.fetch())

    assert cortes == []
    assert any("formato" in aviso for aviso in instancia.warnings)


@respx.mock
def test_se_pide_el_binario_y_no_la_pagina():
    """El `Accept` evita que el CDN responda el HTML del portal."""
    ruta = respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(200, content=build_kmz(build_kml(placemark())))
    )

    asyncio.run(collector().fetch())

    enviado = ruta.calls[0].request.headers["accept"]
    assert "kmz" in enviado
    assert "User-Agent" in ruta.calls[0].request.headers


@respx.mock
def test_un_404_no_se_reintenta_y_sale_como_collector_error():
    """Se conserva el transporte del proyecto: 4xx es contrato roto, no red caída."""
    ruta = respx.route(url__startswith="https://cge.test").mock(
        return_value=httpx.Response(404, text="no existe")
    )

    with pytest.raises(CollectorError, match="404"):
        asyncio.run(collector().fetch())

    assert ruta.call_count == 1, "un 4xx no se reintenta"


# --- El orquestador ----------------------------------------------------------


def test_el_orquestador_carga_los_dos_workers_de_la_capa_electrica():
    """CGE vuelve a rotación: el orquestador la ejecuta cada cinco minutos.

    Estuvo fuera por dos motivos y los dos se levantaron. El primero, que su URL
    devolvía el HTML del visor: resultó que CGE no tiene API y el dato vive en un
    KMZ, y `CGE_API_URL` ahora apunta a ese archivo. El segundo, que el camino
    KMZ estaba escrito y sin verificar: este módulo es esa verificación.

    Lo que estos tests **no** pueden decir es si el archivo que CGE sirve hoy
    tiene la forma que suponen —están construidos a mano contra la especificación
    de KML porque el real no se pudo descargar—. Por eso importa tanto cómo
    falla: los tests de arriba fijan que un formato inesperado deje una corrida
    `failed` con el diagnóstico incluido, y no cero cortes en silencio.
    Registrada y fallando a la vista se arregla; apagada, se olvida.

    Con esto la periferia de la V Región —valle del Aconcagua, litoral y
    sectores rurales— recupera capa de cortes.
    """
    from app.collectors.registry import available_collectors, collector_class

    disponibles = available_collectors()
    assert "chilquinta_cortes" in disponibles
    assert "cge_cortes" in disponibles
    assert collector_class("cge_cortes") is CgeCollector


def test_las_dos_electricas_comparten_cadencia():
    """Cinco minutos: un corte cambia de estado varias veces durante el evento."""
    from app.collectors.power.chilquinta_worker import ChilquintaCollector

    assert CgeCollector.poll_interval_seconds() == 300
    assert ChilquintaCollector.poll_interval_seconds() == 300


def test_la_url_configurada_apunta_al_kmz_y_no_al_visor():
    """La raíz `/afectaciones/` devuelve el HTML del portal. El dato está en el archivo."""
    assert settings.CGE_API_URL.endswith(".kmz")
    assert settings.CGE_API_URL == (
        "https://mapa-afectaciones.grupocge.cl/afectaciones/mapa_cge.kmz"
    )
