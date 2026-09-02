"""Vocabulario de emergencia centralizado.

Lo que este archivo protege son las tres cosas que la extracción puede romper en
silencio:

1. Que los workers sigan viendo los mismos nombres (`test_los_alias_*`). Una
   extracción que rompe un import path falla ruidosamente y se arregla en un
   minuto; una que cambia lo que un alias apunta no falla nunca.
2. Que los términos nuevos estén normalizados. Un término con tilde no
   coincidiría jamás y el fallo sería mudo.
3. Que el pre-filtro y el clasificador sigan siendo la misma decisión. Si se
   separan, hay texto que paga su llamada al modelo y se descarta después.

Los titulares de los casos de agua y barro no son inventados: son las formas en
que la prensa y las cuentas de la zona escriben un invierno de Valparaíso.
"""

from __future__ import annotations

import asyncio

import pytest

from app.collectors.geoservices import normalise_text
from app.collectors.vocabulary import (
    ACCIDENT_TERMS,
    AGENCY_TERMS,
    CRITICAL_TERMS,
    FIRE_TERMS,
    FLOOD_TERMS,
    HEADLINE_VERBS,
    LANDSLIDE_TERMS,
    NOISE_PHRASES,
    OPERATIONAL_TERMS,
    PRESS_NOISE_PHRASES,
    RESCUE_TERMS,
    ROAD_OPS_TERMS,
    TRAFFIC_TERMS,
    clasificar_noticia,
    clasificar_transito,
    classify_event_type,
    es_emergencia,
    es_operacion_vial,
    is_emergency,
)
from app.models.enums import EventType, family_of_event

# =============================================================================
#  Higiene del diccionario
# =============================================================================


@pytest.mark.parametrize(
    "termino",
    sorted(
        CRITICAL_TERMS
        | AGENCY_TERMS
        | OPERATIONAL_TERMS
        | ROAD_OPS_TERMS
        | set(HEADLINE_VERBS)
        | set(NOISE_PHRASES)
        | set(PRESS_NOISE_PHRASES)
    ),
)
def test_todo_el_vocabulario_esta_normalizado(termino: str) -> None:
    """Un término con tilde no coincidiría NUNCA y el fallo sería mudo.

    Todo se compara contra texto pasado por `normalise_text`, así que cada
    entrada tiene que ser ya su propia forma normalizada. Cubre las listas de
    ruido además de los términos: una frase de excisión mal escrita no excinde
    nada y tampoco avisa.
    """
    assert normalise_text(termino) == termino


def test_las_familias_nuevas_estan_en_los_terminos_criticos() -> None:
    """`FLOOD_TERMS` y `LANDSLIDE_TERMS` tienen que disparar solas.

    Si se sumaran sólo al clasificador y no a `CRITICAL_TERMS`, un anegamiento
    no pasaría el pre-filtro y el clasificador no llegaría nunca a verlo.
    """
    assert FLOOD_TERMS <= CRITICAL_TERMS
    assert LANDSLIDE_TERMS <= CRITICAL_TERMS
    assert TRAFFIC_TERMS | FIRE_TERMS | RESCUE_TERMS <= CRITICAL_TERMS


# =============================================================================
#  La capa táctica de tránsito
# =============================================================================


def test_lo_vial_operativo_no_es_una_emergencia() -> None:
    """La invariante que aísla la capa táctica del resto de la plataforma.

    `ROAD_OPS_TERMS` NO puede entrar en `CRITICAL_TERMS`. Si entrara, cada aviso
    de faena programada pasaría `is_emergency`, y ese filtro lo comparten los
    workers de prensa e Instagram: "Municipalidad anuncia repavimentación de Av.
    Alemania" se convertiría en un siniestro con la misma facilidad con que hoy
    se descarta.
    """
    # El único solape admitido es el que ya existía en `TRAFFIC_TERMS`
    # ("transito suspendido"); nada nuevo entra a los términos críticos por acá.
    assert not (ROAD_OPS_TERMS & CRITICAL_TERMS) - TRAFFIC_TERMS
    assert is_emergency("Trabajos en la calzada de Av. Alemania desde el lunes.") is False
    assert is_emergency("Desvío de tránsito por obras viales en Quilpué.") is False


