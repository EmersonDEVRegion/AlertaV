"""Collector del CSN: parseo de la tabla y manejo de la hora.

El HTML de este archivo es una copia literal de filas del catálogo real del
19 de agosto de 2026 (`sismologia.cl/sismicidad/catalogo/2026/08/20260819.html`),
con la estructura de columnas tal cual la publica el CSN. Testear un scraper
contra HTML inventado sólo verifica que el parser entiende lo que uno imaginó.

La zona horaria tiene su propia sección porque es donde este collector puede
fallar de forma silenciosa: un sismo desplazado una hora sigue pareciendo un
sismo válido, y sólo se nota cuando alguien compara con otra fuente.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.collectors.seismic.csn_parser import (
    CHILE_TZ,
    CsnQuake,
    catalog_path,
    page_looks_broken,
    parse_catalog,
    parse_coordinates,
    parse_depth_km,
    parse_local_datetime,
    parse_magnitude,
    parse_utc_datetime,
    recent_catalog_paths,
)
from app.collectors.seismic.sismologia_worker import (
    CSN_CONFIDENCE,
    SEISMIC_KEY,
    SismologiaCollector,
    build_external_id,
    build_text,
    seismic_row,
)
from app.core.config import settings
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EventSource,
    EventType,
)

#: Filas reales del catálogo del CSN del 19 de agosto de 2026, copiadas tal cual.
#:
#: La selección no es arbitraria: **dos caen dentro** del recorte espacial de la
#: capa sísmica (lat −35…−31, lon −73…−69) y **dos caen fuera**, y la última
#: cruza la medianoche local — su fecha local es del 18 y su fecha UTC del 19.
#: Esa mezcla es la que permite verificar el parseo, el recorte y el manejo de
#: fechas con un solo fixture, sin inventar datos que el CSN nunca publicó.
HTML_CATALOGO = """
<html><body>
<table>
<tr><th>Fecha Local / Lugar</th><th>Fecha UTC</th><th>Latitud / Longitud</th>
    <th>Profundidad</th><th>Magnitud</th></tr>
<tr>
  <td><a href="/sismicidad/informes/2026/08/379862.html">2026-08-19 15:05:45</a>
      29 km al S de Pichidangui</td>
  <td>2026-08-19 19:05:45</td>
  <td>-32.390    -71.611</td>
  <td>35 km</td>
  <td>2.5 Mlv</td>
</tr>
<tr>
  <td><a href="/sismicidad/informes/2026/08/379858.html">2026-08-19 14:29:26</a>
      10 km al SO de Pichilemu</td>
  <td>2026-08-19 18:29:26</td>
  <td>-34.459    -72.078</td>
  <td>19 km</td>
  <td>2.5 Mlv</td>
</tr>
<tr>
  <td><a href="/sismicidad/informes/2026/08/379847.html">2026-08-19 13:25:05</a>
      6 km al NE de La Serena</td>
  <td>2026-08-19 17:25:05</td>
  <td>-29.849    -71.229</td>
  <td>51 km</td>
  <td>4.2 Mlv</td>
</tr>
<tr>
  <td><a href="/sismicidad/informes/2026/08/379792.html">2026-08-18 23:10:41</a>
      69 km al SE de Socaire</td>
  <td>2026-08-19 03:10:41</td>
  <td>-24.067    -67.452</td>
  <td>237 km</td>
  <td>3.2 Mlv</td>
