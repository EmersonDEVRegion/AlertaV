"""Decodificación de claves radiales y formato del resumen de despacho.

Estos tests existen porque el formato de salida es un **contrato**, no una
sugerencia del prompt. Un prompt se puede cumplir a medias y nadie se entera; lo
que se guarda en `RawEvent.text` lo lee un operador, y si un día empieza a
llegar con otra forma, el cambio es invisible hasta que alguien mira el mapa.

Se prueba lo determinista: el diccionario, el formateador y la heurística. La
llamada al modelo tiene sus propios tests en `test_gemini_extraction.py`; acá se
verifica que **el modelo no puede torcer el resultado**, que es la propiedad que
justifica todo el diseño.
"""

from __future__ import annotations

import pytest

from app.collectors import vocabulary
from app.collectors.traffic import gemini
from app.collectors.traffic.bomberos_10_4_worker import Dispatch, dispatches_to_events
from app.collectors.vocabulary import (
    CLAVE_MEANINGS,
    CODE_TYPES,
    clave_label,
    clave_meaning,
    find_claves,
    resolve_clave,
)
from app.models.enums import EventType, family_of_event

FUENTE = "@CGI_CBV"

#: Despacho real de @CGI_CBV del 2 de septiembre de 2026, tal como lo publicó.
#: Es el que destapó que las tablas de claves eran de otro sistema: `5-1` no
#: estaba en ninguna, así que este accidente se descartaba en silencio.
DESPACHO_REAL = "91, 71 * AVENIDA ESPANA / AVENIDA ARGENTINA * CLAVE 5-1"


# =============================================================================
#  El caso que motivó el trabajo
# =============================================================================


def test_el_despacho_real_produce_el_resumen_pedido():
    """El ejemplo de la especificación, carácter por carácter."""
    resumen = gemini.format_dispatch_summary(
        clave="5-1",
        street_1="AVENIDA ESPANA",
        street_2="AVENIDA ARGENTINA",
        city="VALPARAÍSO",
        source_handle=FUENTE,
    )
    assert resumen == (
        "(5-1) (Rescate vehicular simple) "
        "en (Avenida Espana con Avenida Argentina, Valparaíso) (Fuente: @CGI_CBV)"
    )


def test_la_heuristica_sola_decodifica_el_despacho_real():
    """Sin GEMINI_API_KEY el resumen sigue saliendo bien formado.

    Es la propiedad que evita que una clave sin provisionar apague en silencio
    la fuente de mayor confianza del sistema. Lo único que falta es la comuna,
    que el texto no trae y que nadie puede deducir.
    """
    decoded = gemini.dispatch_summary_heuristic(DESPACHO_REAL, source_handle=FUENTE)
    assert decoded is not None
    assert decoded["clave"] == "5-1"
    assert decoded["street_1"] == "AVENIDA ESPANA"
    assert decoded["street_2"] == "AVENIDA ARGENTINA"
    assert decoded["city"] is None
    assert decoded["resumen"] == (
        "(5-1) (Rescate vehicular simple) "
        "en (Avenida Espana con Avenida Argentina) (Fuente: @CGI_CBV)"
    )


# =============================================================================
#  El diccionario
# =============================================================================


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1-1 en Serrano", ("1-1", "Incendio estructural simple")),
        ("2-2 en el cerro", ("2-2", "Incendio forestal rural")),
        ("3-1 en la vía", ("3-1", "Incendio vehicular menor")),
        ("5-1 en Ruta 68", ("5-1", "Rescate vehicular simple")),
        ("5-2 con atrapados", ("5-2", "Rescate vehicular complejo")),
        ("6-1 rescate", ("6-1", "Rescate de persona de baja complejidad")),
        ("9-1 en el túnel", ("9-1", "Accidente en túnel")),
        # El sufijo es un subtipo del mismo despacho: hereda el significado.
        ("5-1-2 con atrapado", ("5-1-2", "Rescate vehicular simple")),
        # Nombradas pero NO ingeridas: el resumen las dice igual.
        ("CLAVE 12", ("Clave 12", "Academia de Cuerpo")),
        ("CLAVE 13", ("Clave 13", "Simulacro")),
    ],
)
def test_el_diccionario_decodifica_las_claves_del_estandar(texto, esperado):
    assert resolve_clave(texto) == esperado


