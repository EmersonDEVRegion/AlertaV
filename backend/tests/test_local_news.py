"""Collector de prensa local: partes puras, delta fetching y aislamiento de fallos.

Como en el resto del proyecto, `normalize()` se testea sobre instancias creadas
con `__new__`: sin sesión, sin configuración y sin red. Lo que sí se monta acá
es un doble de repositorio para `unseen()` y un servidor falso con `respx` para
`fetch()`, porque las dos piezas que justifican este módulo son el delta fetching
—lo que ahorra tokens— y que un portal caído no se lleve al otro por delante.

Los fixtures de RSS y de HTML **no son inventados**: reproducen la estructura
real que sirven los dos portales, verificada el 31 de agosto de 2026. El HTML de
Pura Noticia incluye a propósito su trampa: un slot de publicidad envuelto en el
mismo `<article class="relative content">` que las tarjetas de noticias.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.collectors.news.local_news_worker import (
    _VERBOS_TITULAR,
    LocalNewsCollector,
    NewsItem,
    NewsPortal,
    ResolvedNews,
    clasificar_noticia,
    comuna_en_categorias,
    comuna_en_texto,
    es_emergencia,
    es_reciente,
    external_id_for,
    parse_fecha_es,
    parse_feed,
    parse_portada,
    parse_portals,
    portada_parece_rota,
)
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType, family_of_event

AHORA = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)

SITIO = NewsPortal(
    slug="sitiodelsuceso",
    nombre="Sitio del Suceso",
    feed_url="https://www.sitiodelsuceso.cl/feed/",
    portada_url="https://www.sitiodelsuceso.cl/",
)
PURA = NewsPortal(
    slug="puranoticia",
    nombre="Pura Noticia",
    feed_url=None,
    portada_url="https://puranoticia.pnt.cl/region-valparaiso",
)


# --- Fixtures de fuente real -------------------------------------------------

#: Estructura exacta del feed de Sitio del Suceso: RSS 2.0, `guid` no permalink,
#: `<category>` con la comuna y la bajada con entidades HTML sin resolver.
FEED_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
  <title>Sitio del Suceso</title>
  <link>https://www.sitiodelsuceso.cl</link>
  <item>
    <title>Excursionistas fueron rescatados tras perderse camino al Salto del Agua</title>
    <link>https://www.sitiodelsuceso.cl/2026/08/31/excursionistas-rescatados/</link>
    <dc:creator><![CDATA[Jorge]]></dc:creator>
    <pubDate>Mon, 31 Aug 2026 19:55:53 +0000</pubDate>
    <category><![CDATA[Valparaíso]]></category>
    <guid isPermaLink="false">https://www.sitiodelsuceso.cl/?p=19318</guid>
    <description><![CDATA[Una intensa b&#250;squeda se desarroll&#243; en el sector del
    Tranque La Luz, en Placilla, luego que dos personas se extraviaran&#8230; ]]></description>
    <content:encoded><![CDATA[<p>Personal del GOPE concurrió al lugar.</p>]]></content:encoded>
  </item>
  <item>
    <title>Anuncian futuro Macrocentro del Cáncer para la Región de Valparaíso</title>
    <link>https://www.sitiodelsuceso.cl/2026/08/31/macrocentro-del-cancer/</link>
    <pubDate>Mon, 31 Aug 2026 19:34:37 +0000</pubDate>
    <category><![CDATA[Valparaíso]]></category>
    <guid isPermaLink="false">https://www.sitiodelsuceso.cl/?p=19328</guid>
    <description><![CDATA[La Región fue confirmada como una de las prioridades&#8230; ]]></description>
  </item>
</channel>
</rss>
"""