def test_corte_a_secas_no_es_una_intervencion_vial() -> None:
    """El falso amigo que obligó a escribir el sustantivo de la vía en cada término.

    Este vocabulario lo comparten tres workers y en dos de ellos "corte" es casi
    siempre eléctrico. Un `"corte"` suelto en `ROAD_OPS_TERMS` habría convertido
    cada aviso de Chilquinta en una calle cerrada.
    """
    assert es_operacion_vial("Corte de luz programado en Villa Alemana.") is False
    assert es_operacion_vial("Corte de suministro de agua en Playa Ancha.") is False
    assert es_operacion_vial("Corte de calzada en Av. España por faena.") is True


def test_el_accidente_gana_sobre_el_desvio_que_provoca() -> None:
    """El orden de `clasificar_transito`, que es su decisión de diseño.

    Casi todo choque produce un desvío, así que la mayoría de los avisos de
    siniestro traen también vocabulario de cierre. Si el cierre ganara, la capa
    de accidentes perdería su fuente oficial más rápida archivándola como faena
    — y lo haría en silencio, que es lo que hace grave al fallo.
    """
    aviso = (
        "Accidente vehicular en Av. España con Uno Norte, Viña del Mar. "
        "Tránsito desviado por Errázuriz."
    )
    assert clasificar_transito(aviso) is EventType.ACCIDENT

    # Al revés no hay riesgo simétrico: una faena no menciona una colisión.
    assert (
        clasificar_transito("Trabajos en la ruta 68 entre los km 42 y 45.")
        is EventType.ROAD_CLOSURE
    )


def test_transito_suspendido_se_resuelve_hacia_el_cierre() -> None:
    """El único término que está en los dos conjuntos.

    `ACCIDENT_TERMS` se calcula como diferencia para que la ambigüedad se
    resuelva sola y de un solo lado. Un aviso que sólo dice "tránsito suspendido"
    describe una vía cerrada; cuando además hubo un choque, el texto lo dice y
    `accidente` gana porque se consulta primero.
    """
    assert set(TRAFFIC_TERMS & ROAD_OPS_TERMS) == {"transito suspendido"}
    assert "transito suspendido" not in ACCIDENT_TERMS
    assert (
        clasificar_transito("Tránsito suspendido en Cuesta Balmaceda.")
        is EventType.ROAD_CLOSURE
    )


def test_lo_que_no_es_ni_siniestro_ni_intervencion_no_pasa() -> None:
    """El portal del MTT publica bastante que no es ninguna de las dos cosas."""
    for aviso in (
        "",
        "   ",
        "Nuevo recorrido de la línea 605 desde el 1 de octubre.",
        "Paso fronterizo Los Libertadores habilitado sin restricciones.",
    ):
        assert clasificar_transito(aviso) is None


def test_el_corte_de_via_no_entra_al_motor_de_correlacion() -> None:
    """La garantía que hace segura toda la capa táctica.

    Es la razón por la que `road_closure` puede compartir fuente y confianza con
    los accidentes sin contaminarlos: no se agrupa, no genera incidente y no
    mueve ninguna confianza.
    """
    from app.models.enums import (
        CORRELATABLE_EVENT_TYPES,
        EVENT_TO_INCIDENT_TYPE,
    )

    assert EventType.ROAD_CLOSURE not in CORRELATABLE_EVENT_TYPES
    assert EventType.ROAD_CLOSURE not in EVENT_TO_INCIDENT_TYPE


def test_la_excision_no_depende_del_orden_de_la_lista() -> None:
    """La invariante que reemplazó a «de la más larga a la más corta».

    Esa regla vieja era una coincidencia disfrazada de norma: "prevencion de
    incendios forestales" y "campana de prevencion de incendios" **miden lo
    mismo** y se solapan, así que ninguna comparación de longitudes podía elegir
    entre ellas — y elegir mal deja suelto un "forestales" que es término crítico
    por sí mismo. `_excindir` une los tramos sobre el texto original, así que el
    orden dejó de importar. Este test lo comprueba barajando la lista.
    """
    from app.collectors import vocabulary

    caption = "Campaña de prevención de incendios forestales de CONAF"
    original = vocabulary.NOISE_PHRASES
    try:
        for rotacion in range(len(original)):
            vocabulary.NOISE_PHRASES = original[rotacion:] + original[:rotacion]
            assert is_emergency(caption) is False, f"rotación {rotacion}"
    finally:
        vocabulary.NOISE_PHRASES = original