def test_la_clave_12_es_una_academia_y_no_se_ingiere():
    """El hallazgo que obligó a rehacer las tablas.

    `12` estaba en `BOMBEROS_ACCIDENT_KEYS` como «llamado a servicio especial»,
    tomado de otro sistema de claves. En el CBV es **Academia de Cuerpo**: una
    capacitación. Con la configuración anterior, una academia podía entrar al
    mapa como emergencia confirmada con confianza 1.00.
    """
    from app.collectors.vocabulary import CODE_TYPES, NON_INCIDENT_CODES

    assert CLAVE_MEANINGS[(12,)] == "Academia de Cuerpo"
    assert (12,) in NON_INCIDENT_CODES
    assert (12,) not in CODE_TYPES


def test_5_10_no_es_5_1():
    """La confusión que este proyecto persigue desde el primer worker.

    Un `5-10` inexistente no puede responder a `5-1`: un despacho cualquiera
    aparecería en el mapa como rescate vehicular, o sea como un choque con
    lesionados que nadie reportó.
    """
    assert resolve_clave("5-10 en Placeres") is None
    assert clave_meaning((5, 10)) is None


def test_una_fecha_no_es_una_clave():
    assert resolve_clave("el 5-1-2026 se realizara el simulacro") is None


def test_clave_5_1_no_se_lee_como_la_clave_literal_5():
    """El lookahead de `_CLAVE_LITERAL`, que es lo único que separa los dos casos.

    Sin él, "clave 5-1" produciría `(5,)` y el rescate vehicular perdería su
    subclave. Importa más que antes: `(10,)` SÍ existe en el CBV —abastecer
    agua— así que una lectura corta de "clave 10-4" produciría una clave real y
    equivocada en vez de una inexistente.
    """
    assert find_claves("clave 5-1") == [(5, 1)]


def test_la_primera_clave_del_aviso_es_la_que_manda():
    """Un 3-2 acompaña al siniestro; no lo describe.

    "10-4 ... se solicita 3-2" es un rescate vehicular con ambulancia en camino,
    no una ambulancia. El orden de aparición es lo que los distingue.
    """
    assert find_claves("5-1 en Ruta 68, luego 6-1") == [(5, 1), (6, 1)]
    assert resolve_clave("5-1 en Ruta 68, luego 6-1") == (
        "5-1",
        "Rescate vehicular simple",
    )


def test_las_claves_internas_no_crean_eventos():
    """Tienen nombre y NO están en la tabla que clasifica.

    Es la razón de que `CLAVE_MEANINGS` sea una tabla aparte de `CODE_TYPES`:
    una academia, un simulacro o un abastecimiento de agua se pueden nombrar sin
    que disparen un punto en el mapa. Poner cualquiera de éstas en `CODE_TYPES`
    metería un ejercicio de bomberos en la capa de emergencias, con la confianza
    de la central detrás.
    """
    from app.collectors.vocabulary import CODE_TYPES, NON_INCIDENT_CODES

    for code in NON_INCIDENT_CODES:
        assert code in CLAVE_MEANINGS, f"{code} se excluye sin nombrar"
        assert code not in CODE_TYPES, f"{code} no describe una emergencia"


def test_clave_label_distingue_las_dos_formas():
    assert clave_label((10, 4)) == "10-4"
    assert clave_label((12,)) == "Clave 12"


# =============================================================================
#  El formato, que es el entregable
# =============================================================================


def test_el_formato_tiene_los_cuatro_parentesis():
    assert gemini.SUMMARY_TEMPLATE.count("(") == 4
    assert gemini.SUMMARY_TEMPLATE.count(")") == 4
    assert gemini.SUMMARY_TEMPLATE.endswith("(Fuente: {fuente})")