#: Estructura exacta de una tarjeta de Pura Noticia, incluido el slot de Google
#: Ads envuelto en el MISMO `<article class="relative content">` que las
#: noticias, y la nota repetida en un segundo carrusel de la misma página.
PORTADA_HTML = """<!doctype html><html><body>
<section>
 <div class="arts editor arts-six">
  <article class="relative content">
   <a href="/region-valparaiso/al-menos-dos-personas-resultaron-lesionadas-tras-colision"
      title="Al menos dos personas resultaron lesionadas tras colisión frontal entre dos vehículos en San Felipe">
    <figure class="img-wrap"><img src="/cms/foto.jpg" alt=""></figure>
    <footer class="absolute cont-txt">
     <h3 class="subtit">Al menos dos personas resultaron lesionadas tras colisión frontal
      entre dos vehículos en San Felipe</h3>
     <span class="fecha">Lunes 31 de agosto de 2026</span>
    </footer>
   </a>
  </article>
  <article class="relative content">
   <div class="ad-pnt-slot banner-plain" data-adunitpath="/27653347/300x250_TOP_PORTADA_PNT">
    <iframe title="Contenido de anuncios de terceros" width="300" height="250"></iframe>
   </div>
  </article>
  <article class="relative content">
   <a href="/region-valparaiso/comite-de-ministros-mantiene-calificacion-favorable"
      title="Comité de Ministros mantiene calificación favorable para la extensión del Metro">
    <footer class="absolute cont-txt">
     <h3 class="subtit">Comité de Ministros mantiene calificación favorable para la
      extensión del Metro a Quillota y La Calera</h3>
     <span class="fecha">Lunes 31 de agosto de 2026</span>
    </footer>
   </a>
  </article>
 </div>
 <div class="row">
  <article class="col xs-6">
   <div class="content">
    <a href="/region-valparaiso/al-menos-dos-personas-resultaron-lesionadas-tras-colision">
     <h3>Al menos dos personas resultaron lesionadas tras colisión frontal entre dos
      vehículos en San Felipe</h3>
     <span class="fecha">18:42</span>
    </a>
   </div>
  </article>
  <article class="col xs-6">
   <div class="content">
    <a href="/region-valparaiso/tarjeta-solo-imagen">
     <figure class="img-wrap"><img src="/cms/otra.jpg" alt=""></figure>
    </a>
   </div>
  </article>
 </div>
</section>
</body></html>
"""


def _feed_reciente() -> str:
    """El mismo feed, republicado hace un minuto.

    `FEED_RSS` lleva la fecha real del 31 de agosto de 2026 porque los tests de
    parseo verifican que `<pubDate>` se lea exactamente, y para eso la fecha
    tiene que ser fija. Pero los tests que ejercitan `fetch()` pasan además por
    el filtro de frescura, y ahí una fecha fija es una bomba de tiempo: el test
    pasa el día que se escribe y empieza a fallar al siguiente, con aspecto de
    bug del collector. Se reescribe la fecha en el momento de usarla.
    """
    ahora = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return FEED_RSS.replace("Mon, 31 Aug 2026 19:55:53 +0000", ahora).replace(
        "Mon, 31 Aug 2026 19:34:37 +0000", ahora
    )


def _item(**kwargs) -> NewsItem:
    base = {
        "portal": "sitiodelsuceso",
        "portal_nombre": "Sitio del Suceso",
        "titular": "Colisión frontal entre dos vehículos deja dos lesionados",
        "bajada": "El accidente ocurrió en Av. España con Uno Norte.",
        "link": "https://www.sitiodelsuceso.cl/2026/08/31/colision/",
        "guid": "https://www.sitiodelsuceso.cl/?p=1",
        "published_at": AHORA - timedelta(minutes=20),
        "resolucion_dia": False,
        "comuna_hint": "Viña del Mar",
        "origen": "rss",
        "raw": {},
    }
    base.update(kwargs)
    return NewsItem(**base)  # type: ignore[arg-type]


# --- Declaración de portales -------------------------------------------------


def test_parse_portals_acepta_el_feed_vacio() -> None:
    """Pura Noticia no tiene RSS: su fila deja el campo del feed en blanco y eso
    es una configuración válida, no un error."""
    portales = parse_portals(
        "sitiodelsuceso|Sitio del Suceso|https://a/feed/|https://a/;"
        "puranoticia|Pura Noticia||https://b/region-valparaiso"
    )
    assert [p.slug for p in portales] == ["sitiodelsuceso", "puranoticia"]
    assert portales[1].feed_url is None
    assert portales[1].portada_url == "https://b/region-valparaiso"


def test_parse_portals_rechaza_un_portal_sin_ninguna_url() -> None:
    """Las dos URL vacías no es un portal apagado: es una fila que nadie va a
    consultar nunca y que nadie notaría."""
    with pytest.raises(ValueError, match="no declara ni feed ni portada"):
        parse_portals("fantasma|Fantasma||")


def test_parse_portals_rechaza_el_formato_incompleto() -> None:
    with pytest.raises(ValueError, match="mal declarado"):
        parse_portals("sitiodelsuceso|Sitio del Suceso|https://a/feed/")