def test_la_excision_borra_todas_las_apariciones() -> None:
    """La unión de tramos tiene que recorrer cada frase entera, no sólo su
    primera aparición: un caption que repite el ruido no puede colarse por la
    segunda vez que lo dice."""
    assert is_emergency("Fuegos artificiales el sábado y fuegos artificiales el domingo") is False


def test_la_excision_es_quirurgica() -> None:
    """Borrar el ruido no puede llevarse por delante la emergencia real que
    venga en el mismo texto. Es la razón por la que se excinde en vez de vetar.
    """
    assert (
        is_emergency("Tras el show de fuegos artificiales se registró un choque en Av. España")
        is True
    )
    assert is_emergency("Simulacro de aluvión el martes; hoy sí hubo un socavón real") is True


def test_el_prefiltro_sigue_siendo_sincronico() -> None:
    """Se llama dentro del bucle de `fetch()`: no puede ceder el control."""
    assert not asyncio.iscoroutinefunction(is_emergency)
    assert not asyncio.iscoroutinefunction(es_emergencia)


# =============================================================================
#  Inundación y remoción en masa
# =============================================================================


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        # Inundación
        ("Anegamiento en Avenida España a la altura de Recreo", EventType.FLOOD),
        ("Calle anegada en el centro de Viña del Mar", EventType.FLOOD),
        ("Inundación en el paso bajo nivel de Chorrillos", EventType.FLOOD),
        ("Se registra desborde del estero Marga Marga", EventType.FLOOD),
        ("Colapso de colector en Quilpué", EventType.FLOOD),
        ("Salida de cauce en el sector de Placilla", EventType.FLOOD),
        # Remoción en masa
        ("Derrumbe en el camino La Pólvora", EventType.LANDSLIDE),
        ("Deslizamiento de tierra corta la subida Santos Ossa", EventType.LANDSLIDE),
        ("Remoción en masa en la quebrada", EventType.LANDSLIDE),
        ("Socavón de tres metros en calle Independencia", EventType.LANDSLIDE),
        ("Caída de rocas en la Ruta 68", EventType.LANDSLIDE),
        ("Caída de muro en Cerro Barón", EventType.LANDSLIDE),
        ("Aluvión en la quebrada de Placilla", EventType.LANDSLIDE),
        ("Cede muro de contención en Viña del Mar", EventType.LANDSLIDE),
    ],
)
def test_el_invierno_de_valparaiso_ya_tiene_vocabulario(texto: str, esperado: EventType) -> None:
    """El vacío que este módulo vino a llenar.

    Antes de la centralización ninguna de estas catorce frases producía una
    señal: `EventType.FLOOD` y `EventType.LANDSLIDE` existían en el enum y no
    había una sola palabra que llevara hasta ellos.
    """
    assert is_emergency(texto) is True
    assert classify_event_type(texto) is esperado


def test_el_aluvion_es_remocion_en_masa_y_no_inundacion() -> None:
    """Decisión de dominio, no un descuido de orden.

    En la nomenclatura de SENAPRED un aluvión es un flujo de detritos —barro y
    piedra bajando una quebrada—, no agua acumulada. Lo que destruye no es el
    nivel del agua sino el material que arrastra.
    """
    assert classify_event_type("Aluvión baja por la quebrada") is EventType.LANDSLIDE


def test_las_dos_familias_nuevas_comparten_particion_y_no_la_del_fuego() -> None:
    """`hydro` es familia propia: el Paso A no funde un anegamiento con un
    incendio por más que compartan esquina y minuto, y sí deja que un aluvión
    corrobore al anegamiento que lo acompaña."""
    assert family_of_event(EventType.FLOOD) == family_of_event(EventType.LANDSLIDE)
    assert family_of_event(EventType.FLOOD) != family_of_event(EventType.WILDFIRE)
    assert family_of_event(EventType.FLOOD) != family_of_event(EventType.ACCIDENT)


def test_la_remocion_en_masa_gana_al_transito() -> None:
    """Por especificidad, que es la regla del bloque: `TRAFFIC_TERMS` contiene
    "accidente" y "siniestro" a secas —los dos términos más genéricos del
    archivo— y "derrumbe" es inequívoco. Un camión volcado por un derrumbe es un
    derrumbe con un camión dentro."""
    assert (
        classify_event_type("Camión volcado tras el derrumbe en la Ruta 60") is EventType.LANDSLIDE
    )


