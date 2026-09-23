"""Feed de vehículos de GBV: parser, collector, aislamiento y endpoint.

El sesgo de estos tests es el de todo el repositorio: fijar las decisiones que,
de invertirse, NO producirían un error visible. Una regex floja "encuentra"
patentes en `DESDE` y `6784`; una clave por patente sin estado esconde las
recuperaciones del feed; un `vehicle_report` que entrara al motor abriría
incidentes perfectamente plausibles; una grilla vacía se leería como "hoy no
robaron autos". Nada de eso revienta: por eso se sujeta acá.

El HTML es el del sitio real, capturado el 2026-09-23 y recortado a unas pocas
tarjetas. Las clases, los enlaces relativos y los espacios dobles del título de
los recuperados son los originales: son justamente lo que el parser lee.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.collectors.vehicles.gbv_parser import (
    ABANDONADOS,
    CHILE_TZ,
    DENUNCIAS,
    PATENTE_AUTO_CL,
    PATENTE_CL,
    RECUPERADOS,
    FichaListado,
    build_text,
    consolidar,
    hay_pagina_siguiente,
    normalizar_patente,
    parse_anio,
    parse_detalle,
    parse_fecha,
    parse_listado,
    patente_valida,
    ubicar,
    url_de_listado,
)
from app.collectors.vehicles.gbv_worker import (
    MAX_FALLOS_SEGUIDOS,
    AvisoGbv,
    GbvCollector,
    build_event,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    EVENT_TO_INCIDENT_TYPE,
    SOURCE_BASE_CONFIDENCE,
    EventSource,
    EventType,
    VehicleLocation,
    VehicleStatus,
)
from app.schemas.event import EventCreate

BASE = "https://gbvspa.cl"
URL_DENUNCIAS = f"{BASE}/denuncias/"
URL_RECUPERADOS = f"{BASE}/vehiculos-recuperados"
URL_ABANDONADOS = f"{BASE}/vehiculos-abandonados/"
#: En el pasado a propósito: `EventCreate` rechaza timestamps futuros contra el
#: reloj real, y un test anclado a "hoy" se rompería según la hora de la corrida.
HOY = date(2026, 9, 22)
VISTO_EN = datetime(2026, 9, 22, 15, 0, tzinfo=UTC)


# --- HTML real, recortado ----------------------------------------------------


def _tarjeta_denuncia(slug: str, img: str, lugar: str, prefijo: str = "../") -> str:
    return f"""<div class="base_float boton_De redondo_A">
<a href="{prefijo}denuncias/{slug}">
<div class="base_float c_img_De padding_A redondo_A">
<img class="sombra_A" src="{prefijo}administracion/_imagen11133/ftsymgns/denuncia_img/ancho_345/{img}">
</div>
<div class="base_float titulo_MINI  mayusculas">{lugar}</div>
</a>
<div class="base_float margin_top_B c_mas_detalles">
<a href="{prefijo}denuncias/{slug}">
<div class="base_float boton_C texto_centro margin_top_B">MÁS DETALLES</div>
</a>
</div>
</div>"""


def _pagina(titulo: str, tarjetas: str, paginador: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>{titulo}</title></head><body><div class="cuerpo base_float">
<div class="base_float  redondo_B padding_B">
<div class="base_float margin_bottom_B c_mini_menu"><a class="base_float_sin_100 boton_A fondo_Z margin_bottom_A" href="../"><img class="img_mini_menu" src="../imagenes/iconos/casaA.svg"></a><a class="base_float_sin_100 boton_A fondo_Z margin_bottom_A" href="../denuncias/">denuncias</a></div>
<div class="grid_A base_float">
{tarjetas}
</div>
{paginador}
</div>
</div></body></html>"""


PAGINADOR = """<div class="base_float_sin_100_r">
<div class="base_float_sin_100 padding_A boton_pag redondo_50 boton_pag_activa">
					1
				</div>
<a href="../denuncias/?pag=2">
<div class="base_float_sin_100 padding_A boton_pag redondo_50">
					2
				</div>
</a>
<a href="../denuncias/?pag=33">
<div class="base_float_sin_100 padding_A boton_pag redondo_50">
					33
				</div>
</a>
</div>"""

DENUNCIAS_HTML = _pagina(
    "Denuncias Vehículos Robados - GBVSPA",
    "\n".join(
        [
            _tarjeta_denuncia(
                "auto-robo-desde-via-publica-lkxv55-6784",
                "doc9_6ab35bf67fd6b2_51030289.jpg",
                "los araucanos 290 ",
            ),
            _tarjeta_denuncia(
                "auto-robo-desde-via-publica-szsy51-6783",
                "doc9_6ab3393d8447f3_37567616.jpg",
                "Reñaca",
            ),
            _tarjeta_denuncia(
                "camioneta-robo-desde-via-publica-yw8869-6782",
                "doc9_6ab2a363dc7799_51010760.jpg",
                "Estación belloto Quilpue",
            ),
            _tarjeta_denuncia(
                "carro-hurto-vbgy34-6781", "doc9_6ab1cd9b55aeb4_17675017.png", "Mall maipu "
            ),
            _tarjeta_denuncia(
                "auto-robo-desde-via-publica-yw8869-6780",
                "doc9_6ab198c0cfeb78_04696612.jpg",
                "Estación el belloto",
            ),
            _tarjeta_denuncia(
                "auto-robo-desde-via-publica-cshz796-6743",
                "doc9_6aa6d5fb460690_63918400.jpg",
                "Puente Alto ",
            ),
            _tarjeta_denuncia(
                "camioneta-violencia-tcyd68-6582",
                "doc9_6a7b3133b392f3_71317094.jpg",
                "Iquique ",
            ),
        ]
    ),
    PAGINADOR,
)


def _tarjeta_recuperado(slug: str, img: str, titulo: str) -> str:
    return f"""<div class="base_float boton_De redondo_A">
                    <a href="./denuncias/{slug}">
                        <div class="base_float c_img_De padding_A redondo_A">
                            <img class="sombra_A" src="./administracion/_imagen11133/ftsymgns/denuncia_img/ancho_345/{img}">
                        </div>
                        <div class="base_float titulo_MINI  mayusculas">{titulo}</div>
                    </a>
                    <div class="base_float margin_top_B c_mas_detalles">
                            <a href="./denuncias/{slug}">
                            <div class="base_float boton_C texto_centro margin_top_B">VER MÁS</div>
                            </a>
                    </div>
                </div>"""


