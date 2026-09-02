"""El enlace público de una señal: qué sale, qué no, y por qué.

Estos tests cubren un borde de SALIDA hacia el navegador de un usuario. Lo que
pasa por acá termina en un `href`, así que la mitad del archivo son casos que
deben ser rechazados.
"""

from __future__ import annotations

import pytest

from app.models.enums import EventSource
from app.services.source_links import (
    is_safe_url,
    source_label_for,
    source_url_for,
)

# --- 1. Cada fuente guarda el enlace en su propia llave -----------------------


def test_la_prensa_publica_su_url():
    raw = {"url": "https://puranoticia.cl/nota/bus-limache", "titular": "Bus…"}

    assert source_url_for(EventSource.MEDIA, raw) == (
        "https://puranoticia.cl/nota/bus-limache"
    )


def test_instagram_publica_su_permalink():
    raw = {"permalink": "https://www.instagram.com/p/ABC123/", "cuenta": "alertav"}

    assert source_url_for(EventSource.SOCIAL_MEDIA, raw) == (
        "https://www.instagram.com/p/ABC123/"
    )


def test_bomberos_lo_tiene_anidado():
    raw = {"_bomberos": {"guid": "https://x.com/CGI_CBV/status/1962"}}

    assert source_url_for(EventSource.BOMBEROS, raw) == (
        "https://x.com/CGI_CBV/status/1962"
    )


def test_una_fuente_sin_entrada_propia_igual_encuentra_las_llaves_genericas():
    """Un collector que empiece a guardar `url` mañana se recoge solo."""
    raw = {"url": "https://vialidad.mop.gob.cl/aviso/9"}

    assert source_url_for(EventSource.MOP, raw) is not None


def test_las_fuentes_que_no_publican_enlaces_devuelven_none():
    """Chilquinta informa un corte, no una página. None no es un fallo."""
    assert source_url_for(EventSource.CHILQUINTA, {"clientes": 320}) is None


# --- 2. El guid de Bomberos que NO es una URL --------------------------------


def test_un_guid_numerico_no_se_convierte_en_enlace():
    """`_ID_KEYS` del webhook prefiere `id`, que es un número suelto.

    Construir `https://x.com/<cuenta>/status/<id>` a mano obligaría a adivinar
    el nombre de la cuenta y produciría enlaces rotos con aire de válidos. Que
    no haya enlace es la respuesta correcta.
    """
    raw = {"_bomberos": {"guid": "1962847362819273"}}

    assert source_url_for(EventSource.BOMBEROS, raw) is None


# --- 3. El borde de salida: lo que NO puede llegar a un href -----------------


@pytest.mark.parametrize(
    "hostil",
    [
        "javascript:alert(document.cookie)",
        "JavaScript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "blob:https://portal.cl/9f2b",
    ],
)
def test_los_esquemas_que_no_son_http_no_pasan(hostil):
    """Lista blanca, no negra.

    `local_news_worker` arma la URL con `urljoin` sobre un `href` que escribió
    un tercero, y `urljoin` resuelve rutas: no juzga esquemas.
    `urljoin("https://portal.cl/", "javascript:alert(1)")` devuelve el
    `javascript:` intacto.
    """
    assert is_safe_url(hostil) is False
    assert source_url_for(EventSource.MEDIA, {"url": hostil}) is None


def test_un_esquema_partido_por_un_salto_de_linea_no_pasa():
    """Algunos navegadores ignoran los controles al resolver el esquema."""
    assert is_safe_url("java\nscript:alert(1)") is False


def test_una_url_relativa_no_pasa():
    """Sin el portal no se puede resolver, y resolverla contra NUESTRO origen
    produciría un enlace a una página de AlertaV que no existe."""
    assert is_safe_url("/region-valparaiso/bus-limache") is False


def test_una_url_sin_host_no_pasa():
    assert is_safe_url("https://") is False


def test_una_url_absurdamente_larga_no_pasa():
    assert is_safe_url("https://portal.cl/" + "a" * 4000) is False


@pytest.mark.parametrize("basura", [None, 42, [], {}, "", "   "])
def test_lo_que_no_es_una_cadena_util_no_pasa(basura):
    assert is_safe_url(basura) is False


def test_un_raw_data_que_no_es_un_diccionario_no_revienta():
    assert source_url_for(EventSource.MEDIA, None) is None
    assert source_url_for(EventSource.MEDIA, "no soy un dict") is None
    assert source_label_for(EventSource.MEDIA, ["tampoco"]) is None


# --- 4. El nombre de quien publicó -------------------------------------------


def test_el_medio_sale_del_bloque_de_prensa():
    raw = {"_prensa": {"medio": "Pura Noticia", "portal": "puranoticia"}}

    assert source_label_for(EventSource.MEDIA, raw) == "Pura Noticia"


def test_sin_medio_cae_al_slug_del_portal():
    raw = {"_prensa": {"portal": "sitiodelsuceso"}}

    assert source_label_for(EventSource.MEDIA, raw) == "sitiodelsuceso"


def test_la_cuenta_de_instagram_se_muestra_con_arroba():
    """El arroba es decoración del panel: en `raw_data` vive el identificador,
    que es lo que se usa para volver a consultar la cuenta."""
    assert source_label_for(EventSource.SOCIAL_MEDIA, {"cuenta": "alertav"}) == "@alertav"


def test_una_cuenta_que_ya_trae_arroba_no_lo_duplica():
    assert source_label_for(EventSource.SOCIAL_MEDIA, {"cuenta": "@alertav"}) == "@alertav"


def test_sin_nombre_propio_devuelve_none_y_no_una_cadena_vacia():
    """None le dice al panel «mostrá la banda»; "" le diría «mostrá nada»."""
    assert source_label_for(EventSource.CHILQUINTA, {"clientes": 10}) is None
    assert source_label_for(EventSource.MEDIA, {"_prensa": {"medio": "   "}}) is None