# --- Pre-filtro reutilizado --------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Colisión frontal entre dos vehículos deja dos lesionados en San Felipe",
        "Bomberos concurrió a un incendio estructural en el cerro Cordillera",
        "Rescatan a excursionistas perdidos en el Tranque La Luz",
        "Volcamiento de camión mantiene cortada la Ruta 68",
    ],
)
def test_es_emergencia_reconoce_los_titulares_de_siniestro(texto: str) -> None:
    assert es_emergencia(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        # Las nueve de cada diez notas de estos portales.
        "Anuncian futuro Macrocentro del Cáncer para la Región de Valparaíso",
        "Cuatro detenidos y 144 dosis de droga decomisadas tras operativo en Cabildo",
        "Retiran 16 rucos en nuevo operativo para recuperar espacios públicos",
        "Agua Santa y Reñaca entre los sectores beneficiados con plan de bacheo",
        "Bodyboardista de Zapallar clasifica a la Gran Final del IBC World Tour",
    ],
)
def test_es_emergencia_descarta_la_agenda_normal_del_diario(texto: str) -> None:
    assert es_emergencia(texto) is False


@pytest.mark.parametrize(
    "texto",
    [
        "Entregan subsidios a damnificados del megaincendio de febrero de 2024",
        "Se cumple un nuevo aniversario del megaincendio que arrasó Viña del Mar",
        "Condenan a autor del incendio de febrero de 2024 en Valparaíso",
    ],
)
def test_la_retrospectiva_del_megaincendio_no_paga_una_llamada(texto: str) -> None:
    """El suceso más referenciado de la prensa regional contiene la palabra
    'incendio' en cada crónica de tribunales. Sin excindir las formas fechadas,
    cada corrida pagaría el modelo por un juicio."""
    assert es_emergencia(texto) is False


def test_el_megaincendio_sin_fecha_sigue_pasando() -> None:
    """La regla que define `_RUIDO_PRENSA`: sólo formas fechadas. 'megaincendio'
    a secas contiene 'incendio' como subcadena, así que excindirlo dejaría al
    sistema ciego justo el día que ocurra el siguiente."""
    assert es_emergencia("Megaincendio forestal avanza sin control en Quilpué") is True


# --- El registro del titular -------------------------------------------------


@pytest.mark.parametrize(
    ("titular", "esperado"),
    [
        # El caso que descubrió el problema: la única emergencia real del feed de
        # Sitio del Suceso del 31 de agosto se caía del sistema en silencio.
        ("Rescatan a excursionistas perdidos en el Tranque La Luz", EventType.RESCUE),
        ("Excursionistas fueron rescatados tras perderse", EventType.RESCUE),
        ("Chocan dos vehículos en la Ruta 68", EventType.ACCIDENT),
        ("Camión volcó en la cuesta Las Chilcas", EventType.ACCIDENT),
        ("Se incendia vivienda en el cerro Cordillera", EventType.OTHER),
    ],
)
def test_el_verbo_del_titular_tambien_es_una_emergencia(
    titular: str, esperado: EventType
) -> None:
    """El diccionario compartido se calibró contra captions, que anuncian con
    sustantivo ("RESCATE en..."). Un titular usa verbo conjugado ("Rescatan a
    ..."), y por subcadena "rescate" no empareja con "rescatan"."""
    assert es_emergencia(titular) is True
    assert clasificar_noticia(titular) is esperado


@pytest.mark.parametrize(
    "titular",
    [
        # El infinitivo casi siempre es figurado, y por eso "rescatar" NO está en
        # la tabla: estas dos son noticias municipales, no emergencias.
        "Plan busca rescatar espacios públicos ocupados en Quilpué",
        "Iniciativa para rescatar el patrimonio arquitectónico del cerro Alegre",
        # La trampa que mordió al primer intento: "arde" está dentro de "tarde".
        "Feria costumbrista se realizará durante la tarde del domingo",
    ],
)
def test_los_verbos_no_muerden_dentro_de_otra_palabra(titular: str) -> None:
    assert es_emergencia(titular) is False


@pytest.mark.parametrize("termino", sorted(_VERBOS_TITULAR))
def test_los_verbos_estan_normalizados(termino: str) -> None:
    """Todo se compara sobre texto ya normalizado (sin tildes, en minúscula), así
    que un término con tilde no coincidiría nunca y el fallo sería mudo."""
    from app.collectors.geoservices import normalise_text

    assert termino == normalise_text(termino)