RECUPERADOS_HTML = _pagina(
    "Vehiculos recuperados - GBVSPA",
    "\n".join(
        [
            _tarjeta_recuperado(
                "auto-robo-desde-via-publica-sgth85-6647",
                "doc9_6a8b273cb5dbf6_36020193.jpg",
                "Auto Changan  Uní T  ",
            ),
            _tarjeta_recuperado(
                "auto-robo-desde-via-publica-frdt33-6610",
                "doc9_6a83e1ae0a5922_22354033.jpg",
                "Auto Chevrolet  Aveo ",
            ),
            _tarjeta_recuperado(
                "auto-robo-desde-via-publica-frdt33-6609",
                "doc9_6a83e1ae0a5922_22354033.jpg",
                "Auto Chevrolet  Aveo ",
            ),
        ]
    ),
)


def _tarjeta_abandonado(slug: str, img: str, lugar: str, tiempo: str, vehiculo: str) -> str:
    return f"""<div class="base_float boton_De redondo_A">
					<a href="../vehiculos-abandonados/{slug}">
						<div class="base_float c_img_De padding_A redondo_A">
							<img class="sombra_A" src="../administracion/_imagen11133/ftsymgns/denuncia_va_img/ancho_345/{img}">
						</div>

						<div class="base_float padding_A">
							<div class="base_float titulo_MINI  mayusculas">{lugar}</div>

							<table class="table_caracteristicas base_float">
		<tbody><tr>
			<td class="td_caracteristicas negrita padding_A mayusculas">tiempo_abandono</td>
			<td class="padding_A">{tiempo}</td>
		</tr>
		<tr class="fondo_T">
			<td class="td_caracteristicas negrita padding_A mayusculas">vehiculo</td>
			<td class="padding_A">{vehiculo}</td>
		</tr></tbody></table>
						</div>
					</a>

					<div class="base_float margin_top_B c_mas_detalles">
							<a href="../vehiculos-abandonados/{slug}">
							<div class="base_float boton_C texto_centro margin_top_B">MÁS DETALLES</div>
							</a>
					</div>
				</div>"""


ABANDONADOS_HTML = _pagina(
    "Vehiculos abandonados - GBVSPA",
    "\n".join(
        [
            _tarjeta_abandonado(
                "vehiculo-abandonado-auto-franklin-1423-3-semanas-89",
                "doc9_6aac28ba6bc028_74350455.jpg",
                "Franklin 1423",
                "3 semanas",
                "Auto",
            ),
            _tarjeta_abandonado(
                "vehiculo-abandonado-camioneta-valparaiso-a-un-costado-de-la-corte-de-apelaciones-4-dias-88",
                "doc9_6a9c47d9a71978_18073118.jpg",
                "Valparaíso, a un costado de la corte de apelaciones.",
                "4 días ",
                "Camioneta",
            ),
        ]
    ),
)


def _tabla(filas: list[tuple[str, str]]) -> str:
    cuerpo = "\n".join(
        f"""<tr>
			<td class="td_caracteristicas negrita padding_A mayusculas">{etiqueta}</td>
			<td class="padding_A">{valor}</td>
		</tr>"""
        for etiqueta, valor in filas
    )
    return f'<table class="table_caracteristicas base_float">\n\t\t<tbody>{cuerpo}</tbody></table>'


def _detalle(titulo: str, filas: list[tuple[str, str]]) -> str:
    return (
        f"<!DOCTYPE html><html><head><title>{titulo}</title></head><body>"
        f'<div class="base_float padding_A">{_tabla(filas)}</div></body></html>'
    )


DETALLE_LKXV55 = _detalle(
    "Auto-Robo Desde Vía Publica-LKXV55-6784 - Se busca Auto - Delito: Robo desde vía publica - Lugar del delito: los araucanos 290",
    [
        ("vehiculo", "Auto"),
        ("marca", "Toyota"),
        ("modelo", "Rav4"),
        ("color", "Negro Mica"),
        ("ano", "2019"),
        ("lugar de delito", "los araucanos 290 "),
        ("fecha de delito", "2026-09-22"),
        ("delito", "Robo desde vía publica"),
    ],
)

DETALLE_SGTH85 = _detalle(
    "Auto-Robo Desde Vía Publica-SGTH85-6647 - RecuperadoAuto - Delito: Robo desde vía publica - Lugar del delito: Avenida Matta",
    [
        ("recuperado en", "Viña del mar"),
        ("autoridad presente", "Carabineros"),
        ("modelo", "Uní T"),
        ("lugar de delito", "Avenida Matta"),
        ("fecha de delito", "2026-08-21"),
        ("delito", "Robo desde vía publica"),
    ],
)

DETALLE_ABANDONADO_89 = _detalle(
    "VEHICULO ABANDONADO - Auto Franklin 1423 3 semanas 89",
    [
        ("ubicacion", "Franklin 1423"),
        ("tiempo_abandono", "3 semanas"),
        ("patente", "Bwph17"),
        ("vehiculo", "Auto"),
        ("marca", "Geely"),
        ("modelo", "Mk15"),
        ("color", "Plateado"),
    ],
)

DETALLE_GENERICO = _detalle(
    "Detalle",
    [
        ("vehiculo", "Auto"),
        ("marca", "Kia"),
        ("modelo", "Morning"),
        ("fecha de delito", "2026-09-20"),
    ],
)


# --- Patentes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "patente", ["LKXV55", "SZSY51", "YW8869", "VBGY34", "TCYD68", "SGTH85", "FRDT33", "BWPH17"]
)
def test_las_patentes_reales_del_sitio_son_validas(patente):
    assert PATENTE_CL.fullmatch(patente)
    assert PATENTE_AUTO_CL.fullmatch(patente)


@pytest.mark.parametrize(
    ("valor", "esperada"),
    [
        ("lkxv55", "LKXV55"),
        ("BB·BB·10", "BBBB10"),
        ("ab-1234", "AB1234"),
        ("  yw 8869 ", "YW8869"),
    ],
)
def test_normaliza_mayusculas_y_separadores(valor, esperada):
    assert normalizar_patente(valor) == esperada
    assert patente_valida(valor) == esperada