def test_el_significado_lo_pone_el_diccionario_y_no_el_modelo():
    """La propiedad que hace seguro meter un LLM en este camino.

    El modelo devuelve "Rescate vehicular" para un 10-2, que es un incendio de
    pastizales. Gana la tabla. Sin esto, el resumen afirmaría con la autoridad
    de la central —confianza 1.00— algo que la central no dijo.
    """
    decoded = gemini.parse_dispatch_response(
        '{"clave": "2-2", "significado": "Rescate vehicular", '
        '"street_1": "CAMINO LA POLVORA", "street_2": null, "city": null, '
        '"resumen": "cualquier cosa"}',
        source_handle=FUENTE,
    )
    assert decoded is not None
    assert "Incendio forestal rural" in decoded["resumen"]
    assert "Rescate vehicular" not in decoded["resumen"]


def test_el_resumen_del_modelo_nunca_se_guarda():
    """Aunque el modelo devuelva un resumen con otro formato, se ignora."""
    decoded = gemini.parse_dispatch_response(
        '{"clave": "5-1", "significado": "Rescate vehicular", '
        '"street_1": "AV ESPANA", "street_2": null, "city": "VALPARAISO", '
        '"resumen": "5-1 — Av España (Valparaíso) [CGI_CBV]"}',
        source_handle=FUENTE,
    )
    assert decoded is not None
    assert decoded["resumen"] == (
        "(5-1) (Rescate vehicular simple) en (Av Espana, Valparaiso) "
        "(Fuente: @CGI_CBV)"
    )


def test_una_clave_desconocida_conserva_el_numero():
    """El número es el dato; el significado ausente es la señal de que falta una
    entrada en `CLAVE_MEANINGS`. Escribir "Sin clave" ocultaría justo eso."""
    resumen = gemini.format_dispatch_summary(
        clave="5-99",
        street_1="BLANCO",
        street_2=None,
        city=None,
        source_handle=FUENTE,
    )
    assert resumen.startswith("(5-99) (Clave no reconocida)")


def test_sin_via_la_ubicacion_es_explicita():
    """Un paréntesis vacío parece un error de código; esto es un dato ausente."""
    resumen = gemini.format_dispatch_summary(
        clave="5-1", street_1=None, street_2=None, city=None, source_handle=FUENTE
    )
    assert f"en ({gemini.UBICACION_DESCONOCIDA})" in resumen


def test_la_comuna_sola_no_es_una_ubicacion():
    """La central informó una esquina, no una comuna entera.

    Devolver "en (Valparaíso)" pondría el centroide comunal donde debería ir un
    cruce: una ubicación que parece un dato y no lo es.
    """
    assert gemini.build_location(None, None, "VALPARAISO") == gemini.UBICACION_DESCONOCIDA


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("DIEGO COOK", "Diego Cook"),
        # Los artículos son parte del nombre propio en el callejero chileno.
        ("CAMINO LA POLVORA", "Camino La Polvora"),
        ("LOS PLACERES", "Los Placeres"),
        # Las preposiciones no.
        ("DOCE DE FEBRERO", "Doce de Febrero"),
        # Lo que ya viene en mayúscula y minúscula lo escribió alguien con
        # cuidado: no se toca.
        ("Av. Argentina", "Av. Argentina"),
    ],
)
def test_el_titulado_respeta_el_callejero(crudo, esperado):
    assert gemini._titlecase_via(crudo) == esperado


def test_la_fuente_sale_de_la_configuracion():
    """El día que se lea otra central, el resumen no puede seguir citando ésta."""
    resumen = gemini.format_dispatch_summary(
        clave="10-4",
        street_1="SERRANO",
        street_2=None,
        city=None,
        source_handle="@OtraCentral",
    )
    assert resumen.endswith("(Fuente: @OtraCentral)")


# =============================================================================
#  El prompt
# =============================================================================