def test_anegad_y_no_anega() -> None:
    """La raíz corta habría cubierto más conjugaciones y también "fanega".

    Es la misma trampa que dejó "arde" fuera del diccionario de titulares por
    vivir dentro de "tarde". Se pagan dos entradas para no pagar ese ruido.
    """
    assert "anega" not in FLOOD_TERMS
    assert is_emergency("Remataron un predio de doce fanegas") is False


def test_desprendimiento_va_calificado() -> None:
    """ "desprendimiento" a secas está dentro de "desprendimiento de retina", que
    aparece en la sección de salud."""
    assert "desprendimiento" not in LANDSLIDE_TERMS
    assert is_emergency("Operan un desprendimiento de retina en el Van Buren") is False


# =============================================================================
#  Ruido con forma de temporal
# =============================================================================


@pytest.mark.parametrize(
    "texto",
    [
        "El municipio realizó un simulacro de aluvión en las quebradas",
        "Simulacro de inundación con participación de SENAPRED y Bomberos",
        "Simulacro de derrumbe en el colegio del cerro",
    ],
)
def test_un_simulacro_no_es_una_emergencia(texto: str) -> None:
    """El peor falso positivo posible es el que además parece corroborado.

    La Región hace ejercicios de aluvión cada invierno, con despliegue de
    SENAPRED y cobertura de prensa: sin excindirlos entrarían al mapa como
    remociones en masa reales y con entidades mencionadas.
    """
    assert is_emergency(texto) is False
    assert es_emergencia(texto) is False


@pytest.mark.parametrize(
    "texto",
    [
        "Un aluvión de críticas recibió el alcalde por el plan de invierno",
        "El derrumbe del mercado bursátil golpeó a las AFP",
    ],
)
def test_las_metaforas_se_excinden_en_las_dos_fuentes(texto: str) -> None:
    """Una figura retórica no describe una emergencia en NINGUNA fuente.

    Por eso viven en `NOISE_PHRASES` y no en la lista de prensa: estas cuentas
    republican crónica política tanto como los diarios.
    """
    assert is_emergency(texto) is False
    assert es_emergencia(texto) is False


def test_el_megaincendio_sigue_siendo_ruido_solo_cuando_esta_fechado() -> None:
    """La regla que define `PRESS_NOISE_PHRASES`, intacta tras el traslado.

    "megaincendio" a secas NO puede excindirse: dejaría ciego al sistema justo
    el día que ocurra el siguiente.
    """
    assert es_emergencia("A dos años del megaincendio, la reconstrucción avanza") is False
    assert es_emergencia("Megaincendio activo en el sector alto de Viña") is True


def test_la_cobertura_de_obras_no_es_un_muro_que_cede() -> None:
    """El precio de haber admitido "muro de contencion" como sustantivo, pagado
    en la lista de prensa porque la licitación la cubre un diario."""
    assert es_emergencia("Aprueban la licitación del muro de contención") is False
    assert es_emergencia("Cede muro de contención y corta la calle") is True


# =============================================================================
#  Invariante entre pre-filtro y clasificador
# =============================================================================


@pytest.mark.parametrize(
    "texto",
    [
        "Anegamiento en Avenida España",
        "Socavón en calle Independencia",
        "Aluvión en la quebrada",
        "Cede muro de contención en Viña",
        "Choque en Av. España",
        "Bomberos concurre a una emergencia",
        "10-12 solicitado por Bomberos",
        "El alcalde inauguró la plaza",
        "",
        "Incendio forestal en Placilla",
        "Un aluvión de críticas al alcalde",
        "Simulacro de aluvión en la quebrada",
    ],
)
def test_el_prefiltro_y_el_clasificador_no_se_contradicen(texto: str) -> None:
    """Invariante: `classify_event_type` devuelve None si y sólo si el
    pre-filtro dijo que no.

    Si se rompiera, habría texto que paga su llamada al modelo y desaparece
    después en el `if event_type is not None` de `fetch()`: se gasta y no se
    guarda, que es la peor de las dos combinaciones. Las familias nuevas entran
    a `CRITICAL_TERMS` y a `_CLASSIFIERS` a la vez justamente por esto.
    """
    assert (classify_event_type(texto) is None) == (not is_emergency(texto))