@pytest.mark.parametrize(
    "valor",
    [
        # Vocal en la serie 2007+: el ejemplo de la especificación, que no existe.
        "ABCD12",
        # Cero inicial: el formato 2007+ numera de 10 a 99 y el antiguo desde 1000.
        "BCDF09",
        "AB0012",
        # Vocal en la serie 2007+ y letras con vocales + 3 dígitos: typos reales.
        "SOFC13",
        "CEA891",
        # Mal escritas en origen, tal cual aparecen en GBV.
        "cshz796",
        "pdsl30k",
        # Lo que la regex floja `[A-Z0-9]{4,6}` encontraba en el enlace.
        "AUTO",
        "DESDE",
        "PUBLIC",
        "6784",
        "RUTA68",
        "",
        None,
        "Sin patente",
    ],
)
def test_rechaza_lo_que_no_es_patente(valor):
    assert patente_valida(valor) is None


def test_la_regex_floja_se_come_el_id_de_gbv_y_la_buena_no():
    enlace = "AUTO-ROBO-DESDE-VIA-PUBLICA-LKXV55-6784"
    assert "6784" in re.findall(r"[A-Z0-9]{4,6}", enlace)
    assert PATENTE_CL.findall(enlace) == ["LKXV55"]


@pytest.mark.parametrize("patente", ["RGT012", "PDC068", "SLG017", "JLP057", "AB0123"])
def test_las_motos_se_publican_con_cero_de_relleno(patente):
    """Así aparecen en GBV: `moto-robo-desde-via-publica-rgt012-6760`."""
    assert patente_valida(patente) == patente
    # Y no son autos: la regex mínima no las acepta.
    assert not PATENTE_AUTO_CL.fullmatch(patente)


def test_una_moto_sin_cero_es_la_misma_patente():
    assert patente_valida("RGT12") == "RGT012"
    assert patente_valida("AB123") == "AB0123"


def test_formatos_anunciados_por_el_mtt():
    for patente in ("BBBBB0", "BBBB0"):
        assert patente_valida(patente) == patente


@pytest.mark.parametrize(
    ("slug", "patente", "delito"),
    [
        ("auto-robo-desde-via-publica-lkxv55-6784", "LKXV55", "robo desde via publica"),
        ("moto-robo-desde-via-publica-rgt012-6760", "RGT012", "robo desde via publica"),
        # Patentes escritas con guiones: el slug los conserva.
        ("auto-robo-desde-via-publica-aj-2072-1783", "AJ2072", "robo desde via publica"),
        ("auto-robo-desde-via-publica-jc-fg-70-576", "JCFG70", "robo desde via publica"),
        ("carro-hurto-vbgy34-6781", "VBGY34", "hurto"),
        # Irrecuperables: entran por id de GBV.
        ("camioneta-apropiacion-indebida-jjws11-5-2037", None, None),
        ("auto-robo-desde-via-publica-cshz796-6743", None, "robo desde via publica"),
    ],
)
def test_la_patente_del_enlace(slug, patente, delito):
    ficha = parse_listado(
        _pagina("GBV", _tarjeta_denuncia(slug, "x.jpg", "Quilpué")),
        seccion=DENUNCIAS,
        url_pagina=URL_DENUNCIAS,
    ).fichas[0]
    assert ficha.patente == patente
    if delito is not None:
        assert ficha.delito_enlace == delito


# --- Listados ----------------------------------------------------------------


def test_lee_las_denuncias_con_patente_e_id_desde_el_enlace():
    lectura = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)

    assert lectura.tarjetas == 7
    assert lectura.descartadas == []
    primera = lectura.fichas[0]
    assert primera.id_gbv == 6784
    assert primera.patente == "LKXV55"
    assert primera.estado is VehicleStatus.ROBADO
    assert primera.tipo_vehiculo == "Auto"
    assert primera.delito_enlace == "robo desde via publica"
    assert primera.lugar == "los araucanos 290"
    # El enlace es relativo (`../denuncias/…`): se resuelve contra la página.
    assert primera.url == f"{BASE}/denuncias/auto-robo-desde-via-publica-lkxv55-6784"
    assert primera.external_id == "gbv:robado:LKXV55"
    assert [f.tipo_vehiculo for f in lectura.fichas[2:4]] == ["Camioneta", "Carro"]


def test_una_patente_mal_escrita_se_identifica_por_id_de_gbv():
    lectura = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)
    ficha = next(f for f in lectura.fichas if f.id_gbv == 6743)

    assert ficha.patente is None
    assert ficha.patente_publicada == "cshz796"
    assert ficha.external_id == "gbv:robado:id:6743"


def test_dos_denuncias_de_la_misma_patente_comparten_clave():
    """GBV publica duplicados (YW8869 en 6782 y 6780): son el mismo auto."""
    lectura = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)
    claves = [f.external_id for f in lectura.fichas if f.patente == "YW8869"]
    assert claves == ["gbv:robado:YW8869", "gbv:robado:YW8869"]


def test_el_estado_va_en_la_clave_para_que_la_recuperacion_sea_un_evento_nuevo():
    """Con una sola clave por patente el upsert pisaría la fila del robo sin
    tocar `ingested_at`, y la recuperación nunca entraría en la ventana."""
    robo = FichaListado("denuncias", VehicleStatus.ROBADO, 1, "u", patente="SGTH85")
    recuperacion = FichaListado("recuperados", VehicleStatus.RECUPERADO, 1, "u", patente="SGTH85")
    assert robo.external_id != recuperacion.external_id


def test_lee_los_recuperados_con_marca_y_modelo_del_titulo():
    lectura = parse_listado(RECUPERADOS_HTML, seccion=RECUPERADOS, url_pagina=URL_RECUPERADOS)

    ficha = lectura.fichas[0]
    assert ficha.estado is VehicleStatus.RECUPERADO
    assert (ficha.tipo_vehiculo, ficha.marca, ficha.modelo) == ("Auto", "Changan", "Uní T")
    assert ficha.lugar is None
    # `./denuncias/…` relativo a `/vehiculos-recuperados` → `/denuncias/…`.
    assert ficha.url == f"{BASE}/denuncias/auto-robo-desde-via-publica-sgth85-6647"
    assert ficha.external_id == "gbv:recuperado:SGTH85"


def test_lee_los_abandonados_sin_patente_en_el_enlace():
    lectura = parse_listado(ABANDONADOS_HTML, seccion=ABANDONADOS, url_pagina=URL_ABANDONADOS)

    ficha = lectura.fichas[1]
    assert ficha.id_gbv == 88
    assert ficha.patente is None
    assert ficha.tipo_vehiculo == "Camioneta"
    assert ficha.tiempo_abandono == "4 días"
    assert ficha.lugar == "Valparaíso, a un costado de la corte de apelaciones"
    assert ficha.external_id == "gbv:abandonado:id:88"