def test_el_prompt_lleva_el_diccionario_completo():
    """El prompt se RENDERIZA desde la tabla, no se escribe a mano.

    Es lo que impide que existan dos diccionarios de claves —uno en código y
    otro en prosa dentro del prompt— divergiendo en silencio. Si alguien
    reemplaza `CLAVE_GLOSSARY` por texto fijo, este test cae.
    """
    for code, meaning in CLAVE_MEANINGS.items():
        assert f"{clave_label(code)}: {meaning}" in gemini.DISPATCH_SYSTEM_INSTRUCTION


def test_el_prompt_prohibe_lo_que_hay_que_prohibir():
    prompt = gemini.DISPATCH_SYSTEM_INSTRUCTION
    assert "```" in prompt  # la prohibición explícita del bloque de código
    assert "No inventes" in prompt
    assert "coordenadas" in prompt
    # Las dos trampas propias del formato telegráfico.
    assert "unidad o carro" in prompt
    assert "NUNCA la deduzcas" in prompt


def test_el_prompt_muestra_el_formato_de_salida():
    assert "(Fuente: @CGI_CBV)" in gemini.DISPATCH_SYSTEM_INSTRUCTION
    assert "(Clave 12) (Llamado a servicio especial)" in gemini.DISPATCH_SYSTEM_INSTRUCTION


def test_el_esquema_declara_los_seis_campos():
    props = gemini.DISPATCH_RESPONSE_SCHEMA["properties"]
    assert set(props) == {
        "clave",
        "significado",
        "street_1",
        "street_2",
        "city",
        "resumen",
    }
    assert set(gemini.DISPATCH_RESPONSE_SCHEMA["required"]) == set(props)


# =============================================================================
#  Lo que se rechaza
# =============================================================================


@pytest.mark.parametrize(
    "crudo",
    [
        "",
        "   ",
        "lo siento, no puedo ayudarte con eso",
        "[]",
        "null",
        '{"clave": null, "street_1": null, "street_2": null, "city": null}',
    ],
)
def test_una_respuesta_inservible_devuelve_none(crudo):
    assert gemini.parse_dispatch_response(crudo, source_handle=FUENTE) is None


def test_la_clave_sola_basta_aunque_no_haya_calle():
    """Un despacho sin calle sigue siendo una emergencia informada por la central.

    Lo que se pierde sin `street_1` es el marcador en el mapa, no el evento.
    Descartarlo tiraría la señal de mayor confianza del sistema por no poder
    ubicarla.
    """
    decoded = gemini.parse_dispatch_response(
        '{"clave": "5-1", "significado": null, "street_1": null, '
        '"street_2": null, "city": null, "resumen": null}',
        source_handle=FUENTE,
    )
    assert decoded is not None
    assert decoded["resumen"].startswith("(5-1) (Rescate vehicular simple)")


def test_el_markdown_que_el_modelo_agrega_se_limpia():
    decoded = gemini.parse_dispatch_response(
        '```json\n{"clave": "10-4", "significado": null, "street_1": "SERRANO", '
        '"street_2": null, "city": null, "resumen": null}\n```',
        source_handle=FUENTE,
    )
    assert decoded is not None
    assert decoded["street_1"] == "SERRANO"


def test_una_frase_cualquiera_no_es_un_despacho():
    """La heurística no puede convertir prosa en una vía llamada como la prosa."""
    assert gemini.dispatch_summary_heuristic("buenos dias", source_handle=FUENTE) is None


def test_la_unidad_despachada_no_es_una_direccion():
    """El "81" que abre el aviso es el carro, no la altura de Diego Cook."""
    decoded = gemini.dispatch_summary_heuristic(
        "B1 * AVENIDA ALEMANIA CON LOS PLACERES * 10-4-1", source_handle=FUENTE
    )
    assert decoded is not None
    assert decoded["street_1"] == "AVENIDA ALEMANIA"
    assert decoded["street_2"] == "LOS PLACERES"


