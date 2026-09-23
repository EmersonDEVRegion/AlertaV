"""Prensa Marga Marga y Quinta Prensa en la rotación de prensa local.

Entraron el 2026-09-22, cuando se retiraron de Apify los Tasks de Instagram y
de prensa. El feed de abajo reproduce la estructura real de
`prensamargamarga.cl/feed/` verificada ese día (WordPress 7.1.2): las
categorías son la provincia y la sección —«Marga Marga», «Policial»,
«Destacado»—, nunca la comuna. De ahí el respaldo por texto de `parse_feed`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.collectors.news.local_news_worker import NewsPortal, parse_feed, parse_portals
from app.core.config import settings

MARGA = NewsPortal(
    slug="margamarga",
    nombre="Prensa Marga Marga",
    feed_url="https://prensamargamarga.cl/feed/",
    portada_url=None,
)

FEED_MARGA = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Prensa Marga Marga</title>
<link>https://prensamargamarga.cl</link>
<generator>https://wordpress.org/?v=7.1.2</generator>
<item>
<title>Tragedia en Olmué: Dos personas mueren tras fatal accidente en la Ruta F-100-G</title>
<link>https://prensamargamarga.cl/2026/09/04/tragedia-en-olmue-dos-personas-mueren-tras-fatal-accidente-en-la-ruta-f-100-g/</link>
<pubDate>Fri, 04 Sep 2026 20:46:10 +0000</pubDate>
<guid isPermaLink="false">https://prensamargamarga.cl/?p=4545</guid>
<category><![CDATA[Destacado]]></category>
<description><![CDATA[<p>Un violento choque se registró en la Ruta F-100-G&#8230;</p>]]></description>
</item>
<item>
<title>SIP de Carabineros Limache detiene a sujeto que portaba una pistola cargada</title>
<link>https://prensamargamarga.cl/2026/09/10/sip-de-carabineros-limache-detiene/</link>
<pubDate>Thu, 10 Sep 2026 02:41:54 +0000</pubDate>
<guid isPermaLink="false">https://prensamargamarga.cl/?p=4552</guid>
<category><![CDATA[Marga Marga]]></category>
<category><![CDATA[Policial]]></category>
<description><![CDATA[<p>Un hombre de 42 años fue detenido&#8230;</p>]]></description>
</item>
<item>
<title>Concejales solicitan retén carretero en Ruta Lo Orozco</title>
<link>https://prensamargamarga.cl/2026/09/09/concejales-solicitan-reten/</link>
<pubDate>Wed, 09 Sep 2026 14:19:43 +0000</pubDate>
<guid isPermaLink="false">https://prensamargamarga.cl/?p=4550</guid>
<category><![CDATA[Política]]></category>
<description><![CDATA[<p>La solicitud fue entregada durante la visita del ministro&#8230;</p>]]></description>
</item>
</channel>
</rss>
"""


def test_los_dos_portales_nuevos_estan_en_la_rotacion_por_defecto():
    portales = {p.slug: p for p in parse_portals(settings.LOCAL_NEWS_SOURCES)}

    assert portales["margamarga"].feed_url == "https://prensamargamarga.cl/feed/"
    assert portales["quintaprensa"].feed_url == "https://www.quintaprensa.cl/feed/"


def test_los_portales_nuevos_no_tienen_portada_de_respaldo():
    """La portada de Prensa Marga Marga es un ticker de posts SIN fecha, el más
    viejo de mayo de 2025. El camino HTML estampa lo que no trae fecha con la
    hora de la corrida: con un feed caído, un homicidio de hace un año entraría
    como emergencia de hoy. Sin portada, un feed caído es un aviso y nada más."""
    portales = {p.slug: p for p in parse_portals(settings.LOCAL_NEWS_SOURCES)}

    assert portales["margamarga"].portada_url is None
    assert portales["quintaprensa"].portada_url is None


def test_los_portales_de_antes_siguen_donde_estaban():
    slugs = [p.slug for p in parse_portals(settings.LOCAL_NEWS_SOURCES)]
    assert slugs[:2] == ["alertanoticias", "puranoticia"]


def test_el_feed_real_de_marga_marga_se_lee():
    noticias = parse_feed(FEED_MARGA, MARGA)

    assert len(noticias) == 3
    primera = noticias[0]
    assert primera.titular.startswith("Tragedia en Olmué")
    assert primera.guid == "https://prensamargamarga.cl/?p=4545"
    assert primera.published_at == datetime(2026, 9, 4, 20, 46, 10, tzinfo=UTC)
    assert primera.origen == "rss"


def test_sin_comuna_en_las_categorias_se_lee_del_texto():
    """«Marga Marga» es la provincia, no una comuna. Sin el respaldo, el
    accidente de Olmué llegaba al geocodificador sin ninguna comuna."""
    noticias = {n.guid: n for n in parse_feed(FEED_MARGA, MARGA)}

    assert noticias["https://prensamargamarga.cl/?p=4545"].comuna_hint == "Olmué"
    assert noticias["https://prensamargamarga.cl/?p=4552"].comuna_hint == "Limache"
    # Nada que leer: queda en None y no se inventa.
    assert noticias["https://prensamargamarga.cl/?p=4550"].comuna_hint is None
