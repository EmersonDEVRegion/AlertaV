"""La salud por familia: el módulo que existe por un accidente que no se vio.

El 2026-09-02 el Actor de Instagram estuvo detenido dos horas. El collector
siguió corriendo cada cinco minutos sin fallar —lee el dataset de la última
corrida del Actor, que sigue ahí— y `collector_runs` guardaba el diagnóstico
redactado. El mapa mostró «Accidentes viales · 0», idéntico al de un día
tranquilo, mientras se publicaba un choque en Avenida España.

Casi todos estos tests son ese caso, mirado desde distintos ángulos.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import CollectorStatus
from app.models.event import CollectorRun
from app.services.collector_health import build_health

AHORA = datetime(2026, 9, 2, 22, 0, tzinfo=UTC)


def corrida(
    collector: str,
    *,
    estado: CollectorStatus = CollectorStatus.SUCCESS,
    hace_minutos: float = 1,
    error: str | None = None,
) -> CollectorRun:
    run = CollectorRun()
    run.collector = collector
    run.status = estado.value
    run.started_at = AHORA - timedelta(minutes=hace_minutos)
    run.finished_at = AHORA - timedelta(minutes=hace_minutos)
    run.error = error
    return run


def familia(ultimas: dict[str, CollectorRun], nombre: str) -> str:
    _, por_familia = build_health(ultimas, ahora=AHORA)
    return por_familia[nombre]


# --- 1. El caso que motivó el módulo -----------------------------------------


def test_una_corrida_reciente_pero_ciega_no_es_salud():
    """El corazón del asunto.

    La corrida terminó hace un minuto: cualquier chequeo de recencia la daría
    por sana. Y sin embargo esa capa no ve, porque lo que leyó es de hace dos
    horas. Sólo el propio collector puede saberlo, y por eso lo declara.
    """
    ultimas = {
        "instagram_apify": corrida(
            "instagram_apify",
            estado=CollectorStatus.DEGRADED,
            hace_minutos=1,
            error="datos rancios: la última corrida exitosa del Actor terminó hace 136 min",
        )
    }

    salud, _ = build_health(ultimas, ahora=AHORA)
    instagram = next(s for s in salud if s.collector == "instagram_apify")

    assert instagram.status == "degraded"
    assert instagram.age_seconds is not None and instagram.age_seconds < 120
    assert "136 min" in (instagram.detail or ""), "el motivo tiene que llegar a la ficha"


def test_partial_permanente_no_ensucia_la_salud():
    """El USGS descarta 235 sismos por corrida y está perfectamente sano.

    Si `partial` contara como problema, media interfaz quedaría amarilla para
    siempre y el aviso dejaría de significar algo — que es exactamente cómo se
    perdió de vista la capa de Instagram.
    """
    ultimas = {
        "conaf_incendios": corrida("conaf_incendios", estado=CollectorStatus.PARTIAL),
        "nasa_firms_area": corrida("nasa_firms_area", estado=CollectorStatus.PARTIAL),
    }

    assert familia(ultimas, "fire") == "ok"


# --- 2. La regla de agregación ----------------------------------------------


def test_el_escenario_exacto_del_2_de_septiembre():
    """La reconstrucción del día que motivó todo esto.

    Instagram ciego, Transporte Informa publicando con normalidad, prensa
    corriendo. La primera versión de este módulo daba `traffic: ok` acá —tomaba
    el estado de la fuente más sana— y por lo tanto NO habría atrapado el caso
    para el que se escribió. Este test existe para que eso no vuelva.
    """
    ultimas = {
        "instagram_apify": corrida(
            "instagram_apify", estado=CollectorStatus.DEGRADED, hace_minutos=1
        ),
        "transporte_informa": corrida("transporte_informa", hace_minutos=2),
        "prensa_local": corrida("prensa_local", estado=CollectorStatus.PARTIAL),
    }

    assert familia(ultimas, "traffic") == "degraded"


def test_una_fuente_de_apoyo_sana_no_rescata_a_la_familia():
    """El MTT emite sobre todo `road_closure`, que no crea incidentes.

    Que publique con normalidad no significa que un choque se vaya a ver, así
    que su salud no puede tapar la de quien sí reporta choques.
    """
    ultimas = {
        "transporte_informa": corrida("transporte_informa"),
        "instagram_apify": corrida("instagram_apify", estado=CollectorStatus.FAILED),
        "prensa_local": corrida("prensa_local", estado=CollectorStatus.FAILED),
    }

    assert familia(ultimas, "traffic") == "failing"


def test_una_principal_caida_basta_aunque_otra_vea():
    """CGE y Chilquinta son territorios distintos, no redundancia.

    Con CGE caída, los cortes del valle del Aconcagua no aparecen por mucho que
    Chilquinta esté impecable. Un cero ahí no es calma.
    """
    ultimas = {
        "chilquinta_cortes": corrida("chilquinta_cortes"),
        "cge_cortes": corrida("cge_cortes", estado=CollectorStatus.FAILED),
    }

    assert familia(ultimas, "power") == "failing"


def test_una_fuente_de_apoyo_caida_no_ensucia_a_la_familia():
    """FIRMS emite `thermal_anomaly` con confianza baja y no confirma nada.

    Si CONAF ve, los incendios se están viendo. Marcar la familia por su peor
    fuente daría rojo permanente y sería el falso positivo que entrena a
    ignorar el aviso.
    """
    ultimas = {
        "conaf_incendios": corrida("conaf_incendios"),
        "nasa_firms_area": corrida("nasa_firms_area", estado=CollectorStatus.FAILED),
        "instagram_apify": corrida("instagram_apify", estado=CollectorStatus.DEGRADED),
    }

    assert familia(ultimas, "fire") == "ok"


def test_una_fuente_que_nunca_corrio_no_enciende_el_aviso():
    """`never` es un hueco de configuración, no una regresión.

    `bomberos_apify_webhook` no entrega hasta que el Actor de X esté andando. Si
    eso encendiera la marca, tres familias quedarían señaladas para siempre y en
    dos semanas nadie la miraría — la misma muerte que tuvo `partial`.
    """
    ultimas = {"conaf_incendios": corrida("conaf_incendios")}

    assert familia(ultimas, "fire") == "ok", "bomberos nunca corrió y no debe pesar"


def test_una_familia_sin_ninguna_corrida_es_never():
    assert familia({}, "power") == "never"


def test_lo_mas_grave_gana():
    ultimas = {
        "chilquinta_cortes": corrida("chilquinta_cortes", hace_minutos=600),
        "cge_cortes": corrida("cge_cortes", estado=CollectorStatus.DEGRADED),
    }

    assert familia(ultimas, "power") == "degraded"


# --- 3. Recencia -------------------------------------------------------------


def test_un_collector_que_dejo_de_correr_queda_stale():
    """Chilquinta corre cada pocos minutos; nueve horas de silencio no son calma."""
    ultimas = {
        "chilquinta_cortes": corrida("chilquinta_cortes", hace_minutos=540),
        "cge_cortes": corrida("cge_cortes", hace_minutos=540),
    }

    assert familia(ultimas, "power") == "stale"


def test_saltarse_una_cadencia_no_alarma():
    """Un reinicio del proceso o la dispersión del runner se saltan una corrida.

    Declarar una capa ciega por eso sería el canal que grita siempre.
    """
    ultimas = {
        "chilquinta_cortes": corrida("chilquinta_cortes", hace_minutos=6),
        "cge_cortes": corrida("cge_cortes", hace_minutos=6),
    }

    assert familia(ultimas, "power") == "ok"


def test_una_fuente_de_cadencia_larga_tiene_piso():
    """El MOP corre cada hora y se actualiza los lunes.

    Sin el piso, tres cadencias suyas serían tres horas; con él, el umbral no
    baja de quince minutos para nadie, pero tampoco sube por encima de lo que su
    propia cadencia justifica.
    """
    salud, _ = build_health(
        {"senapred_alertas": corrida("senapred_alertas", hace_minutos=10)},
        ahora=AHORA,
    )
    senapred = next(s for s in salud if s.collector == "senapred_alertas")

    assert senapred.status == "ok"


# --- 4. El webhook, que no está en COLLECTORS --------------------------------


def test_el_webhook_de_bomberos_aparece_aunque_no_lo_dispare_el_runner():
    """Es el pilar de dos familias y no está en `COLLECTORS`.

    Omitirlo por no ser un collector del runner dejaría fuera del cuadro
    justamente la fuente cuya caída más importa.
    """
    salud, _ = build_health({}, ahora=AHORA)
    nombres = {s.collector for s in salud}

    assert "bomberos_apify_webhook" in nombres
    webhook = next(s for s in salud if s.collector == "bomberos_apify_webhook")
    assert "traffic" in webhook.families
    assert webhook.expected_interval_seconds > 0, "sin cadencia no hay umbral"


def test_toda_familia_tiene_al_menos_una_fuente_principal():
    """Una familia sólo con fuentes de apoyo nunca podría estar sana.

    `_estado_de_familia` mira exclusivamente las principales: si una familia no
    declara ninguna, la lista llega vacía y devuelve `never` para siempre. Se
    leería como avería permanente y sería un error de configuración de este
    módulo, no del sistema.
    """
    from app.services.collector_health import COLLECTOR_ROLES, FAMILIES

    for familia_ in FAMILIES:
        principales = [
            nombre
            for nombre, roles in COLLECTOR_ROLES.items()
            if roles.get(familia_) == "principal"
        ]
        assert principales, f"{familia_} no tiene ninguna fuente principal"


def test_toda_puerta_que_escribe_en_collector_runs_esta_declarada():
    """El olvido que este test impide que se repita.

    `prensa_x_webhook` se construyó, se desplegó y escribió en `collector_runs`
    durante horas sin estar en `COLLECTOR_ROLES`. Consecuencia: su caída no
    movía el estado de ninguna familia, o sea que la fuente podía morir en
    silencio — exactamente lo que este módulo existe para impedir.

    La lista se declara a mano y no se deriva de `COLLECTORS` porque las dos
    puertas de webhook no están ahí: no las dispara el runner, las empuja Apify.
    """
    from app.services.collector_health import COLLECTOR_ROLES

    puertas_por_webhook = {"bomberos_apify_webhook", "prensa_x_webhook"}

    for nombre in puertas_por_webhook:
        assert nombre in COLLECTOR_ROLES, (
            f"{nombre} escribe en collector_runs y no está en COLLECTOR_ROLES: "
            f"su caída no movería ninguna familia"
        )


def test_los_roles_declarados_son_los_dos_que_existen():
    """Un typo en el rol degradaría la fuente a apoyo en silencio."""
    from app.services.collector_health import COLLECTOR_ROLES

    for nombre, roles in COLLECTOR_ROLES.items():
        for familia_, rol in roles.items():
            assert rol in ("principal", "apoyo"), f"{nombre}/{familia_}: rol {rol!r}"