@pytest.mark.parametrize(
    "titular",
    [
        "Rescatan a excursionistas perdidos en el Tranque La Luz",
        "Colisión frontal entre dos vehículos deja dos lesionados",
        "Bomberos concurrió a un incendio estructural",
        "Anuncian futuro Macrocentro del Cáncer para la Región",
        "Cuatro detenidos tras operativo antidrogas en Cabildo",
    ],
)
def test_el_prefiltro_y_el_clasificador_de_prensa_no_se_contradicen(
    titular: str,
) -> None:
    """La invariante: `clasificar_noticia` devuelve None si y sólo si
    `es_emergencia` devolvió False. Sin ella, una noticia pasaría el filtro y
    desaparecería después en el `if event_type is not None` de `fetch()` — sin
    costar dinero, pero perdiendo la señal sin dejar rastro."""
    assert (clasificar_noticia(titular) is not None) == es_emergencia(titular)


# --- Comunas -----------------------------------------------------------------


def test_comuna_en_categorias_lee_la_etiqueta_del_feed() -> None:
    """`<category>` es la fuente diciendo dónde ocurrió: gratis y mejor que
    cualquier heurística sobre el texto."""
    assert comuna_en_categorias(["Viña del Mar"]) == "Viña del Mar"
    assert comuna_en_categorias(["VALPARAISO"]) == "Valparaíso"


def test_comuna_en_categorias_no_busca_subcadenas() -> None:
    """Una categoría es una etiqueta corta y controlada. Buscar subcadenas ahí
    convertiría 'Deportes Quillota' en una ubicación."""
    assert comuna_en_categorias(["Policial"]) is None
    assert comuna_en_categorias(["Deportes Quillota"]) is None


def test_comuna_en_texto_es_el_respaldo_del_camino_html() -> None:
    assert (
        comuna_en_texto("Colisión frontal entre dos vehículos en San Felipe")
        == "San Felipe"
    )


def test_las_comunas_largas_se_prueban_antes_que_las_cortas() -> None:
    """Sin ordenar por longitud, 'La Calera' resolvería a 'Calera' —o peor, a
    otra comuna contenida— y el punto caería en el lugar equivocado."""
    assert comuna_en_texto("Choque múltiple en La Calera") == "La Calera"
    assert comuna_en_texto("Incendio en Villa Alemana") == "Villa Alemana"


# --- Fechas ------------------------------------------------------------------


def test_parse_fecha_es_lee_la_fecha_larga_de_la_tarjeta() -> None:
    momento, resolucion_dia = parse_fecha_es("Lunes 31 de agosto de 2026", ahora=AHORA)
    assert momento == datetime(2026, 8, 31, tzinfo=UTC)
    # El día es cierto; la hora no existe y no se inventa.
    assert resolucion_dia is True


def test_parse_fecha_es_combina_fecha_y_hora_cuando_estan_las_dos() -> None:
    momento, resolucion_dia = parse_fecha_es(
        "31 de agosto de 2026 18:42", ahora=AHORA
    )
    assert momento == datetime(2026, 8, 31, 18, 42, tzinfo=UTC)
    assert resolucion_dia is False


def test_la_hora_suelta_se_ancla_al_dia_de_la_corrida() -> None:
    momento, resolucion_dia = parse_fecha_es("18:42", ahora=AHORA)
    assert momento == datetime(2026, 8, 31, 18, 42, tzinfo=UTC)
    assert resolucion_dia is False


def test_la_hora_suelta_futura_retrocede_un_dia() -> None:
    """Son las 00:20 UTC y la tarjeta dice 23:10 (hora de Chile). Sin este
    retroceso, `EventCreate` rechazaría el evento por futuro y se perdería la
    señal entera por un desfase de zona horaria."""
    medianoche = datetime(2026, 9, 1, 0, 20, tzinfo=UTC)
    momento, _ = parse_fecha_es("23:10", ahora=medianoche)
    assert momento == datetime(2026, 8, 31, 23, 10, tzinfo=UTC)


def test_parse_fecha_es_sin_fecha_reconocible() -> None:
    assert parse_fecha_es("Región Valparaíso", ahora=AHORA) == (None, False)


# --- Frescura ----------------------------------------------------------------


def test_es_reciente_usa_la_ventana_normal_cuando_hay_hora() -> None:
    assert es_reciente(
        _item(published_at=AHORA - timedelta(minutes=30)),
        ahora=AHORA,
        max_age_minutes=240,
    )
    assert not es_reciente(
        _item(published_at=AHORA - timedelta(hours=9)),
        ahora=AHORA,
        max_age_minutes=240,
    )


def test_una_noticia_sin_fecha_pasa() -> None:
    """Misma decisión que en Instagram: procesar de más cuesta una llamada que
    el delta atrapa en la corrida siguiente; descartar pierde un siniestro por un
    campo que el portal no llenó."""
    assert es_reciente(_item(published_at=None), ahora=AHORA, max_age_minutes=240)