</tr>
</table></body></html>
"""

#: Los dos que sobreviven al recorte espacial, en el orden en que aparecen.
DENTRO_DE_LA_CAJA = ("379862", "379858")


def collector() -> SismologiaCollector:
    """Instancia sin `__init__`: no toca sesión ni red."""
    instancia = SismologiaCollector.__new__(SismologiaCollector)
    instancia.bbox = settings.csn_bbox
    instancia.min_magnitude = 0.0
    return instancia


# --- Parseo de la tabla ------------------------------------------------------


def test_parsea_las_cuatro_filas_reales():
    sismos = parse_catalog(HTML_CATALOGO, base_url="https://www.sismologia.cl")

    assert [s.csn_id for s in sismos] == ["379862", "379858", "379847", "379792"]
    assert [s.magnitude for s in sismos] == [2.5, 2.5, 4.2, 3.2]
    assert [s.depth_km for s in sismos] == [35.0, 19.0, 51.0, 237.0]


def test_la_cabecera_no_entra_como_sismo():
    """Las `<th>` tienen cinco celdas igual que un sismo. El filtro es el enlace."""
    sismos = parse_catalog(HTML_CATALOGO)
    assert all(s.csn_id.isdigit() for s in sismos)


def test_extrae_el_identificador_del_enlace_al_informe():
    """Es lo único estable que publica la página.

    El CSN revisa magnitud y profundidad horas después del sismo, así que un
    hash de los atributos crearía un evento nuevo en cada revisión. El id del
    informe sobrevive a esas correcciones.
    """
    sismo = parse_catalog(HTML_CATALOGO)[0]
    assert sismo.csn_id == "379862"
    assert build_external_id(sismo) == "csn:379862"


def test_el_id_externo_no_choca_con_el_del_usgs():
    """Ambas redes conviven en `raw_events`; el namespace las separa."""
    sismo = parse_catalog(HTML_CATALOGO)[0]
    assert build_external_id(sismo).startswith("csn:")


def test_extrae_el_lugar_sin_la_fecha():
    sismo = parse_catalog(HTML_CATALOGO)[0]
    assert sismo.place == "29 km al S de Pichidangui"
    assert "2026" not in (sismo.place or "")


def test_arma_la_url_absoluta_del_informe():
    sismo = parse_catalog(HTML_CATALOGO, base_url="https://www.sismologia.cl")[0]
    assert sismo.report_url == (
        "https://www.sismologia.cl/sismicidad/informes/2026/08/379862.html"
    )


@pytest.mark.parametrize(
    ("celda", "esperado"),
    [
        ("-32.390    -71.611", (-32.390, -71.611)),
        ("-21.074 -69.015", (-21.074, -69.015)),
        ("-44.193\n-74.111", (-44.193, -74.111)),
        ("", None),
        ("-32.390", None),  # falta la longitud
        ("109 km", None),  # es la celda de profundidad, no la de coordenadas
    ],
)
def test_parse_coordinates(celda, esperado):
    assert parse_coordinates(celda) == esperado


def test_las_coordenadas_fuera_de_chile_delatan_una_celda_mal_leida():
    """No es un filtro de negocio: es detección de parseo roto.

    Si la tabla cambia de columnas, lo que llegue acá no será una coordenada
    lejana sino otra cosa. Rechazarlo evita depositar sismos en el Atlántico.
    """
    assert parse_coordinates("40.712 -74.006") is None  # Nueva York
    assert parse_coordinates("-33.045 -71.620") is not None  # Valparaíso


@pytest.mark.parametrize(
    ("celda", "valor", "escala"),
    [
        ("2.5 Mlv", 2.5, "Mlv"),
        ("6.1 Mww", 6.1, "Mww"),
        ("4.0 Ml", 4.0, "Ml"),
        ("3.2", 3.2, None),
        ("", None, None),
        ("sin dato", None, None),
    ],
)
def test_parse_magnitude_conserva_la_escala(celda, valor, escala):
    """Ml y Mw no son comparables: para un sismo grande la local satura.

    Guardar sólo el número perdería la información que permite saber si dos
    cifras distintas son un desacuerdo o dos formas de medir.
    """
    assert parse_magnitude(celda) == (valor, escala)


@pytest.mark.parametrize(
    ("celda", "esperado"),
    [("109 km", 109.0), ("5 km", 5.0), ("109.5 km", 109.5), ("", None), ("—", None)],
)
def test_parse_depth_km(celda, esperado):
    assert parse_depth_km(celda) == esperado


# --- Zona horaria: la parte crítica ------------------------------------------


def test_se_prefiere_la_columna_utc_que_la_pagina_ya_publica():
    """La decisión que elimina el problema en vez de resolverlo.

    El CSN publica ambas horas. Tomar la que ya viene en UTC deja fuera de juego
    el horario de verano, la hora ambigua del cambio de reloj y los decretos que
    en Chile han movido las fechas de transición varias veces.
    """
    sismos = parse_catalog(HTML_CATALOGO)

    assert all(s.time_source == "utc_column" for s in sismos)
    assert sismos[0].time == datetime(2026, 8, 19, 19, 5, 45, tzinfo=UTC)


def test_todas_las_horas_salen_con_tzinfo():
    """Un naive datetime aguas abajo se interpretaría como UTC en silencio."""
    for sismo in parse_catalog(HTML_CATALOGO):
        assert sismo.time.tzinfo is not None
        assert sismo.time.utcoffset().total_seconds() == 0


def test_la_fila_que_cruza_la_medianoche_local_conserva_su_dia_utc():
    """Local 2026-08-18 23:10 es UTC 2026-08-19 03:10: cambia de día.

    Es el caso donde un parser que reconstruyera la fecha desde el nombre del
    archivo, en vez de leer la celda, se equivocaría de día entero.
    """
    socaire = parse_catalog(HTML_CATALOGO)[3]
    assert socaire.time == datetime(2026, 8, 19, 3, 10, 41, tzinfo=UTC)


@pytest.mark.parametrize(
    ("local", "utc_esperado", "estacion"),
    [
        # Invierno austral: UTC-4.
        ("2026-08-19 19:33:16", datetime(2026, 8, 19, 23, 33, 16, tzinfo=UTC), "invierno"),
        # Verano austral: UTC-3. Un desplazamiento fijo de -4 fallaría acá.
        ("2026-01-15 19:33:16", datetime(2026, 1, 15, 22, 33, 16, tzinfo=UTC), "verano"),
    ],
)
def test_el_respaldo_local_respeta_el_horario_de_verano(local, utc_esperado, estacion):
    """`zoneinfo` con America/Santiago, nunca un offset escrito a mano.

    Chile cambia la hora dos veces al año y ha movido las fechas por decreto
    varias veces. Un `-4` fijo acierta la mitad del año y desplaza los sismos una
    hora la otra mitad — y una hora es exactamente el error que hace que el
    motor los empareje con lo que no corresponde.
    """
    assert parse_local_datetime(local) == utc_esperado


def test_la_conversion_local_coincide_con_la_columna_utc():
    """Prueba cruzada sobre datos reales.

    Si el respaldo y la columna oficial dan lo mismo en una fila real, la
    conversión está bien calibrada. Es el test que detectaría un cambio de
    política horaria en Chile antes de que desplace datos.
    """
    assert parse_local_datetime("2026-08-19 15:05:45") == parse_utc_datetime(
        "2026-08-19 19:05:45"
    )


def test_el_respaldo_se_usa_solo_si_falta_la_columna_utc():
    html = """<table><tr>
      <td><a href="/sismicidad/informes/2026/08/1.html">2026-08-19 15:05:45</a> Lugar</td>
      <td>—</td><td>-32.390 -71.611</td><td>35 km</td><td>2.5 Ml</td>
    </tr></table>"""
    sismo = parse_catalog(html)[0]

    assert sismo.time_source == "local_column"
    assert sismo.time == datetime(2026, 8, 19, 19, 5, 45, tzinfo=UTC)


def test_las_rutas_del_catalogo_usan_el_dia_chileno():
    """El CSN organiza sus páginas por día local.

    A las 22:00 de Chile son las 02:00 UTC del día siguiente; pedir el catálogo
    de "hoy UTC" traería una página que aún no existe.
    """
    # 2026-08-20 02:00 UTC == 2026-08-19 22:00 en Chile.
    rutas = recent_catalog_paths(datetime(2026, 8, 20, 2, 0, tzinfo=UTC), days=2)

    assert rutas[0] == "sismicidad/catalogo/2026/08/20260819.html"
    assert rutas[1] == "sismicidad/catalogo/2026/08/20260818.html"


def test_catalog_path_usa_la_zona_chilena():
    momento = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
    assert momento.astimezone(CHILE_TZ).day == 19
    assert catalog_path(momento).endswith("20260819.html")


# --- Mapeo al dominio --------------------------------------------------------


def test_emite_earthquake_con_confianza_uno():
    eventos = collector().normalize(parse_catalog(HTML_CATALOGO))

    assert len(eventos) == 2, "sólo los dos dentro del recorte espacial"
    for evento in eventos:
        assert evento.source is EventSource.CSN
        assert evento.type is EventType.EARTHQUAKE
        assert evento.confidence == pytest.approx(CSN_CONFIDENCE) == 1.0


def test_el_sismo_sigue_fuera_del_motor_de_correlacion():
    """La garantía que el volumen del CSN hace más importante, no menos.

    Con radio de 1500 m y ventana de 4 h, DBSCAN fundiría el sismo principal con
    sus réplicas en un solo "incidente" y borraría la secuencia. El CSN publica
    enjambres completos, así que el riesgo crece con esta fuente.
    """
    assert EventType.EARTHQUAKE not in CORRELATABLE_EVENT_TYPES


def test_el_sismo_no_aporta_confianza_a_ningun_fenomeno():
    """1.0 significa "este sismo ocurrió", no "aquí hay una emergencia"."""
    from app.services.correlation.confidence import rule_for

    regla = rule_for(EventSource.CSN)
    assert regla.max_weight == 0.0
    assert regla.confirming is False


def test_magnitud_y_profundidad_viajan_planas_para_el_frontend():
    """El mapa calcula el radio del círculo con ambas.

    Van también al primer nivel de `raw_data`, no sólo dentro de `_seismic`:
    obligar al cliente a bajar a una clave con guion bajo sería exponer nuestra
    estructura interna como contrato.
    """
    evento = collector().normalize(parse_catalog(HTML_CATALOGO))[0]

    assert evento.raw_data["magnitude"] == 2.5
    assert evento.raw_data["depth_km"] == 35.0
    assert evento.raw_data["mag_type"] == "Mlv"


def test_el_detalle_sismico_queda_listo_para_after_ingest():
    evento = collector().normalize(parse_catalog(HTML_CATALOGO))[0]
    detalle = evento.raw_data[SEISMIC_KEY]

    assert detalle["provider"] == "csn"
    assert detalle["usgs_id"] == "379862"
    assert detalle["magnitude"] == 2.5
    assert detalle["depth_km"] == 35.0


def test_el_detalle_es_serializable_a_json():
    """Viaja dentro de `raw_data`, que es JSONB: nada de `datetime` ahí."""
    import json

    sismo = parse_catalog(HTML_CATALOGO)[0]
    json.dumps(seismic_row(sismo))


def test_el_recorte_espacial_descarta_lo_que_esta_lejos():
    """El CSN publica todo Chile; a este sistema le sirve la zona central.

    Un sismo en Magallanes es un hecho real y no aporta nada a un mapa de
    emergencias de Valparaíso. La caja es más ancha que `region_bbox` porque un
    sismo a 200 km sí se siente, pero no cubre el país entero.
    """
    instancia = collector()
    lejano = CsnQuake(
        csn_id="1", time=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
        lat=-53.15, lon=-70.91,  # Punta Arenas
        depth_km=10.0, magnitude=5.0, mag_type="Ml", place="Magallanes",
        report_url=None, time_source="utc_column",
    )
    assert instancia.normalize([lejano]) == []


def test_el_recorte_deja_pasar_lo_de_la_zona_central():
    eventos = collector().normalize(parse_catalog(HTML_CATALOGO))
    ids = [evento.external_id for evento in eventos]

    assert ids == [f"csn:{csn_id}" for csn_id in DENTRO_DE_LA_CAJA]


def test_el_umbral_de_magnitud_es_configurable():
    """Por defecto es 0.0 —todo lo que el CSN publique— y esa es la gracia.

    El umbral existe para poder subirlo si algún día el volumen molesta, no
    porque un microsismo sobre. Se prueba con sismos construidos a mano para no
    depender de qué magnitudes trajo el catálogo del fixture.
    """
    def sismo(csn_id: str, magnitude: float) -> CsnQuake:
        return CsnQuake(
            csn_id=csn_id, time=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
            lat=-33.0, lon=-71.5, depth_km=30.0, magnitude=magnitude,
            mag_type="Ml", place="Valparaíso", report_url=None,
            time_source="utc_column",
        )

    instancia = collector()
    entrada = [sismo("1", 2.5), sismo("2", 4.2)]

    assert len(instancia.normalize(entrada)) == 2, "por defecto entra todo"

    instancia.min_magnitude = 4.0
    filtrados = instancia.normalize(entrada)
    assert len(filtrados) == 1
    assert filtrados[0].external_id == "csn:2"


def test_el_texto_es_legible_en_español():
    sismo = parse_catalog(HTML_CATALOGO)[2]  # La Serena, M4.2
    texto = build_text(sismo)

    assert "magnitud 4.2" in texto
    assert "La Serena" in texto
    assert "profundidad 51 km" in texto
    assert "CSN" in texto


def test_un_sismo_sin_magnitud_no_miente():
    sismo = CsnQuake(
        csn_id="1", time=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
        lat=-33.0, lon=-71.5, depth_km=10.0, magnitude=None, mag_type=None,
        place="Valparaíso", report_url=None, time_source="utc_column",
    )
    assert "no determinada" in build_text(sismo)


# --- Resiliencia -------------------------------------------------------------


def test_una_fila_ilegible_no_arrastra_a_las_demas():
    roto = HTML_CATALOGO.replace("-29.849    -71.229", "no son coordenadas")
    sismos = parse_catalog(roto)

    assert len(sismos) == 3, "las otras tres filas deben sobrevivir"
    assert "379847" not in [s.csn_id for s in sismos]


def test_una_fila_sin_enlace_se_descarta():
    """Sin identificador estable no hay idempotencia posible."""
    html = """<table><tr>
      <td>2026-08-19 15:05:45 Lugar</td><td>2026-08-19 19:05:45</td>
      <td>-32.390 -71.611</td><td>35 km</td><td>2.5 Ml</td>
    </tr></table>"""
    assert parse_catalog(html) == []


def test_distingue_pagina_vacia_de_pagina_rota():
    """Un `len(sismos) == 0` confunde dos cosas muy distintas."""
    sin_tabla = "<html><body><p>Servicio no disponible</p></body></html>"
    rota, motivo = page_looks_broken(sin_tabla)
    assert rota is True
    assert motivo

    rota, _ = page_looks_broken(HTML_CATALOGO)
    assert rota is False


def test_no_duplica_sismos_repetidos_entre_catalogos():
    """Dos días consecutivos se solapan en la frontera horaria."""
    doble = HTML_CATALOGO + HTML_CATALOGO
    assert len(parse_catalog(doble)) == 4