def test_sin_grilla_es_un_error_y_no_cero_vehiculos():
    html = "<html><head><title>Mantención</title></head><body>Volvemos pronto</body></html>"
    with pytest.raises(CollectorError, match="Mantención"):
        parse_listado(html, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)


def test_una_grilla_vacia_tambien_es_un_error():
    with pytest.raises(CollectorError, match="vacía"):
        parse_listado(_pagina("GBV", ""), seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)


def test_una_tarjeta_ajena_se_descarta_sin_tumbar_la_pagina():
    html = _pagina(
        "GBV",
        '<div><a href="../servicios/gps">GPS</a></div>'
        + _tarjeta_denuncia("auto-hurto-lkxv55-1", "x.jpg", "Quilpué"),
    )
    lectura = parse_listado(html, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)
    assert len(lectura.fichas) == 1
    assert len(lectura.descartadas) == 1


def test_paginador():
    assert hay_pagina_siguiente(DENUNCIAS_HTML, 1)
    assert not hay_pagina_siguiente(DENUNCIAS_HTML, 2)
    assert url_de_listado(BASE, DENUNCIAS, 1) == URL_DENUNCIAS
    assert url_de_listado(BASE, DENUNCIAS, 2) == f"{URL_DENUNCIAS}?pag=2"
    assert url_de_listado(BASE, RECUPERADOS, 2) == URL_RECUPERADOS


# --- Detalle y consolidación -------------------------------------------------


def test_lee_la_tabla_del_detalle():
    campos = parse_detalle(DETALLE_LKXV55)
    assert campos["marca"] == "Toyota"
    assert campos["fecha de delito"] == "2026-09-22"
    # `tiempo_abandono` se normaliza a `tiempo abandono`.
    assert "tiempo abandono" in parse_detalle(DETALLE_ABANDONADO_89)


def test_un_detalle_sin_tabla_es_un_error():
    with pytest.raises(CollectorError, match="tabla"):
        parse_detalle("<html><head><title>404</title></head><body></body></html>")


def test_consolida_un_robo_con_su_detalle():
    ficha = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS).fichas[0]
    vehiculo = consolidar(ficha, parse_detalle(DETALLE_LKXV55), hoy=HOY)

    assert (vehiculo.marca, vehiculo.modelo, vehiculo.color) == ("Toyota", "Rav4", "Negro Mica")
    assert vehiculo.anio == 2019
    assert vehiculo.fecha_delito == date(2026, 9, 22)
    assert vehiculo.delito == "Robo desde vía publica"
    assert vehiculo.patente == "LKXV55"
    assert vehiculo.con_detalle


def test_un_recuperado_combina_tarjeta_y_detalle():
    """El detalle de un recuperado no trae marca: la aporta la tarjeta."""
    ficha = parse_listado(RECUPERADOS_HTML, seccion=RECUPERADOS, url_pagina=URL_RECUPERADOS).fichas[
        0
    ]
    vehiculo = consolidar(ficha, parse_detalle(DETALLE_SGTH85), hoy=HOY)

    assert vehiculo.marca == "Changan"
    assert vehiculo.modelo == "Uní T"
    assert vehiculo.recuperado_en == "Viña del mar"
    assert vehiculo.autoridad == "Carabineros"


def test_en_abandonados_la_patente_sale_del_detalle():
    fichas = parse_listado(ABANDONADOS_HTML, seccion=ABANDONADOS, url_pagina=URL_ABANDONADOS).fichas
    con_patente = consolidar(fichas[0], parse_detalle(DETALLE_ABANDONADO_89), hoy=HOY)
    sin_patente = consolidar(
        fichas[1], {"patente": "Sin patente", "vehiculo": "Camioneta"}, hoy=HOY
    )

    assert con_patente.patente == "BWPH17"
    assert (con_patente.marca, con_patente.color) == ("Geely", "Plateado")
    assert sin_patente.patente is None
    assert sin_patente.patente_publicada is None


def test_una_fecha_futura_se_descarta_y_se_marca():
    ficha = FichaListado("denuncias", VehicleStatus.ROBADO, 1, "u", patente="LKXV55")
    vehiculo = consolidar(ficha, {"fecha de delito": "2027-01-01"}, hoy=HOY)
    assert vehiculo.fecha_delito is None
    assert vehiculo.fecha_futura_descartada


@pytest.mark.parametrize(
    ("valor", "esperada"),
    [
        ("2026-09-22", date(2026, 9, 22)),
        ("22-09-2026", date(2026, 9, 22)),
        ("ayer", None),
        ("", None),
    ],
)
def test_fechas(valor, esperada):
    assert parse_fecha(valor) == esperada


def test_anios_implausibles_no_pasan():
    assert parse_anio("2019", hoy=HOY) == 2019
    assert parse_anio("1850", hoy=HOY) is None
    assert parse_anio("2030", hoy=HOY) is None
    assert parse_anio("dos mil", hoy=HOY) is None


# --- Dónde: sin Nominatim ----------------------------------------------------