def test_la_resolucion_de_dia_no_se_mide_en_minutos() -> None:
    """Chile va cuatro horas por detrás de UTC: una nota publicada el martes por
    la tarde en Valparaíso puede estar fechada el martes mientras acá ya es
    miércoles. Comparar eso con una ventana de minutos sería fingir una precisión
    que el dato no tiene."""
    ayer = _item(
        published_at=datetime(2026, 8, 30, tzinfo=UTC), resolucion_dia=True
    )
    anteayer = _item(
        published_at=datetime(2026, 8, 29, tzinfo=UTC), resolucion_dia=True
    )
    assert es_reciente(ayer, ahora=AHORA, max_age_minutes=240)
    assert not es_reciente(anteayer, ahora=AHORA, max_age_minutes=240)


# --- Camino 1: RSS -----------------------------------------------------------


def test_parse_feed_lee_el_esquema_real_de_sitio_del_suceso() -> None:
    noticias = parse_feed(FEED_RSS, SITIO)

    assert len(noticias) == 2
    primera = noticias[0]
    assert primera.titular.startswith("Excursionistas fueron rescatados")
    assert primera.guid == "https://www.sitiodelsuceso.cl/?p=19318"
    assert primera.published_at == datetime(2026, 8, 31, 19, 55, 53, tzinfo=UTC)
    assert primera.resolucion_dia is False
    assert primera.origen == "rss"
    # El regalo del feed: la comuna la declara la propia fuente.
    assert primera.comuna_hint == "Valparaíso"


def test_la_bajada_llega_sin_html_ni_entidades() -> None:
    """`&#250;` y `&#8230;` viajan crudos en el CDATA. Sin resolverlos, el texto
    que ve el modelo —y el que queda archivado en `raw_events.text`— llevaría
    marcado dentro."""
    primera = parse_feed(FEED_RSS, SITIO)[0]
    assert "búsqueda" in primera.bajada
    assert "&#" not in primera.bajada
    assert "<" not in primera.bajada


def test_el_cuerpo_completo_no_entra_al_texto() -> None:
    """Se lee `<description>` y no `<content:encoded>`: el cuerpo de una crónica
    nombra todos los lugares del relato y le daría al extractor tres candidatos
    sin forma de elegir."""
    primera = parse_feed(FEED_RSS, SITIO)[0]
    assert "GOPE" not in primera.texto


def test_el_texto_filtrable_concatena_titular_y_bajada() -> None:
    item = _item(titular="Choque múltiple", bajada="Ocurrió en la Ruta 68.")
    assert item.texto == "Choque múltiple. Ocurrió en la Ruta 68."


def test_el_texto_sin_bajada_es_solo_el_titular() -> None:
    assert _item(bajada="").texto == "Colisión frontal entre dos vehículos deja dos lesionados"


# --- Camino 2: HTML ----------------------------------------------------------


def test_parse_portada_lee_las_tarjetas_reales() -> None:
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)

    titulares = [n.titular for n in noticias]
    assert len(noticias) == 2
    assert any("colisión frontal" in t.lower() for t in titulares)
    assert any("Comité de Ministros" in t for t in titulares)


def test_el_anuncio_disfrazado_de_article_no_entra() -> None:
    """La trampa real de Pura Noticia: sirve sus slots de Google Ads envueltos en
    el mismo `<article class="relative content">` que las noticias. Un barrido
    por etiqueta los recoge; la ausencia de un `<a href>` propio los descarta."""
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    assert all("anuncios" not in n.titular.lower() for n in noticias)
    assert all("ad-pnt" not in n.link for n in noticias)


def test_la_tarjeta_de_solo_imagen_tampoco_entra() -> None:
    """La otra mitad de la red: un `<article>` con enlace y sin titular. Es la
    tarjeta de sólo imagen que estos portales ponen en los rieles laterales, y
    sin la guarda de titular entraría con texto vacío — una señal sin hecho que
    filtrar y sin nada que geocodificar."""
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    assert all("solo-imagen" not in n.link for n in noticias)
    assert all(len(n.titular) >= 25 for n in noticias)
    assert len(noticias) == 2


def test_la_misma_nota_en_dos_carruseles_entra_una_sola_vez() -> None:
    """Sin deduplicar por enlace, una noticia repetida en tres rieles de la misma
    página pagaría tres extracciones y tres geocodificaciones antes de que la base
    la colapse por `external_id`."""
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    enlaces = [n.link for n in noticias]
    assert len(enlaces) == len(set(enlaces))