def test_varias_unidades_juntas_tampoco_son_una_direccion():
    """A una emergencia grande la central despacha mas de un carro.

    El despacho real que destapo las tablas de claves abria con «91, 71». Con el
    patron de una sola unidad, ese campo no se reconocia y terminaba en
    `street_1`: el rescate vehicular de Avenida Espana se iba a geocodificar
    buscando la calle «91, 71». El fallo aparece justo en los despachos mas
    grandes, que son los que mas importan.
    """
    decoded = gemini.dispatch_summary_heuristic(
        "B2, M5, 11 * ALDUNATE 1200 * 1-1", source_handle=FUENTE
    )
    assert decoded["street_1"] == "ALDUNATE 1200"


def test_una_altura_de_calle_si_es_direccion():
    """ "GUACOLDA 81" es una dirección; el descarte es del campo suelto."""
    decoded = gemini.dispatch_summary_heuristic("81 * GUACOLDA 1250 * 10-4", source_handle=FUENTE)
    assert decoded is not None
    assert decoded["street_1"] == "GUACOLDA 1250"


# --- Claves configuradas: el lado de `BOMBEROS_ACCIDENT_KEYS` ----------------
#
# `normalise_code` responde "¿esto que vi en el texto es un código?" y
# `parse_key` responde "¿esto que alguien configuró es una clave?". Son
# preguntas distintas y admiten cosas distintas: en un despacho, un número
# suelto es casi siempre el carro; en la configuración, ya viene aislado.


def test_una_clave_de_un_solo_numero_si_es_configurable():
    """El fallo mudo que corrige `parse_key`.

    Antes, `matches_key` normalizaba cada clave con `normalise_code` y saltaba
    con `continue` la que devolviera None. Configurar `12` no producía error, ni
    advertencia, ni coincidencias: la clave simplemente no existía y el tablero
    mostraba cero despachos de servicio especial como si la central no los
    despachara.
    """
    assert vocabulary.parse_key("12") == (12,)
    assert vocabulary.parse_key("clave 12") == (12,)
    assert vocabulary.parse_key("CLAVE N° 12") == (12,)


def test_la_forma_con_familia_sigue_ganando():
    """Nada de lo que ya funcionaba cambia de camino."""
    assert vocabulary.parse_key("10-4") == (10, 4)
    assert vocabulary.parse_key("10-0-4") == (10, 4)
    assert vocabulary.parse_key("10.4") == (10, 4)
    assert vocabulary.parse_key("10-4-2026") is None


def test_matches_key_ve_la_clave_12_literal():
    """La forma que publica el Cuerpo de Valparaíso, sin familia por delante.

    `find_codes` no puede verla por diseño —exige dos grupos de dígitos— así que
    mientras fuera la única fuente del filtro, ninguna configuración capturaba
    estos despachos.
    """
    aviso = "81 * DIEGO COOK / GUACOLDA * CLAVE 12"
    assert vocabulary.matches_key(aviso, ["12"]) == "12"
    assert vocabulary.matches_key(aviso, ["10-4", "12"]) == "12"


def test_un_numero_suelto_en_el_aviso_no_es_una_clave():
    """"Carro 12 en el lugar" no es un llamado a servicio especial.

    La asimetría entera de `parse_key` está acá: del lado de la configuración
    `12` es una clave; del lado del texto hace falta la palabra "clave" delante.
    """
    assert vocabulary.matches_key("Carro 12 en el lugar", ["12"]) is None
    assert vocabulary.matches_key("Unidad 12, 12 voluntarios", ["12"]) is None


def test_no_hay_aliasing_entre_familias():
    """`12` y `10-12` significan lo mismo y NO son la misma clave.

    Comparten texto en `CLAVE_MEANINGS`, pero son tuplas distintas y hacerlas
    equivalentes exigiría una tabla de sinónimos que hoy no existe. Para
    capturar las dos formas hay que configurar las dos claves — y esta es la
    línea que avisa si alguien cambia esa decisión sin querer.
    """
    assert vocabulary.matches_key("10-12 apoyo a la 2a", ["12"]) is None
    assert vocabulary.matches_key("clave 12 en Placilla", ["10-12"]) is None


