"""La hora declarada y la ventana de congestión.

Las dos piezas de «convertirnos en Waze sin la API de Waze»: leer del texto
cuándo ocurrió el hecho, y estimar cuánto va a durar el taco.

Ninguna de las dos mide nada. Buena parte de estos tests existe para fijar
justamente eso: dónde el sistema tiene que callar en vez de inventar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.collectors.horas import Precision, parse_hora_declarada
from app.services.congestion import (
    ARTERIAS,
    CHILE_TZ,
    arteria_de,
    es_hora_punta,
    estimar,
)


def local(hora: int, minuto: int = 0, *, dia: int = 2) -> datetime:
    """Un instante en hora de Chile, devuelto en UTC como vive en la base."""
    return datetime(2026, 9, dia, hora, minuto, tzinfo=CHILE_TZ).astimezone(UTC)


# =============================================================================
#  1. La hora que declara el texto
# =============================================================================


@pytest.mark.parametrize(
    ("texto", "hora", "minuto", "precision"),
    [
        ("El accidente ocurrió a las 14:30 horas.", 14, 30, Precision.EXACTA),
        ("Cerca de las 14 horas se registró la colisión.", 14, 0, Precision.APROXIMADA),
        ("Pasadas las 17:45 horas.", 17, 45, Precision.APROXIMADA),
        ("Aproximadamente al mediodía de este martes.", 12, 30, Precision.FRANJA),
        ("Durante la madrugada de este miércoles.", 3, 30, Precision.FRANJA),
        ("En horas de la tarde se registró un choque.", 16, 0, Precision.FRANJA),
    ],
)
def test_lee_la_hora_y_su_precision(texto, hora, minuto, precision):
    resultado = parse_hora_declarada(texto)

    assert resultado is not None
    assert (resultado.hora, resultado.minuto) == (hora, minuto)
    assert resultado.precision is precision


@pytest.mark.parametrize(
    ("texto", "esperada"),
    [
        ("El hecho se produjo a las 2 de la tarde.", 14),
        ("A las 8 de la mañana, dos vehículos chocaron.", 8),
        ("A las 11 de la noche en Vía Las Palmas.", 23),
        ("A las 12 de la madrugada.", 0),
        ("A las 3 de la madrugada.", 3),
    ],
)
def test_el_sufijo_del_dia_corrige_el_reloj(texto, esperada):
    """«A las 2 de la tarde» son las 14, no las 02.

    Es el error más peligroso de este módulo porque **no se ve raro**: una
    ventana doce horas desplazada tiene el mismo aspecto que una correcta, y
    mandaría a alguien a evitar una calle que lleva medio día despejada.
    """
    assert parse_hora_declarada(texto).hora == esperada


def test_sin_hora_declarada_devuelve_none():
    """Inventarla a partir de la publicación es lo que este módulo evita.

    Entre que ocurre un accidente y que la prensa lo publica pasa una hora
    larga. Usar la hora de publicación como hora del hecho produce ventanas de
    congestión sistemáticamente tarde.
    """
    assert parse_hora_declarada(
        "Un accidente de tránsito se ha registrado en Av. España, "
        "a la altura del nudo Barón."
    ) is None
    assert parse_hora_declarada("") is None


def test_una_hora_imposible_no_pasa():
    assert parse_hora_declarada("A las 47:99 del reloj roto.") is None


def test_la_franja_nunca_se_presenta_como_exacta():
    """«Durante la tarde» no es un instante y el sistema no puede fingir que sí.

    `es_estimacion` es lo que la ficha usa para no mostrar «14:00» a secas
    cuando lo único que dijo la nota fue «por la tarde».
    """
    assert parse_hora_declarada("En la tarde.").es_estimacion is True
    assert parse_hora_declarada("A las 14:30 horas.").es_estimacion is False


# =============================================================================
#  2. La ventana de congestión
# =============================================================================


def test_reconoce_las_arterias_como_las_nombra_la_prensa():
    assert arteria_de("Av. España").label == "Av. España"
    assert arteria_de("AVENIDA ESPAÑA").label == "Av. España"
    assert arteria_de("Ruta 68").label == "Ruta 68"
    assert arteria_de("Vía Las Palmas").label == "Vía Las Palmas"


def test_una_calle_fuera_de_la_tabla_no_recibe_ventana():
    """Y es la respuesta correcta para la enorme mayoría de las calles.

    Inventar una duración genérica sería el mismo error que geocodificar al
    centroide comunal: parece un dato y no lo es. Mejor callar en mil calles y
    acertar en las diez que generan taco.
    """
    assert arteria_de("Pasaje Los Aromos") is None
    assert estimar("Pasaje Los Aromos", local(14, 10)) is None
    assert estimar(None, local(14, 10)) is None


def test_gana_el_alias_mas_largo():
    """«Troncal Sur» tiene que ganarle a «Ruta 60» cuando el texto trae los dos."""
    assert arteria_de("Ruta 60 Troncal Sur").label == "Troncal Sur"


def test_en_hora_punta_la_ventana_es_mas_larga():
    fuera = estimar("Av. España", local(14, 10))
    punta = estimar("Av. España", local(18, 20))

    assert fuera is not None and punta is not None
    assert punta.duracion > fuera.duracion
    assert punta.en_punta is True and fuera.en_punta is False


def test_el_fin_de_semana_no_tiene_hora_punta():
    """El 6 de septiembre de 2026 es sábado."""
    sabado = estimar("Av. España", local(18, 20, dia=6))

    assert sabado is not None
    assert sabado.en_punta is False


def test_la_punta_se_evalua_en_hora_de_chile_y_no_en_utc():
    """Todo en esta base vive en UTC, y Chile está a tres o cuatro horas.

    Evaluar la punta sobre el reloj UTC pondría la punta de la mañana a mitad de
    la madrugada — un error que produce ventanas plausibles y equivocadas.
    """
    ocho_y_media_chile = local(8, 30)

    assert es_hora_punta(ocho_y_media_chile) is True
    # El mismo instante leído en UTC son las 11 o las 12: fuera de punta.
    assert 11 <= ocho_y_media_chile.astimezone(UTC).hour <= 12


def test_la_ventana_empieza_cuando_ocurrio_el_hecho():
    momento = local(14, 10)
    ventana = estimar("Av. España", momento)

    assert ventana.desde == momento
    assert ventana.hasta == momento + timedelta(minutes=ventana.duracion_min)


# =============================================================================
#  3. Cómo llega a la ficha
# =============================================================================


def _evento(texto: str, calle: str | None):
    from app.models.event import RawEvent

    event = RawEvent()
    event.text = texto
    event.raw_data = {"_extraction": {"street_1": calle} if calle else {}}
    return event


def _incidente(tipo, visto_en):
    from app.models.incident import Incident

    incident = Incident()
    incident.type = tipo
    incident.first_seen_at = visto_en
    return incident


def test_el_accidente_de_av_espana_recibe_su_ventana():
    """El caso del 2026-09-02, ya con calle."""
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.ACCIDENT, local(14, 40))
    eventos = [
        _evento(
            "Un accidente de tránsito se registró cerca de las 14:10 horas en "
            "Av. España, a la altura del nudo Barón.",
            "Av. España",
        )
    ]

    congestion = _congestion_for(incidente, eventos)

    assert congestion is not None
    assert congestion.road == "Av. España"
    # La ventana arranca a las 14:10 —la hora del HECHO— y no a las 14:40, que
    # es cuando la prensa lo publicó. Media hora de diferencia.
    assert congestion.starts_at.astimezone(CHILE_TZ).hour == 14
    assert congestion.starts_at.astimezone(CHILE_TZ).minute == 10
    assert congestion.source_time == "aproximada"


def test_sin_hora_en_el_texto_se_usa_la_publicacion_y_se_declara():
    """Es la peor de las bases posibles, así que tiene que ir etiquetada.

    `source_time: "publicacion"` es lo que le permite a la ficha decir que la
    ventana puede estar corrida, en vez de presentarla con la misma cara que una
    calculada sobre la hora declarada.
    """
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.ACCIDENT, local(14, 40))
    eventos = [_evento("Choque en Av. España.", "Av. España")]

    congestion = _congestion_for(incidente, eventos)

    assert congestion.source_time == "publicacion"
    assert congestion.starts_at.astimezone(CHILE_TZ).minute == 40


def test_una_hora_posterior_a_la_publicacion_se_lee_como_del_dia_anterior():
    """Una nota publicada a las 00:30 que dice «a las 23:00» habla de ayer.

    Sin la corrección, la ventana quedaría casi un día en el futuro: un taco
    anunciado para esta noche por un choque de anoche.
    """
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.ACCIDENT, local(0, 30, dia=3))
    eventos = [_evento("Choque a las 23:00 horas en Av. España.", "Av. España")]

    congestion = _congestion_for(incidente, eventos)

    inicio = congestion.starts_at.astimezone(CHILE_TZ)
    assert inicio.hour == 23
    assert inicio.day == 2, "el hecho fue ayer, no hoy"
    assert congestion.starts_at < incidente.first_seen_at


def test_un_incendio_no_recibe_ventana_de_congestion():
    """La tabla está calibrada sobre siniestros viales.

    Un incendio también corta calles, pero aplicarle estos minutos sería usar un
    número fuera del dominio donde significa algo.
    """
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.WILDFIRE, local(14, 10))
    eventos = [_evento("Incendio junto a Av. España.", "Av. España")]

    assert _congestion_for(incidente, eventos) is None


def test_un_accidente_en_una_calle_cualquiera_no_recibe_ventana():
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.ACCIDENT, local(14, 10))
    eventos = [_evento("Choque en Pasaje Los Aromos.", "Pasaje Los Aromos")]

    assert _congestion_for(incidente, eventos) is None


def test_un_accidente_sin_extraccion_no_revienta():
    from app.models.enums import IncidentType
    from app.services.incident_service import _congestion_for

    incidente = _incidente(IncidentType.ACCIDENT, local(14, 10))

    assert _congestion_for(incidente, [_evento("Choque.", None)]) is None
    assert _congestion_for(incidente, []) is None


def test_cada_arteria_declara_por_que_esta_en_la_tabla():
    """El motivo se muestra al usuario como base de la estimación.

    Obliga a que agregar una vía sea una decisión escrita y no un número
    suelto, que es la propiedad entera por la que se eligió una tabla en vez de
    preguntarle la ventana a un modelo.
    """
    for arteria in ARTERIAS:
        assert arteria.motivo.strip(), f"{arteria.label} sin motivo declarado"
        assert arteria.despeje_punta_min > arteria.despeje_min, arteria.label
        assert arteria.alias, arteria.label