@pytest.mark.parametrize(
    "titular",
    [
        "Anegan calles del plan de Valparaíso tras el temporal",
        "Se inundó el paso bajo nivel de Chorrillos",
        "Se desbordó el estero en Quilpué",
        "Rescatan a excursionistas perdidos en el Tranque La Luz",
        "Chocan dos vehículos en San Felipe",
        "El concejo aprobó el presupuesto municipal",
        "",
    ],
)
def test_el_prefiltro_y_el_clasificador_de_prensa_no_se_contradicen(
    titular: str,
) -> None:
    """La misma invariante del lado de la prensa, donde `HEADLINE_VERBS` amplía
    los dos caminos a la vez."""
    assert (clasificar_noticia(titular) is None) == (not es_emergencia(titular))


@pytest.mark.parametrize(
    ("titular", "esperado"),
    [
        ("Anegan calles del plan de Valparaíso", EventType.FLOOD),
        ("Anegaron los patios del colegio", EventType.FLOOD),
        ("Se inundó el paso bajo nivel", EventType.FLOOD),
        ("Inundaron los locales del centro", EventType.FLOOD),
        ("Se desbordó el estero Marga Marga", EventType.FLOOD),
    ],
)
def test_los_verbos_de_titular_cubren_lo_que_los_sustantivos_no(
    titular: str, esperado: EventType
) -> None:
    """Ninguna de estas formas está contenida en "inundacion", "inundad",
    "anegamiento" ni "anegad": comparten prefijo y divergen en la letra que
    importa. Es el mismo defecto que perdió el rescate del Tranque La Luz."""
    assert es_emergencia(titular) is True
    assert clasificar_noticia(titular) is esperado


def test_la_remocion_en_masa_no_necesita_verbos_de_titular() -> None:
    """No es un olvido: "derrumb" ya empareja con toda su conjugación.

    Si alguien agrega entradas de derrumbe a `HEADLINE_VERBS`, este test le
    recuerda que ya estaban cubiertas.
    """
    for titular in (
        "Se derrumba parte del cerro",
        "Derrumban el muro tras el temporal",
        "Se derrumbaron las rocas sobre la vía",
    ):
        assert clasificar_noticia(titular) is EventType.LANDSLIDE


# =============================================================================
#  Compatibilidad de los import paths
# =============================================================================


def test_los_alias_de_los_workers_apuntan_al_modulo_central() -> None:
    """Una extracción que rompe un import path falla ruidosamente. Una que deja
    un alias apuntando a una copia vieja no falla nunca, y el sistema vuelve a
    tener dos diccionarios divergiendo — que es exactamente lo que este módulo
    existe para impedir."""
    from app.collectors import vocabulary
    from app.collectors.news import local_news_worker as prensa
    from app.collectors.social import instagram_apify_worker as instagram
    from app.collectors.traffic import bomberos_10_4_worker as bomberos

    assert instagram.is_emergency is vocabulary.is_emergency
    assert instagram.classify_event_type is vocabulary.classify_event_type
    assert instagram.CRITICAL_TERMS is vocabulary.CRITICAL_TERMS
    assert instagram.TRAFFIC_TERMS is vocabulary.TRAFFIC_TERMS
    assert instagram.FIRE_TERMS is vocabulary.FIRE_TERMS
    assert instagram._NOISE_PHRASES is vocabulary.NOISE_PHRASES

    assert prensa.es_emergencia is vocabulary.es_emergencia
    assert prensa.clasificar_noticia is vocabulary.clasificar_noticia
    assert prensa._VERBOS_TITULAR is vocabulary.HEADLINE_VERBS
    assert prensa._RUIDO_PRENSA is vocabulary.PRESS_NOISE_PHRASES

    assert bomberos.find_codes is vocabulary.find_codes
    assert bomberos.matches_key is vocabulary.matches_key
    assert bomberos.normalise_code is vocabulary.normalise_code


def test_la_prensa_ya_no_depende_de_las_redes_sociales() -> None:
    """La flecha que la centralización vino a borrar.

    Un collector de diarios importando de uno de redes sociales no describe
    ninguna relación real entre las dos fuentes: sólo el orden en que se
    escribieron.
    """
    import inspect

    from app.collectors.news import local_news_worker as prensa

    fuente = inspect.getsource(prensa)
    importa_social = any(
        linea.startswith(("from app.collectors.social", "import app.collectors.social"))
        for linea in fuente.splitlines()
    )
    assert not importa_social