@pytest.mark.parametrize(
    ("lugar", "ubicacion", "comuna"),
    [
        ("Reñaca", VehicleLocation.V_REGION, "Viña del Mar"),
        ("Estación belloto Quilpue", VehicleLocation.V_REGION, "Quilpué"),
        ("Estación el belloto", VehicleLocation.V_REGION, "Quilpué"),
        ("Afueras del liceo olmue", VehicleLocation.V_REGION, "Olmué"),
        (
            "Valparaíso, a un costado de la corte de apelaciones.",
            VehicleLocation.V_REGION,
            "Valparaíso",
        ),
        # "Santiago" en medio del texto es una calle, no la comuna.
        ("Calle Santiago Diaz 338, Rocuant, Valparaíso", VehicleLocation.V_REGION, "Valparaíso"),
        ("Mall maipu", VehicleLocation.OTRA, None),
        ("Blanco encalada Maipu", VehicleLocation.OTRA, None),
        ("Puente Alto", VehicleLocation.OTRA, None),
        ("Iquique", VehicleLocation.OTRA, None),
        (
            "MALL PLAZA OESTE, ANDENES DE DESCARGA, LADO DE PARIS, SANTIAGO",
            VehicleLocation.OTRA,
            None,
        ),
        ("los araucanos 290", VehicleLocation.SIN_UBICAR, None),
        ("Jonh kennedy 425", VehicleLocation.SIN_UBICAR, None),
        # Independencia es comuna de la RM y avenida de Valparaíso: no oculta nada.
        ("Av. Independencia 1500", VehicleLocation.SIN_UBICAR, None),
        # "V Región de Valparaíso" es la región, no la comuna.
        ("Belgrano 1143, Quilpué, V Region de Valparaíso", VehicleLocation.V_REGION, "Quilpué"),
        ("Pasaje 1 comuna panquehue 5ta region", VehicleLocation.V_REGION, "Panquehue"),
        # Con dos comunas, gana la que no es Valparaíso: al final suele ser la región.
        (
            "Tunel del Cristo Redentor, Ruta 60 CH, Los Andes, Valparaiso",
            VehicleLocation.V_REGION,
            "Los Andes",
        ),
        # Otra región dicha explícitamente desmiente la coincidencia de nombre.
        (
            "Calle Lautaro pasado Santo Domingo, las compañías, IV región",
            VehicleLocation.OTRA,
            None,
        ),
        ("Sta. Margarita 1667, San Bernardo, Región Metropolitana", VehicleLocation.OTRA, None),
        # La grilla numerada es de Viña; "Ruta 5 Norte" es la Panamericana.
        ("15 norte con 3 oriente", VehicleLocation.V_REGION, "Viña del Mar"),
        ("Frente a la carcel de Antofagasta, ruta 5 Norte", VehicleLocation.OTRA, None),
        ("Placilla peñuelas", VehicleLocation.V_REGION, "Valparaíso"),
        ("Maule 459 Santiago centro", VehicleLocation.OTRA, None),
        # Palabra completa: "Olmué" no puede salir de "olmuesito".
        ("Pasaje olmuesito 12", VehicleLocation.SIN_UBICAR, None),
        # Comunas que son nombre de calle.
        ("Los nogales 2908", VehicleLocation.SIN_UBICAR, None),
        ("Calle Quillota 250, Viña del Mar", VehicleLocation.V_REGION, "Viña del Mar"),
        ("Av. Valparaíso 1020, viña", VehicleLocation.V_REGION, "Viña del Mar"),
    ],
)
def test_ubica_sin_geocodificar(lugar, ubicacion, comuna):
    assert ubicar([lugar]) == (ubicacion, comuna)


def test_un_recuperado_se_ubica_primero_por_donde_se_recupero():
    assert ubicar(["Viña del mar", "Avenida Matta"]) == (VehicleLocation.V_REGION, "Viña del Mar")


def test_la_v_region_gana_sobre_otra_region():
    assert ubicar(["Maipu", "Quilpué"])[0] is VehicleLocation.V_REGION


# --- El evento ---------------------------------------------------------------


def _vehiculo_robado():
    ficha = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS).fichas[0]
    return ficha, consolidar(ficha, parse_detalle(DETALLE_LKXV55), hoy=HOY)


def test_el_evento_no_tiene_coordenadas_y_eventcreate_lo_admite():
    """Entregable 4: `EventCreate` no necesita cambios para eventos sin punto."""
    ficha, vehiculo = _vehiculo_robado()
    evento = build_event(
        vehiculo,
        external_id=ficha.external_id,
        semilla=False,
        visto_en=VISTO_EN,
        ficha_raw=ficha.as_raw(),
        detalle_raw=None,
    )

    assert isinstance(evento, EventCreate)
    assert evento.lat is None and evento.lon is None
    assert evento.has_location is False
    assert evento.text
    assert evento.source is EventSource.GBV
    assert evento.type is EventType.VEHICLE_REPORT
    assert evento.confidence == 0.0
    assert evento.external_id == "gbv:robado:LKXV55"


def test_el_raw_data_trae_lo_que_el_feed_filtra_y_nunca_extraction():
    ficha, vehiculo = _vehiculo_robado()
    evento = build_event(
        vehiculo,
        external_id=ficha.external_id,
        semilla=True,
        visto_en=VISTO_EN,
        ficha_raw=ficha.as_raw(),
        detalle_raw={"marca": "Toyota"},
    )
    gbv = evento.raw_data["gbv"]

    assert gbv["estado"] == "robado"
    # "los araucanos 290": no hay cómo saber la comuna sin geocodificar.
    assert gbv["region"] == "sin_ubicar"
    assert gbv["comuna"] is None
    assert gbv["semilla"] is True
    assert gbv["seccion"] == "denuncias"
    # El vínculo por sector del motor busca `_extraction.sector_clave`.
    assert "_extraction" not in evento.raw_data
    assert evento.raw_data["fuente"]["detalle"] == {"marca": "Toyota"}


def test_un_robo_con_fecha_usa_la_medianoche_chilena_de_ese_dia():
    ficha, vehiculo = _vehiculo_robado()
    evento = build_event(
        vehiculo, external_id="x", semilla=False, visto_en=VISTO_EN, ficha_raw={}, detalle_raw=None
    )
    assert evento.timestamp == datetime(2026, 9, 22, tzinfo=CHILE_TZ).astimezone(UTC)
    assert evento.raw_data["gbv"]["fecha_precision"] == "dia"


def test_un_recuperado_usa_la_hora_de_deteccion():
    """La fecha de su detalle es la del ROBO, no la de la recuperación."""
    ficha = parse_listado(RECUPERADOS_HTML, seccion=RECUPERADOS, url_pagina=URL_RECUPERADOS).fichas[
        0
    ]
    vehiculo = consolidar(ficha, parse_detalle(DETALLE_SGTH85), hoy=HOY)
    evento = build_event(
        vehiculo, external_id="x", semilla=False, visto_en=VISTO_EN, ficha_raw={}, detalle_raw=None
    )
    assert evento.timestamp == VISTO_EN
    assert evento.raw_data["gbv"]["fecha_precision"] == "deteccion"
    assert evento.raw_data["gbv"]["fecha_delito"] == "2026-08-21"


def test_el_texto_es_legible():
    _, vehiculo = _vehiculo_robado()
    texto = build_text(vehiculo)
    assert texto.startswith("Vehículo robado: Auto Toyota Rav4 negro mica 2019")
    assert "patente LKXV55" in texto
    assert "Fuente: GBV." in texto


# --- Aislamiento: no es una emergencia --------------------------------------