def test_los_href_relativos_se_resuelven_contra_el_portal() -> None:
    """El enlace es la identidad de la noticia. Una ruta sin dominio haría que dos
    portales pudieran colisionar en el mismo `external_id`."""
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    assert all(n.link.startswith("https://puranoticia.pnt.cl/") for n in noticias)


def test_la_tarjeta_sin_parrafo_deja_la_bajada_vacia() -> None:
    """El caso NORMAL de Pura Noticia, no el excepcional: sus tarjetas son enlace
    + imagen + h3 + fecha, sin un solo `<p>`."""
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    assert all(n.bajada == "" for n in noticias)
    assert all(n.raw["tiene_bajada"] is False for n in noticias)


def test_la_fecha_larga_de_la_tarjeta_marca_resolucion_de_dia() -> None:
    noticias = parse_portada(PORTADA_HTML, PURA, ahora=AHORA)
    colision = next(n for n in noticias if "colisión" in n.titular.lower())
    assert colision.resolucion_dia is True


def test_portada_parece_rota_distingue_el_rediseno_del_dia_tranquilo() -> None:
    rota, motivo = portada_parece_rota(PORTADA_HTML)
    assert rota is False and motivo is None

    rota, motivo = portada_parece_rota(
        "<!doctype html><html><body><div>sin tarjetas</div></body></html>"
    )
    assert rota is True
    assert motivo is not None and "article" in motivo


def test_un_429_servido_como_texto_cuenta_como_portada_rota() -> None:
    """Un puente o un WAF que responde "429 Too Many Requests" en texto plano con
    HTTP 200 es el fallo silencioso clásico. Ojo con el detalle de
    implementación: BeautifulSoup envuelve el texto suelto en `<html><body>`, así
    que quien lo atrapa NO es la guarda de "esto no es HTML" sino la ausencia de
    contenedores. Lo que importa es que se detecta; el motivo exacto es
    diagnóstico, no contrato."""
    rota, motivo = portada_parece_rota("429 Too Many Requests")
    assert rota is True
    assert motivo is not None


# --- Identidad ---------------------------------------------------------------


def test_el_external_id_sale_de_la_identidad_y_no_del_texto() -> None:
    """Un medio corrige y actualiza sus notas varias veces el mismo día. Un id
    derivado del texto convertiría cada corrección en una emergencia nueva."""
    original = _item(titular="Choque en la Ruta 68")
    corregida = _item(titular="Choque múltiple en la Ruta 68 deja tres heridos")
    assert external_id_for(original) == external_id_for(corregida)


def test_dos_medios_cubriendo_el_mismo_hecho_son_dos_senales() -> None:
    """Es lo que permite que el motor los corrobore entre sí. Colapsarlos en un
    id compartido borraría justamente la corroboración."""
    uno = _item(portal="sitiodelsuceso", guid="g", link="https://a/x")
    otro = _item(portal="puranoticia", guid="g", link="https://a/x")
    assert external_id_for(uno) != external_id_for(otro)
    assert external_id_for(uno).startswith("prensa:sitiodelsuceso:")


def test_sin_guid_manda_el_enlace() -> None:
    """El camino HTML no da `<guid>`; la URL es igual de estable."""
    item = _item(guid=None, link="https://puranoticia.pnt.cl/region-valparaiso/x")
    assert external_id_for(item).startswith("prensa:sitiodelsuceso:")


# --- Delta fetching ----------------------------------------------------------


class _RepoFalso:
    """Doble de `EventRepository`: registra la llamada y responde lo conocido."""

    def __init__(self, conocidos: set[str]) -> None:
        self.conocidos = conocidos
        self.llamadas = 0

    async def ids_by_external_id(self, source, external_ids):
        self.llamadas += 1
        return {eid: 1 for eid in external_ids if eid in self.conocidos}


class _ServicioFalso:
    def __init__(self, repo: _RepoFalso) -> None:
        self.repo = repo


def _collector_con(conocidos: set[str]) -> tuple[LocalNewsCollector, _RepoFalso]:
    collector = LocalNewsCollector.__new__(LocalNewsCollector)
    collector.source = EventSource.MEDIA
    collector.name = "prensa_local"
    repo = _RepoFalso(conocidos)
    collector.service = _ServicioFalso(repo)  # type: ignore[attr-defined]
    return (collector, repo)