def test_la_familia_completa_del_default_captura_lo_que_debe():
    """Las seis claves que `BOMBEROS_ACCIDENT_KEYS` trae por defecto."""
    claves = ["10-0", "10-1", "10-2", "10-3", "10-4", "12"]

    assert vocabulary.matches_key("Clave 10-0 incendio estructural", claves) == "10-0"
    assert vocabulary.matches_key("Clave 10-2 pastizales, Placilla", claves) == "10-2"
    assert vocabulary.matches_key("Clave 10-3 rescate en altura", claves) == "10-3"
    assert vocabulary.matches_key("10-4-1 víctima atrapada", claves) == "10-4"
    assert vocabulary.matches_key("81 * X / Y * CLAVE 12", claves) == "12"

    # Las impostoras de siempre siguen fuera, ahora contra la lista completa.
    assert vocabulary.matches_key("Clave 10-40 emanación de gas", claves) is None
    assert vocabulary.matches_key("Clave 10-41 en Playa Ancha", claves) is None
    assert vocabulary.matches_key("Reporte del 10-4-2026", claves) is None
    assert vocabulary.matches_key("Carro 104 en tránsito", claves) is None


def test_el_default_de_la_configuracion_es_parseable_entero():
    """Una clave que `parse_key` no entiende se salta EN SILENCIO.

    Ese es el modo de fallo que este test cubre: no hay excepción ni aviso, sólo
    una clave que deja de capturar. Si alguien agrega una a los defaults con un
    formato que no se reconoce, esto lo dice acá y no tres semanas después.
    """
    from app.core.config import settings

    for clave in settings.BOMBEROS_ACCIDENT_KEYS:
        assert vocabulary.parse_key(clave) is not None, clave


# =============================================================================
#  La clave decide el tipo de la señal
# =============================================================================
#
# Acá vivía un `EventType.ACCIDENT` fijo, correcto mientras
# `BOMBEROS_ACCIDENT_KEYS` sólo aceptaba `10-4`. Cuando la ingesta se abrió a la
# familia 10 entera, el literal se quedó y pasó a mentir: un incendio
# estructural entraba al sistema declarándose choque.
#
# El daño no era cosmético. El motor particiona por familia ANTES de agrupar, así
# que ese incendio quedaba en `traffic` y no podía corroborar ninguna señal de
# fuego del mismo lugar y minuto —ni FIRMS, ni CONAF, ni un reporte ciudadano—.
# Y en la interfaz sumaba a "Accidentes viales" y nunca a "Incendios", que es
# donde alguien lo iba a buscar.


@pytest.mark.parametrize(
    ("despacho", "esperado"),
    [
        # El despacho real que destapó todo esto.
        ("91, 71 * AVENIDA ESPANA / AVENIDA ARGENTINA * 5-1", EventType.ACCIDENT),
        # `5-1-2` es un subtipo del mismo despacho: comparación por prefijo.
        ("21 * RUTA 68 KM 42 * 5-1-2", EventType.ACCIDENT),
        ("21 * RUTA 68 KM 42 * 5-2", EventType.ACCIDENT),
        ("B2 * ALDUNATE 1200 * 1-1", EventType.STRUCTURAL_FIRE),
        ("11 * SUBIDA CARVALLO * 1-2", EventType.STRUCTURAL_FIRE),
        ("M5 * CAMINO LA POLVORA * 2-2", EventType.WILDFIRE),
        ("31 * QUEBRADA VERDE * 6-1", EventType.RESCUE),
        # Incendio vehicular: `STRUCTURAL_FIRE` es lo más cercano del enum y lo
        # que importa es que caiga en la familia `fire`.
        ("41 * AV ESPANA * 3-2", EventType.STRUCTURAL_FIRE),
        ("51 * TUNEL * 9-1", EventType.ACCIDENT),
    ],
)
def test_cada_clave_produce_su_tipo(despacho, esperado):
    assert vocabulary.dispatch_event_type(despacho) is esperado