def test_vehicle_report_esta_fuera_del_motor_y_del_backfill():
    """`backfill.py` geocodifica con Nominatim los tipos correlacionables."""
    assert EventType.VEHICLE_REPORT not in CORRELATABLE_EVENT_TYPES
    assert EventType.VEHICLE_REPORT not in EVENT_TO_INCIDENT_TYPE


def test_la_confianza_es_cero_en_las_dos_tablas():
    from app.services.correlation.confidence import RULES

    assert SOURCE_BASE_CONFIDENCE[EventSource.GBV] == 0.0
    regla = RULES[EventSource.GBV]
    assert regla.max_weight == 0.0
    assert regla.min_weight == 0.0


def test_esta_registrado_con_cadencia_de_media_hora():
    from app.collectors.registry import available_collectors, collector_class

    assert "gbv_vehiculos" in available_collectors()
    assert collector_class("gbv_vehiculos") is GbvCollector
    assert GbvCollector.default_interval_seconds == 1800


def test_no_alimenta_ninguna_familia_del_mapa():
    from app.services.collector_health import COLLECTOR_ROLES

    assert "gbv_vehiculos" not in COLLECTOR_ROLES


# --- El collector (fetch con respx) -----------------------------------------


class _Gbv(GbvCollector):
    """Collector sin sesión: la base se sustituye por dos atributos."""

    def __init__(self, *, semilla: set[str] | None = None, conocidas: set[str] | None = None):
        # Sin `super().__init__`: la convención del proyecto es no tocar sesión.
        self.base_url = BASE
        self.semilla = semilla or set()
        self.conocidas = conocidas or set()

    async def _seccion_sin_filas(self, seccion):
        return seccion.clave in self.semilla

    async def _claves_conocidas(self, claves):
        return {clave for clave in claves if clave in self.conocidas}


TODAS_LAS_DENUNCIAS = {
    "gbv:robado:LKXV55",
    "gbv:robado:SZSY51",
    "gbv:robado:YW8869",
    "gbv:robado:VBGY34",
    "gbv:robado:id:6743",
    "gbv:robado:TCYD68",
}
TODOS_LOS_RECUPERADOS = {"gbv:recuperado:SGTH85", "gbv:recuperado:FRDT33"}
TODOS_LOS_ABANDONADOS = {"gbv:abandonado:id:89", "gbv:abandonado:id:88"}
TODO = TODAS_LAS_DENUNCIAS | TODOS_LOS_RECUPERADOS | TODOS_LOS_ABANDONADOS


def _html(texto: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=texto, headers={"content-type": "text/html; charset=UTF-8"})


Respuesta = httpx.Response | Exception


def _ruta(route: respx.Route, respuesta: Respuesta) -> respx.Route:
    if isinstance(respuesta, Exception):
        return route.mock(side_effect=respuesta)
    return route.mock(return_value=respuesta)


def _listados(
    denuncias: Respuesta | None = None,
    recuperados: Respuesta | None = None,
    abandonados: Respuesta | None = None,
) -> dict[str, respx.Route]:
    """Los tres listados. La página 1 de denuncias se ancla SIN query: una ruta
    de respx sin `params` también captura `?pag=2`."""
    return {
        "denuncias": _ruta(
            respx.get(url__regex=r"^https://gbvspa\.cl/denuncias/$"),
            denuncias or _html(DENUNCIAS_HTML),
        ),
        "recuperados": _ruta(respx.get(URL_RECUPERADOS), recuperados or _html(RECUPERADOS_HTML)),
        "abandonados": _ruta(respx.get(URL_ABANDONADOS), abandonados or _html(ABANDONADOS_HTML)),
    }


@pytest.fixture(autouse=True)
def _sin_pausa(monkeypatch):
    monkeypatch.setattr(settings, "GBV_PAUSA_SEGUNDOS", 0.0)
    monkeypatch.setattr(settings, "GBV_MAX_DETALLES", 15)
    # Una página salvo en los tests de paginación: con todo nuevo, el collector
    # pediría `?pag=2` y respx lo rechazaría por no estar declarado.
    monkeypatch.setattr(settings, "GBV_MAX_PAGINAS", 1)


@pytest.mark.asyncio
@respx.mock
async def test_la_semilla_marca_todo_y_no_pide_ningun_detalle():
    rutas = _listados()
    detalles = respx.get(url__regex=r".*-\d+$").mock(return_value=_html(DETALLE_GENERICO))

    instancia = _Gbv(semilla={"denuncias", "recuperados", "abandonados"})
    avisos = await instancia.fetch()

    assert all(aviso.semilla for aviso in avisos)
    assert all(aviso.detalle is None for aviso in avisos)
    assert not detalles.called
    # Los duplicados (YW8869, FRDT33) se funden antes de salir.
    assert {a.ficha.external_id for a in avisos} == TODO
    assert len(avisos) == len(TODO)
    # La semilla no pagina: sólo necesita el punto de partida.
    assert rutas["denuncias"].call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_lo_conocido_no_se_pide_ni_se_devuelve():
    _listados()
    detalle = respx.get(f"{BASE}/denuncias/auto-robo-desde-via-publica-lkxv55-6784").mock(
        return_value=_html(DETALLE_LKXV55)
    )
    otros = respx.get(url__regex=r".*-\d+$").mock(return_value=_html(DETALLE_GENERICO))

    instancia = _Gbv(conocidas=TODO - {"gbv:robado:LKXV55"})
    avisos = await instancia.fetch()

    assert [a.ficha.external_id for a in avisos] == ["gbv:robado:LKXV55"]
    assert avisos[0].detalle is not None and avisos[0].detalle["marca"] == "Toyota"
    assert detalle.call_count == 1
    assert not otros.called
    assert instancia.warnings == []


@pytest.mark.asyncio
@respx.mock
async def test_se_identifica_con_un_user_agent_de_navegador_y_de_alertav():
    rutas = _listados()
    await _Gbv(conocidas=TODO).fetch()

    ua = rutas["denuncias"].calls[0].request.headers["user-agent"]
    assert ua.startswith("Mozilla/5.0")
    assert "AlertaV" in ua
    assert rutas["denuncias"].calls[0].request.headers["accept-language"].startswith("es-CL")