def test_unseen_descarta_lo_ya_procesado_con_una_sola_consulta() -> None:
    """La razón de ser del módulo: una noticia queda en portada durante días y sin
    esto cada corrida —cuatro por hora— la volvería a mandar al modelo."""
    viejo = _item(guid="viejo")
    nuevo = _item(guid="nuevo")
    collector, repo = _collector_con({external_id_for(viejo)})

    nuevas = asyncio.run(collector.unseen([viejo, nuevo, viejo]))

    assert [n.guid for n in nuevas] == ["nuevo"]
    # Una consulta por corrida y para los DOS portales juntos: si esto sube, el
    # delta fetching dejó de ser una optimización y pasó a ser un N+1.
    assert repo.llamadas == 1


def test_unseen_sin_noticias_no_toca_la_base() -> None:
    collector, repo = _collector_con(set())
    assert asyncio.run(collector.unseen([])) == []
    assert repo.llamadas == 0


# --- Aislamiento de portales -------------------------------------------------


def _collector_de_red(portales: list[NewsPortal]) -> LocalNewsCollector:
    collector = LocalNewsCollector.__new__(LocalNewsCollector)
    collector.source = EventSource.MEDIA
    collector.name = "prensa_local"
    collector.portales = portales
    collector.max_items = 40
    collector.max_age = 240
    collector.max_geocodes = 0  # ninguna geocodificación: este test es de red
    collector.max_llm_calls = 0
    collector.confidence = 0.60
    collector.timeout = 5.0
    collector.service = _ServicioFalso(_RepoFalso(set()))  # type: ignore[attr-defined]
    return collector


@respx.mock
def test_un_portal_caido_no_se_lleva_al_otro() -> None:
    """El requisito operativo: el CRON no se detiene. Y no por tragarse el error
    —eso haría que dos portales muertos se vieran igual que dos portales sin
    noticias— sino por atribuirlo al medio que lo produjo."""
    respx.get("https://www.sitiodelsuceso.cl/feed/").mock(
        return_value=httpx.Response(200, text=_feed_reciente())
    )
    respx.get("https://puranoticia.pnt.cl/region-valparaiso").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    collector = _collector_de_red([SITIO, PURA])
    resueltas = asyncio.run(collector.fetch())

    # El rescate del feed sobrevivió; la caída de Pura Noticia quedó a la vista.
    assert len(resueltas) == 1
    assert resueltas[0].event_type is EventType.RESCUE
    assert any("Pura Noticia" in aviso for aviso in collector.warnings)


@respx.mock
def test_si_caen_todos_la_corrida_falla_en_rojo() -> None:
    """Una corrida `success` con cero noticias y una fuente muerta se ven
    idénticas desde el tablero. Sólo una de las dos se arregla."""
    respx.get("https://www.sitiodelsuceso.cl/feed/").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://www.sitiodelsuceso.cl/").mock(return_value=httpx.Response(500))
    respx.get("https://puranoticia.pnt.cl/region-valparaiso").mock(
        return_value=httpx.Response(500)
    )

    collector = _collector_de_red([SITIO, PURA])
    with pytest.raises(CollectorError, match="ningún portal de prensa respondió"):
        asyncio.run(collector.fetch())


@respx.mock
def test_el_feed_vacio_cae_a_la_portada_y_lo_avisa() -> None:
    """Un feed válido y vacío pasa todas las comprobaciones de formato y produce
    cero noticias, igual que una madrugada tranquila. Un diario regional no pasa
    horas sin publicar: se usa el respaldo y la corrida queda `partial`."""
    vacio = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Sitio del Suceso</title></channel></rss>"
    )
    respx.get("https://www.sitiodelsuceso.cl/feed/").mock(
        return_value=httpx.Response(200, text=vacio)
    )
    respx.get("https://www.sitiodelsuceso.cl/").mock(
        return_value=httpx.Response(200, text=PORTADA_HTML)
    )

    collector = _collector_de_red([SITIO])
    asyncio.run(collector.fetch())

    avisos = " ".join(collector.warnings)
    assert "no trae ninguna entrada" in avisos
    assert "se leyó por html" in avisos


@respx.mock
def test_el_navegador_se_declara_en_las_cabeceras() -> None:
    """El `User-Agent` por defecto de httpx es lo primero que mira una regla de
    Cloudflare, y su respuesta es un 403 —o un desafío con HTTP 200, que es peor
    porque parece una página."""
    ruta = respx.get("https://www.sitiodelsuceso.cl/feed/").mock(
        return_value=httpx.Response(200, text=_feed_reciente())
    )

    collector = _collector_de_red([SITIO])
    asyncio.run(collector.fetch())

    enviadas = ruta.calls[0].request.headers
    assert "Mozilla/5.0" in enviadas["user-agent"]
    assert "httpx" not in enviadas["user-agent"].lower()
    assert enviadas["accept-language"].startswith("es-CL")


