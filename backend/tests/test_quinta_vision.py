"""Quinta Visión Ahora en la rotación de prensa local (§L, 2026-09-30).

El feed de abajo reproduce la estructura real de
`www.quintavisionahora.cl/feed/` verificada ese día: `<category>` trae la
sección («Destacados», «Nacional»), la región («Región Valparaíso»), la comuna y
etiquetas libres. El feed mezcla región y país, y lo que separa lo de la V
Región es la etiqueta «Región Valparaíso»: `categoria_requerida`.
"""

from __future__ import annotations

import pytest

from app.collectors.news.local_news_worker import NewsPortal, parse_feed, parse_portals
from app.core.config import settings

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Quinta Visión Ahora</title>
<link>https://www.quintavisionahora.cl/</link>
<item>
<title>AHORA: Peatón sufre graves lesiones tras ser atropellado por bus en Valparaíso</title>
<link>https://www.quintavisionahora.cl/2026/09/30/ahora-peaton-atropellado-bus-valparaiso/</link>
<pubDate>Wed, 30 Sep 2026 14:32:22 +0000</pubDate>
<category><![CDATA[Destacados]]></category>
<category><![CDATA[Región Valparaíso]]></category>
<category><![CDATA[Valparaíso]]></category>
<category><![CDATA[Atropello]]></category>
<guid isPermaLink="false">https://www.quintavisionahora.cl/?p=160309</guid>
<description><![CDATA[<p>Un peatón resultó con lesiones graves.</p>]]></description>
</item>
<item>
<title>Incendio forestal consume 200 hectáreas en la región del Maule</title>
<link>https://www.quintavisionahora.cl/2026/09/30/incendio-forestal-maule/</link>
<pubDate>Wed, 30 Sep 2026 14:22:11 +0000</pubDate>
<category><![CDATA[Destacados]]></category>
<category><![CDATA[Nacional]]></category>
<category><![CDATA[Chile]]></category>
<guid isPermaLink="false">https://www.quintavisionahora.cl/?p=160293</guid>
<description><![CDATA[<p>Brigadas de CONAF combaten el fuego.</p>]]></description>
</item>
<item>
<title>Festival de Ciencia y Tecnología 2026 llegará a Villa Alemana</title>
<link>https://www.quintavisionahora.cl/2026/09/30/festival-ciencia-villa-alemana/</link>
<pubDate>Wed, 30 Sep 2026 15:27:10 +0000</pubDate>
<category><![CDATA[Destacados]]></category>
<category><![CDATA[REGION VALPARAISO]]></category>
<category><![CDATA[Villa Alemana]]></category>
<guid isPermaLink="false">https://www.quintavisionahora.cl/?p=160312</guid>
<description><![CDATA[<p>En el Centro Cultural Gabriela Mistral.</p>]]></description>
</item>
</channel>
</rss>
"""


def _portal() -> NewsPortal:
    return {p.slug: p for p in parse_portals(settings.LOCAL_NEWS_SOURCES)}["quintavision"]


def test_esta_en_la_rotacion_por_defecto_solo_por_feed() -> None:
    portal = _portal()
    assert portal.feed_url == "https://www.quintavisionahora.cl/feed/"
    assert portal.portada_url is None
    # Medio con redacción: la confianza por defecto, no la de un agregador.
    assert portal.confianza is None
    assert portal.categoria_requerida == "Región Valparaíso"


def test_quinta_prensa_salio_de_la_rotacion() -> None:
    slugs = [p.slug for p in parse_portals(settings.LOCAL_NEWS_SOURCES)]
    assert "quintaprensa" not in slugs
    assert slugs == ["alertanoticias", "puranoticia", "margamarga", "quintavision"]


def test_solo_entra_lo_de_la_region_y_con_su_comuna() -> None:
    noticias = {n.guid: n for n in parse_feed(FEED, _portal())}

    # La nota nacional queda fuera aunque su titular diga «incendio».
    assert "https://www.quintavisionahora.cl/?p=160293" not in noticias
    atropello = noticias["https://www.quintavisionahora.cl/?p=160309"]
    assert atropello.comuna_hint == "Valparaíso"
    # La etiqueta se compara normalizada: sin tildes ni mayúsculas.
    assert noticias["https://www.quintavisionahora.cl/?p=160312"].comuna_hint == "Villa Alemana"


def test_sin_filtro_entra_todo() -> None:
    portal = NewsPortal(
        slug="qv", nombre="QV", feed_url="https://www.quintavisionahora.cl/feed/", portada_url=None
    )
    assert len(parse_feed(FEED, portal)) == 3


def test_el_sexto_campo_se_lee_y_no_admite_portada() -> None:
    [portal] = parse_portals("medio|Medio|https://a/feed/|||Región X")
    assert portal.categoria_requerida == "Región X"
    assert portal.confianza is None
    with pytest.raises(ValueError, match="categoría"):
        parse_portals("medio|Medio|https://a/feed/|https://a/||Región X")