def test_una_clave_interna_no_afirma_una_emergencia():
    """Una academia o un simulacro no son un hecho del mundo que mapear.

    Caen al tipo por defecto —`OTHER`— y además quedan fuera de
    `BOMBEROS_ACCIDENT_KEYS`, así que ni siquiera llegan a crear un evento.
    Doble barrera a propósito: la clasificación y la ingesta son dos decisiones
    distintas y ninguna debería depender de que la otra esté bien.
    """
    assert vocabulary.dispatch_event_type("81 * CUARTEL * CLAVE 12") is EventType.OTHER
    assert vocabulary.dispatch_event_type("81 * CUARTEL * CLAVE 13") is EventType.OTHER


def test_la_clave_de_familia_10_gana_a_la_peticion_de_recursos():
    """Un `3-2` pide ambulancia: describe un recurso, no el siniestro.

    La central abre el despacho con lo que ocurrió y después pide apoyo, así que
    la primera clave manda. Es el mismo criterio que `resolve_clave`.
    """
    assert (
        vocabulary.dispatch_event_type("81 * AV ARGENTINA * 1-1, luego 6-1")
        is EventType.STRUCTURAL_FIRE
    )


def test_un_despacho_sin_clave_no_afirma_nada():
    assert vocabulary.dispatch_event_type("Gracias a la comunidad") is EventType.OTHER
    assert vocabulary.DISPATCH_DEFAULT_TYPE is EventType.OTHER


def test_toda_clave_ingerida_tiene_significado_y_tipo():
    """La invariante que ya falló dos veces, ahora sobre toda la tabla.

    Primero fue `10-1`, que se nombraba sin clasificar. Después fue peor: la
    tabla entera describía otro sistema de claves, así que ninguna de las que
    se ingerían existía en el CBV.

    Una clave configurada sin tipo entra como `OTHER` y sale de la familia donde
    el mapa la busca — un incendio de la fuente de peso 1.00 fuera de
    «Incendios». Una sin significado sale en el resumen como un número pelado.
    """
    from app.core.config import settings

    for key in settings.BOMBEROS_ACCIDENT_KEYS:
        code = vocabulary.parse_key(key)
        assert code is not None, f"{key} no es una clave parseable"
        assert code in CLAVE_MEANINGS, f"{key} se ingiere y no tiene significado"
        assert code in CODE_TYPES, f"{key} se ingiere y no tiene tipo"


def test_ninguna_clave_interna_esta_configurada_para_ingesta():
    """El contrapunto: lo que NO es una emergencia no puede entrar.

    Es lo que fallaba con `12`, que estaba configurada y es una academia.
    """
    from app.collectors.vocabulary import NON_INCIDENT_CODES
    from app.core.config import settings

    configuradas = {vocabulary.parse_key(k) for k in settings.BOMBEROS_ACCIDENT_KEYS}
    assert not (configuradas & NON_INCIDENT_CODES)


def test_el_evento_de_un_incendio_estructural_cae_en_la_familia_fire():
    """La propiedad que de verdad importa, medida donde importa.

    Es la que decidía si un incendio de Bomberos podía corroborar una detección
    de FIRMS del mismo cerro. Con el literal viejo, no podía.
    """
    despacho = Dispatch(
        key="1-1",
        address="ALDUNATE 1200",
        occurred_at=None,
        commune=None,
        raw_text="B2 * ALDUNATE 1200 * 10-1",
    )
    eventos, _ = dispatches_to_events([despacho], collector="test")

    assert eventos[0].type is EventType.STRUCTURAL_FIRE
    assert family_of_event(eventos[0].type) == "fire"
    assert family_of_event(EventType.ACCIDENT) == "traffic", "eran familias distintas"