# --- Normalización -----------------------------------------------------------


def _normalize(records):
    collector = LocalNewsCollector.__new__(LocalNewsCollector)
    collector.source = EventSource.MEDIA
    collector.name = "prensa_local"
    collector.confidence = 0.60
    return collector.normalize(records)


def _resuelta(**kwargs) -> ResolvedNews:
    base = {
        "item": _item(),
        "event_type": EventType.ACCIDENT,
        "streets": {},
        "point": None,
    }
    base.update(kwargs)
    return ResolvedNews(**base)  # type: ignore[arg-type]


def test_normalize_sin_punto_conserva_la_senal() -> None:
    """Una noticia sin coordenadas entra igual: se pierde el Paso A del motor, no
    el hecho."""
    eventos = _normalize([_resuelta()])

    assert len(eventos) == 1
    assert eventos[0].lat is None and eventos[0].lon is None
    assert eventos[0].source is EventSource.MEDIA
    assert eventos[0].confidence == 0.60


def test_la_confianza_emitida_es_la_que_el_motor_va_a_usar() -> None:
    """`SOURCE_BASE_CONFIDENCE[MEDIA]` dice 0.70 y `RULES[MEDIA].max_weight` dice
    0.60. Emitir 0.70 archivaría en `raw_events` un número que el motor recorta
    igual."""
    from app.services.correlation.confidence import RULES

    eventos = _normalize([_resuelta()])
    assert eventos[0].confidence == RULES[EventSource.MEDIA].max_weight


def test_una_noticia_de_ayer_no_se_estampa_como_si_pasara_ahora() -> None:
    """El peor error posible de esta fuente, y el que nadie notaría: el punto se
    vería perfectamente normal mientras corrobora con 0.60 un incidente con el
    que no tiene nada que ver."""
    ayer = _item(
        published_at=datetime(2026, 8, 30, tzinfo=UTC),
        resolucion_dia=True,
    )
    evento = _normalize([_resuelta(item=ayer)])[0]

    assert evento.timestamp.date() == ayer.published_at.date()
    assert (datetime.now(UTC) - evento.timestamp) > timedelta(hours=4)


def test_una_noticia_de_hoy_sin_hora_usa_la_hora_de_primera_lectura() -> None:
    """El delta garantiza que se procesa una sola vez, la primera que se ve: esa
    hora es una cota superior de la publicación con error de una cadencia."""
    hoy = _item(
        published_at=datetime.now(UTC).replace(hour=0, minute=0, second=0),
        resolucion_dia=True,
    )
    evento = _normalize([_resuelta(item=hoy)])[0]
    assert (datetime.now(UTC) - evento.timestamp) < timedelta(minutes=1)


def test_normalize_recorta_el_timestamp_futuro() -> None:
    """Un reloj adelantado en la fuente haría fallar la validación de
    `EventCreate` y perdería el lote entero por un ítem."""
    futuro = _item(published_at=datetime.now(UTC) + timedelta(hours=3))
    evento = _normalize([_resuelta(item=futuro)])[0]
    assert evento.timestamp <= datetime.now(UTC)


def test_normalize_separa_extraccion_de_geocodificacion() -> None:
    """Cuando mañana un punto esté mal hay que poder distinguir si el modelo leyó
    mal la calle o si Nominatim la resolvió a otra comuna."""
    evento = _normalize(
        [_resuelta(streets={"street_1": "Av. España", "city": "Viña del Mar"})]
    )[0]

    assert evento.raw_data["_extraction"]["street_1"] == "Av. España"
    assert evento.raw_data["_geocoding"] is None
    assert evento.raw_data["comuna"] == "Viña del Mar"
    assert evento.raw_data["_prensa"]["medio"] == "Sitio del Suceso"
    assert evento.raw_data["url"].startswith("https://")


def test_la_comuna_declarada_queda_registrada_aunque_el_modelo_no_la_use() -> None:
    evento = _normalize([_resuelta()])[0]
    assert evento.raw_data["_prensa"]["comuna_declarada"] == "Viña del Mar"
    # Sin extracción, la comuna del `<category>` es lo único que hay.
    assert evento.raw_data["comuna"] == "Viña del Mar"


def test_un_accidente_de_prensa_no_se_funde_con_un_incendio() -> None:
    """La partición del motor separa `traffic` de `fire`: dos hechos distintos en
    la misma manzana siguen siendo dos puntos."""
    assert family_of_event(EventType.ACCIDENT) == "traffic"
    assert family_of_event(EventType.STRUCTURAL_FIRE) == "fire"