@pytest.mark.asyncio
@respx.mock
async def test_lo_que_excede_el_tope_se_difiere_entero(monkeypatch):
    monkeypatch.setattr(settings, "GBV_MAX_DETALLES", 2)
    _listados()
    detalles = respx.get(url__regex=r".*-\d+$").mock(return_value=_html(DETALLE_GENERICO))

    avisos = await _Gbv(conocidas=TODOS_LOS_RECUPERADOS | TODOS_LOS_ABANDONADOS).fetch()

    # Seis denuncias nuevas, dos detalles: entran dos y las otras cuatro esperan
    # a la próxima corrida, siguen siendo nuevas y no entran a medias.
    assert len(avisos) == 2
    assert all(a.detalle is not None for a in avisos)
    assert detalles.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_un_detalle_que_ya_no_existe_entra_sin_el():
    _listados()
    respx.get(url__regex=r".*-\d+$").mock(return_value=_html("no", status=404))

    instancia = _Gbv(conocidas=TODO - {"gbv:robado:LKXV55"})
    avisos = await instancia.fetch()

    assert len(avisos) == 1
    assert avisos[0].detalle is None
    assert any("ya no existe" in aviso for aviso in instancia.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_detalles_caidos_se_difieren_y_se_corta_tras_tres():
    _listados()
    detalles = respx.get(url__regex=r".*-\d+$").mock(return_value=_html("caído", status=503))

    instancia = _Gbv(conocidas=TODOS_LOS_RECUPERADOS | TODOS_LOS_ABANDONADOS)
    avisos = await instancia.fetch()

    assert avisos == []
    assert detalles.call_count == MAX_FALLOS_SEGUIDOS
    # Había novedades y no se pudo leer ninguna: la corrida está ciega.
    assert instancia.blindness


@pytest.mark.asyncio
@respx.mock
async def test_una_seccion_caida_deja_la_corrida_parcial():
    _listados(recuperados=_html("error", status=503))

    instancia = _Gbv(conocidas=TODO)
    assert await instancia.fetch() == []
    assert any("recuperados" in aviso for aviso in instancia.warnings)
    assert not instancia.blindness


@pytest.mark.asyncio
@respx.mock
async def test_un_listado_sin_tarjetas_cuenta_como_seccion_caida():
    _listados(abandonados=_html(_pagina("GBV", "")))

    instancia = _Gbv(conocidas=TODO)
    await instancia.fetch()
    assert any("abandonados" in aviso and "vacía" in aviso for aviso in instancia.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_si_caen_las_tres_secciones_la_corrida_falla():
    _listados(
        denuncias=_html("x", status=500),
        recuperados=_html("x", status=500),
        abandonados=_html("x", status=500),
    )
    with pytest.raises(CollectorError, match="ninguna sección"):
        await _Gbv().fetch()


@pytest.mark.asyncio
@respx.mock
async def test_un_timeout_no_se_escapa_del_collector():
    _listados(recuperados=httpx.ReadTimeout("lento"))

    instancia = _Gbv(conocidas=TODO)
    assert await instancia.fetch() == []
    assert any("ReadTimeout" in aviso for aviso in instancia.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_pagina_solo_si_la_primera_venia_entera_nueva(monkeypatch):
    monkeypatch.setattr(settings, "GBV_MAX_PAGINAS", 2)
    pagina_2 = _pagina(
        "GBV",
        _tarjeta_denuncia("auto-hurto-kkkk11-6500", "a.jpg", "Quilpué")
        + _tarjeta_denuncia("auto-hurto-lllk22-6499", "b.jpg", "Limache"),
    )
    _listados()
    ruta_2 = respx.get(URL_DENUNCIAS, params={"pag": "2"}).mock(return_value=_html(pagina_2))
    respx.get(url__regex=r".*-\d+$").mock(return_value=_html(DETALLE_GENERICO))

    avisos = await _Gbv(
        conocidas=TODOS_LOS_RECUPERADOS | TODOS_LOS_ABANDONADOS | {"gbv:robado:LLLK22"}
    ).fetch()

    assert ruta_2.call_count == 1
    claves = {a.ficha.external_id for a in avisos}
    assert "gbv:robado:KKKK11" in claves
    assert "gbv:robado:LLLK22" not in claves


@pytest.mark.asyncio
@respx.mock
async def test_en_regimen_no_pagina(monkeypatch):
    monkeypatch.setattr(settings, "GBV_MAX_PAGINAS", 2)
    _listados()
    ruta_2 = respx.get(URL_DENUNCIAS, params={"pag": "2"}).mock(return_value=_html(DENUNCIAS_HTML))
    await _Gbv(conocidas=TODO).fetch()
    assert not ruta_2.called


# --- normalize ---------------------------------------------------------------


def _aviso(ficha: FichaListado, detalle=None, semilla=False) -> AvisoGbv:
    return AvisoGbv(ficha, detalle, semilla=semilla, visto_en=VISTO_EN)


def test_normalize_avisa_de_una_patente_invalida_solo_si_es_nueva():
    ficha = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS).fichas[5]

    semilla = _Gbv()
    assert len(semilla.normalize([_aviso(ficha, semilla=True)])) == 1
    assert semilla.warnings == []

    nueva = _Gbv()
    eventos = nueva.normalize([_aviso(ficha, {"lugar de delito": "Puente Alto"})])
    assert eventos[0].external_id == "gbv:robado:id:6743"
    assert eventos[0].raw_data["gbv"]["region"] == "otra"
    assert any("cshz796" in aviso for aviso in nueva.warnings)


def test_normalize_produce_eventos_de_vehiculo():
    lectura = parse_listado(DENUNCIAS_HTML, seccion=DENUNCIAS, url_pagina=URL_DENUNCIAS)
    eventos = _Gbv().normalize([_aviso(f) for f in lectura.fichas])

    assert len(eventos) == 7
    assert {e.type for e in eventos} == {EventType.VEHICLE_REPORT}
    assert all(e.lat is None for e in eventos)


# --- Salud -------------------------------------------------------------------


def test_la_salud_usa_las_reglas_de_collectors_health():
    from app.services.collector_health import estado_de_collector

    ahora = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
    reciente = SimpleNamespace(
        status="success", finished_at=ahora - timedelta(minutes=10), started_at=ahora, error=None
    )
    vieja = SimpleNamespace(
        status="success", finished_at=ahora - timedelta(hours=5), started_at=ahora, error=None
    )
    fallida = SimpleNamespace(status="failed", finished_at=ahora, started_at=ahora, error="x")

    assert estado_de_collector("gbv_vehiculos", None, ahora=ahora) == "never"
    assert estado_de_collector("gbv_vehiculos", reciente, ahora=ahora) == "ok"
    assert estado_de_collector("gbv_vehiculos", vieja, ahora=ahora) == "stale"
    assert estado_de_collector("gbv_vehiculos", fallida, ahora=ahora) == "failing"


# --- Consulta del feed -------------------------------------------------------


def _sql(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    from app.repositories.event_repository import EventRepository

    repo = EventRepository.__new__(EventRepository)
    stmt = repo.vehicle_feed_stmt(**kwargs)
    compilado = stmt.compile(dialect=postgresql.dialect())
    return str(compilado), dict(compilado.params)


def test_la_ventana_se_mide_con_ingested_at_y_excluye_la_semilla():
    desde = VISTO_EN - timedelta(hours=48)
    sql, params = _sql(since=desde, locations=[VehicleLocation.V_REGION])

    assert "raw_events.ingested_at >=" in sql
    assert "raw_events.timestamp >=" not in sql
    assert re.search(r"NOT \([\w.]*raw_events\.raw_data @>", sql)
    assert desde in params.values()
    assert {"gbv": {"semilla": True}} in params.values()
    assert {"gbv": {"region": "v_region"}} in params.values()
    assert re.search(r"ORDER BY [\w.]*raw_events\.ingested_at DESC", sql)


def test_filtra_por_estado_y_comuna_con_contencion():
    _, params = _sql(
        since=VISTO_EN,
        statuses=[VehicleStatus.RECUPERADO],
        locations=[VehicleLocation.V_REGION, VehicleLocation.SIN_UBICAR],
        commune="Quilpué",
    )
    assert {"gbv": {"estado": "recuperado"}} in params.values()
    assert {"gbv": {"comuna": "Quilpué"}} in params.values()
    assert {"gbv": {"region": "sin_ubicar"}} in params.values()
    assert {"gbv": {"region": "otra"}} not in params.values()


# --- Endpoint ----------------------------------------------------------------


def _fila(**gbv: Any) -> SimpleNamespace:
    datos = {
        "seccion": "denuncias",
        "estado": "robado",
        "id_gbv": 6784,
        "url": f"{BASE}/denuncias/auto-robo-desde-via-publica-lkxv55-6784",
        "patente": "LKXV55",
        "tipo_vehiculo": "Auto",
        "marca": "Toyota",
        "modelo": "Rav4",
        "color": "Negro Mica",
        "anio": 2019,
        "delito": "Robo desde vía publica",
        "lugar": "los araucanos 290",
        "recuperado_en": None,
        "autoridad": None,
        "tiempo_abandono": None,
        "fecha_delito": "2026-09-22",
        "fecha_precision": "dia",
        "comuna": None,
        "region": "sin_ubicar",
        "con_detalle": True,
        "semilla": False,
    }
    datos.update(gbv)
    return SimpleNamespace(public_id=uuid4(), ingested_at=VISTO_EN, raw_data={"gbv": datos})


class _RepoFalso:
    def __init__(self, filas: list[SimpleNamespace]) -> None:
        self.filas = filas
        self.kwargs: dict[str, Any] = {}

    async def list_vehicle_feed(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.filas


@pytest.fixture
def cliente():
    from app.api.deps import get_vehicle_feed_service
    from app.main import app
    from app.services.vehicle_feed_service import VehicleFeedService

    repo = _RepoFalso([_fila(), _fila(estado="robado", region="rara")])
    servicio = VehicleFeedService.__new__(VehicleFeedService)
    servicio.repo = repo  # type: ignore[assignment]

    async def _sin_corridas() -> None:
        return None

    servicio._ultima_corrida = _sin_corridas  # type: ignore[method-assign]
    app.dependency_overrides[get_vehicle_feed_service] = lambda: servicio
    yield TestClient(app), repo
    app.dependency_overrides.pop(get_vehicle_feed_service, None)


def test_el_feed_responde_con_los_vehiculos_y_la_salud_de_la_fuente(cliente):
    client, repo = cliente
    respuesta = client.get("/api/v1/feed/vehiculos")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    # La fila con `region` desconocida se omite en vez de tumbar el feed.
    assert cuerpo["total"] == 1
    item = cuerpo["items"][0]
    assert item["patente"] == "LKXV55"
    assert item["estado"] == "robado"
    assert item["fecha_delito"] == "2026-09-22"
    assert item["region"] == "sin_ubicar"
    assert cuerpo["horas"] == 48
    assert cuerpo["fuente"] == {
        "collector": "gbv_vehiculos",
        "estado": "never",
        "ultima_corrida": None,
        "detalle": None,
    }
    assert repo.kwargs["locations"] == [VehicleLocation.V_REGION, VehicleLocation.SIN_UBICAR]
    assert repo.kwargs["statuses"] is None


def test_la_ventana_por_defecto_es_48_horas(cliente):
    client, repo = cliente
    antes = datetime.now(UTC)
    client.get("/api/v1/feed/vehiculos")
    desde = repo.kwargs["since"]
    assert timedelta(hours=47, minutes=59) < antes - desde < timedelta(hours=48, minutes=1)


def test_no_acepta_mas_de_48_horas(cliente):
    client, _ = cliente
    assert client.get("/api/v1/feed/vehiculos?horas=49").status_code == 422
    assert client.get("/api/v1/feed/vehiculos?horas=6").json()["horas"] == 6


def test_filtros_de_estado_y_comuna(cliente):
    client, repo = cliente
    respuesta = client.get(
        "/api/v1/feed/vehiculos?estado=recuperado&estado=abandonado&comuna=quilpue"
        "&incluir_sin_ubicar=false"
    )
    assert respuesta.status_code == 200
    assert repo.kwargs["statuses"] == [VehicleStatus.RECUPERADO, VehicleStatus.ABANDONADO]
    # La comuna se canoniza: el filtro por contención es exacto.
    assert repo.kwargs["commune"] == "Quilpué"
    assert repo.kwargs["locations"] == [VehicleLocation.V_REGION]


def test_una_comuna_de_otra_region_es_un_422(cliente):
    client, _ = cliente
    respuesta = client.get("/api/v1/feed/vehiculos?comuna=Maipú")
    assert respuesta.status_code == 422
    assert "Región de Valparaíso" in respuesta.json()["detail"]


def test_un_estado_desconocido_es_un_422(cliente):
    client, _ = cliente
    assert client.get("/api/v1/feed/vehiculos?estado=chocado").status_code == 422
