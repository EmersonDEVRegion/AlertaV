"""El día del hecho, leído del texto.

El 2026-09-03, un jueves, @alertanoticiasvalparaiso publicó un atropello ocurrido
"durante la mañana del domingo". El post tenía tres horas y los filtros de
frescura miden la publicación, así que el mapa lo mostró como un accidente
activo, cuatro días después.

Lo que este archivo fija es la regla del módulo: **descartar sólo lo que se
puede probar viejo**. Cada caso que pasa es tan importante como cada caso que se
descarta: un falso negativo pierde un siniestro.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.collectors.dia_del_hecho import (
    habla_del_presente,
    hecho_fuera_de_ventana,
    parse_dia_declarado,
)

#: Jueves 3 de septiembre de 2026, 11:00 en Chile (UTC-4 hasta el cambio de
#: hora del primer fin de semana de septiembre).
PUBLICADO = datetime(2026, 9, 3, 15, 0, tzinfo=UTC)
AHORA = PUBLICADO + timedelta(hours=3)
VENTANA = 180  # APIFY_WEBHOOK_MAX_AGE_MINUTES

#: El texto real de la captura, recortado a lo que importa.
ATROPELLO = (
    "Una mujer resultó gravemente lesionada luego de ser atropellada por un "
    "vehículo cuyo conductor se dio a la fuga durante la mañana del domingo en Av. "
    "Errázuriz, en Valparaíso. El hecho ocurrió cerca de las 07:40 horas, en el "
    "tramo comprendido entre las calles Freire y Rodríguez. Luego de lo ocurrido, "
    "se realizó la denuncia ante Carabineros y los antecedentes fueron puestos en "
    "conocimiento del Ministerio Público."
)


def viejo(texto: str, *, publicado: datetime = PUBLICADO, ahora: datetime = AHORA) -> bool:
    return hecho_fuera_de_ventana(
        texto, publicado=publicado, ahora=ahora, max_age_minutes=VENTANA
    )


# --- El caso que lo motivó -----------------------------------------------------


def test_el_atropello_del_domingo_publicado_el_jueves_es_viejo() -> None:
    declarado = parse_dia_declarado(ATROPELLO, publicado=PUBLICADO)

    assert declarado is not None
    assert declarado.dia == date(2026, 8, 30)  # el domingo anterior
    assert declarado.fragmento == "manana del domingo"
    assert viejo(ATROPELLO)


def test_el_incendio_de_miraflores_alto_no_es_viejo() -> None:
    """El tuit del 2026-09-03 no nombra ningún día, y además dice "a esta hora"."""
    tuit = (
        "Un incendio estructural declarado moviliza a esta hora a unidades del "
        "Cuerpo de Bomberos de Viña del Mar hasta calle once, en el sector de "
        "Miraflores Alto."
    )
    assert not viejo(tuit)


# --- Cómo se resuelve el día ---------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Choque en la madrugada del sábado", date(2026, 8, 29)),
        ("Incendio durante la noche de este martes", date(2026, 9, 1)),
        ("Colisión el pasado lunes en Quillota", date(2026, 8, 31)),
        ("Colisión el lunes pasado en Quillota", date(2026, 8, 31)),
        # "el pasado jueves" dicho un jueves es el de la semana anterior.
        ("El accidente ocurrió el pasado jueves", date(2026, 8, 27)),
        ("El accidente ocurrió el pasado 28 de agosto", date(2026, 8, 28)),
        ("Atropello ocurrido este sábado 29 de agosto", date(2026, 8, 29)),
        ("Choque ocurrido ayer en Av. España", date(2026, 9, 2)),
        ("Choque ocurrido antes de ayer en Av. España", date(2026, 9, 1)),
        ("El incendio se registró hace tres días", date(2026, 8, 31)),
    ],
)
def test_resolucion_del_dia(texto: str, esperado: date) -> None:
    declarado = parse_dia_declarado(texto, publicado=PUBLICADO)
    assert declarado is not None
    assert declarado.dia == esperado


def test_el_dia_de_la_semana_es_el_de_chile_y_no_el_de_utc() -> None:
    """Domingo 30 a las 21:30 en Valparaíso ya es lunes 31 en UTC. Leído en UTC,
    "este domingo" sería ayer; en Chile es hoy."""
    domingo_de_noche = datetime(2026, 8, 31, 1, 30, tzinfo=UTC)
    declarado = parse_dia_declarado(
        "Choque en la noche de este domingo", publicado=domingo_de_noche
    )
    assert declarado.dia == date(2026, 8, 30)


def test_una_fecha_sin_año_posterior_a_la_publicacion_es_del_año_anterior() -> None:
    dos_de_enero = datetime(2027, 1, 2, 15, 0, tzinfo=UTC)
    declarado = parse_dia_declarado(
        "El accidente ocurrió el pasado 30 de diciembre", publicado=dos_de_enero
    )
    assert declarado.dia == date(2026, 12, 30)


def test_el_dia_de_hoy_no_es_viejo() -> None:
    assert not viejo("Colisión ocurrida este jueves en Av. Argentina")


# --- La edad mínima: un día no es un instante ----------------------------------


def test_ayer_puede_ser_de_hace_una_hora() -> None:
    """Leído a la 01:00, "ayer" puede ser las 23:55: no se puede probar viejo."""
    una_de_la_manana = datetime(2026, 9, 3, 5, 0, tzinfo=UTC)  # 01:00 en Chile
    assert not viejo(
        "Choque ocurrido ayer en Av. España",
        publicado=una_de_la_manana,
        ahora=una_de_la_manana,
    )
    # A las 11:00, ayer terminó hace once horas.
    assert viejo("Choque ocurrido ayer en Av. España")


def test_anoche_se_estira_hasta_la_madrugada() -> None:
    siete_de_la_manana = datetime(2026, 9, 3, 11, 0, tzinfo=UTC)  # 07:00 en Chile
    assert not viejo(
        "Anoche se registró un choque en Viña",
        publicado=siete_de_la_manana,
        ahora=siete_de_la_manana,
    )
    assert viejo("Anoche se registró un choque en Viña")


# --- Lo que NO se descarta -------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        # El incendio empezó el domingo y sigue: es de hoy.
        "El incendio forestal que comenzó el domingo sigue sin control en Quilpué",
        "Incendio iniciado ayer aún consume hectáreas en Limache",
        "Bomberos combaten a esta hora el fuego que partió la noche del martes",
        "Hoy continúan las labores tras el derrumbe del pasado lunes",
    ],
)
def test_el_presente_anula_el_descarte(texto: str) -> None:
    assert habla_del_presente(texto)
    assert not viejo(texto)


@pytest.mark.parametrize(
    "texto",
    [
        # Un día de la semana suelto puede ser el anuncio de otra cosa.
        "Choque en Av. España; el concejo se reunirá el lunes para analizar el cruce",
        # Una fecha suelta puede ser contexto.
        "Como el incendio del 2 de febrero de 2024, el fuego avanzó rápido",
        # Sin día declarado no hay nada que probar.
        "Colisión frontal entre dos vehículos deja dos lesionados",
    ],
)
def test_lo_que_no_esta_anclado_al_hecho_no_cuenta(texto: str) -> None:
    assert not viejo(texto)


def test_sin_texto_no_se_descarta_nada() -> None:
    assert not hecho_fuera_de_ventana(None, publicado=PUBLICADO, ahora=AHORA, max_age_minutes=1)


# --- Los tres collectors de texto lo aplican -------------------------------------


def test_la_prensa_descarta_la_nota_reciente_de_un_hecho_viejo() -> None:
    from app.collectors.news.local_news_worker import NewsItem, es_reciente

    def nota(titular: str) -> NewsItem:
        return NewsItem(
            portal="alertanoticias",
            portal_nombre="Alerta Noticias",
            titular=titular,
            bajada="",
            link="https://alertanoticias.cl/x",
            guid="x",
            published_at=AHORA - timedelta(minutes=20),
            resolucion_dia=False,
            comuna_hint="Valparaíso",
            origen="rss",
            raw={},
        )

    assert not es_reciente(nota(ATROPELLO), ahora=AHORA, max_age_minutes=240)
    assert es_reciente(
        nota("Colisión frontal en Av. España deja dos lesionados"),
        ahora=AHORA,
        max_age_minutes=240,
    )


def test_x_descarta_el_tuit_reciente_de_un_hecho_viejo() -> None:
    from app.services.apify_press_service import Tuit, is_fresh

    def tuit(texto: str) -> Tuit:
        return Tuit(
            tweet_id="1",
            handle="sitiodelsuceso",
            text=texto,
            published_at=AHORA - timedelta(minutes=30),
            raw={},
        )

    assert not is_fresh(tuit(ATROPELLO), now=AHORA, max_age_minutes=VENTANA)
    assert is_fresh(tuit("Choque en Av. España con Uno Norte"), now=AHORA, max_age_minutes=VENTANA)


def test_instagram_descarta_el_post_reciente_de_un_hecho_viejo() -> None:
    from app.collectors.social.instagram_apify_worker import InstagramPost, is_fresh

    def post(caption: str) -> InstagramPost:
        return InstagramPost(
            short_code="abc",
            username="alertanoticiasvalparaiso",
            caption=caption,
            image_url=None,
            published_at=AHORA - timedelta(hours=3),
            permalink="https://www.instagram.com/p/abc/",
            raw={},
        )

    assert not is_fresh(post(ATROPELLO), now=AHORA, max_age_minutes=VENTANA)
    assert is_fresh(post("Choque en Av. España con Uno Norte"), now=AHORA, max_age_minutes=VENTANA)
