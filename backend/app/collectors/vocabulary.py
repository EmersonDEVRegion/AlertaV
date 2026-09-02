"""Vocabulario de emergencia. Único diccionario del sistema, sin dueño.

Por qué existe este módulo
--------------------------
Nació como un bloque de constantes dentro del worker de Instagram, porque ese
fue el primer collector que tuvo que leer texto libre. Después lo importó el
worker de prensa, y el resultado era un collector de diarios dependiendo de uno
de redes sociales: una flecha que no describe ninguna relación real entre las
dos fuentes, sólo el orden en que se escribieron.

Los dos workers ya lo tenían anotado como deuda —`app/collectors/codes.py` en
uno, `app/collectors/vocabulario.py` en el otro— y con un argumento que sigue
siendo el bueno: **dos diccionarios de emergencia que se editan por separado
terminan divergiendo**, y el día que alguien agregue un término en uno y no en
el otro, media plataforma deja de ver esa clase de siniestro sin que nada falle.
Este archivo es el pago de esa deuda, y el vacío que la hizo urgente está en el
bloque de hidrometeorología: el sistema no tenía una sola palabra para nombrar
un anegamiento ni un socavón, que es exactamente lo que le pasa a Valparaíso
entre mayo y agosto.

Qué vive acá y qué no
---------------------
Acá vive **el léxico y la decisión de tipo**: qué palabras nombran una
emergencia, qué claves radiales despacha la central, qué frases parecen una
emergencia sin serlo, y la traducción de todo eso a un `EventType`.

Acá **no** vive nada que toque la red, la base de datos ni el formato de una
fuente concreta. Un collector sabe cómo se lee su fuente; este módulo sabe qué
significan las palabras que salen de ella. Si una función necesita `await`, no
pertenece a este archivo.

La regla que gobierna todo el archivo
-------------------------------------
Es un filtro de **RECALL, no de precisión**. Un falso positivo cuesta una
llamada al modelo y se descarta más adelante; un falso negativo pierde un
siniestro para siempre y **en silencio**, que es la peor forma de perderlo. Ante
la duda, pasa.

Todo se compara sobre texto ya pasado por `normalise_text` (NFD → se descartan
las marcas combinantes → minúsculas → espacios colapsados), así que **todos los
términos de este archivo se escriben sin tildes y en minúscula**. Un término con
tilde no coincidiría nunca y el fallo sería mudo: lo impiden
`test_los_terminos_estan_normalizados` y `test_los_verbos_estan_normalizados`.

Coincidencia por SUBCADENA, no por palabra. "atropell" cubre atropello,
atropellado y atropellaron sin enumerar conjugaciones; el precio es que hay que
elegir raíces que no aparezcan dentro de otra palabra, y en este archivo hay al
menos cuatro decisiones tomadas por ese motivo (ver "anegad", "derrumb",
"desprendimiento de" y la ausencia de "arde").
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.collectors.geoservices import normalise_text
from app.models.enums import EventType

# =============================================================================
#  Reconocimiento de la clave radial
# =============================================================================
#
# Vivía en `traffic/bomberos_10_4_worker.py`, que fue su primer y único
# consumidor durante un tiempo. Hoy lo usan tres. La explicación completa de por
# qué son dos pasos y no una expresión regular queda acá porque es la
# justificación del diseño, no del worker:
#
# La misma clave aparece escrita de varias formas en un feed de texto libre:
#
#     10-4        forma canónica
#     10-0-4      con el separador de familia que usan varios Cuerpos
#     10-4-1      con sufijo de subtipo (rescate con víctima atrapada)
#     10.4        con punto
#     10 – 4      con guion largo y espacios, cortesía del autocorrector
#
# Y hay cadenas que se le parecen y NO son la clave:
#
#     10-40       otra clave por completo (emanación de gas)
#     10-41       ídem
#     10-0-1      otra clave (incendio estructural)
#     10-4-2026   una fecha, no un código
#
# Se puede escribir una sola regex que acepte las cinco y rechace las cuatro:
#
#     (?<!\d)10\s*[-–—.]\s*(?:0\s*[-–—.]\s*)?4(?:\s*[-–—.]\s*\d{1,2})?(?!\d)
#
# Funciona, y es exactamente el tipo de línea que nadie se atreve a tocar en
# seis meses. El fallo que importa —confundir 10-40 con 10-4— depende de un
# `(?!\d)` en el extremo derecho: un carácter fácil de perder en una edición
# apurada, imposible de ver en una revisión, y cuyo síntoma es un despacho por
# fuga de gas apareciendo en el mapa como accidente vehicular.
#
# Por eso son dos pasos triviales en vez de uno ingenioso:
#
#   1. `_CODE_TOKEN` reconoce *cualquier* cosa con forma de código —grupos de
#      dígitos unidos por separadores— sin decidir cuál es.
#   2. `normalise_code` la convierte en tupla de enteros y aplica las dos reglas
#      del dominio: descartar grupos de tres o más dígitos (años, alturas de
#      calle) y colapsar el 0 intermedio del formato 10-0-x.
#
# Después basta comparar tuplas. `10-40` produce `(10, 40)` y `10-4` produce
# `(10, 4)`: distintas por construcción, sin depender de ningún lookahead.

#: Cualquier cosa con forma de código: grupos de dígitos unidos por separadores.
#: Deliberadamente permisivo — filtrar es trabajo de `normalise_code`.
_CODE_TOKEN = re.compile(r"(?<!\d)(\d{1,4}(?:\s*[-–—/.]\s*\d{1,4}){1,3})(?!\d)")

#: Separadores admitidos, incluidos los guiones tipográficos que introducen los
#: teclados de teléfono.
_CODE_SPLIT = re.compile(r"[\s\-–—/.]+")

#: Un grupo con tres o más dígitos delata que el token no es una clave: es una
#: fecha ("10-4-2026") o una altura de calle. Ninguna clave del Sistema Nacional
#: pasa de dos dígitos por grupo.
_MAX_GROUP_DIGITS = 2


def normalise_code(token: str) -> tuple[int, ...] | None:
    """Token con forma de código → tupla de enteros comparable. None si no lo es.

    Aplica las dos reglas del dominio:

    * **Grupos de 3+ dígitos lo descalifican.** `10-4-2026` es una fecha.
    * **El 0 intermedio del formato `10-0-x` se colapsa.** Varios Cuerpos lo usan
      como separador de familia, no como parte del código, así que `10-0-4` y
      `10-4` son la misma clave y tienen que normalizar a la misma tupla.

    >>> normalise_code("10-4")
    (10, 4)
    >>> normalise_code("10-0-4")
    (10, 4)
    >>> normalise_code("10-4-1")
    (10, 4, 1)
    >>> normalise_code("10-40")
    (10, 40)
    >>> normalise_code("10-4-2026") is None
    True
    """
    pieces = [piece for piece in _CODE_SPLIT.split(token.strip()) if piece]
    if len(pieces) < 2:
        return None
    if any(len(piece) > _MAX_GROUP_DIGITS for piece in pieces):
        return None

    try:
        groups = [int(piece) for piece in pieces]
    except ValueError:  # pragma: no cover — la regex sólo captura dígitos
        return None

    # El 0 en segunda posición es separador de familia, no un valor.
    if len(groups) > 2 and groups[1] == 0:
        groups = [groups[0], *groups[2:]]
    return tuple(groups)


def find_codes(text: str) -> list[tuple[int, ...]]:
    """Todos los códigos normalizados presentes en un texto."""
    found: list[tuple[int, ...]] = []
    for match in _CODE_TOKEN.finditer(text):
        code = normalise_code(match.group(1))
        if code is not None:
            found.append(code)
    return found


def parse_key(token: str) -> tuple[int, ...] | None:
    """Una clave **configurada** → tupla comparable. None si no lo es.

    Es el lado de la configuración de lo que `normalise_code` hace del lado del
    texto, y son dos funciones y no una porque los dos lados admiten cosas
    distintas. Del lado del texto, un número suelto no puede ser una clave: en
    un despacho, "12" es casi siempre el carro, la altura de la calle o la hora,
    y por eso `normalise_code` exige dos grupos de dígitos. Del lado de la
    configuración el número YA viene aislado —alguien escribió `12` en
    `BOMBEROS_ACCIDENT_KEYS` con la intención de nombrar una clave— y esa
    ambigüedad no existe.

    El fallo que esto corrige era mudo, que es el peor de los que persigue este
    proyecto. `matches_key` normalizaba cada clave con `normalise_code` y
    **saltaba con `continue` la que devolviera None**: configurar `12` no
    producía ningún error, ninguna advertencia y ninguna coincidencia — la clave
    simplemente no existía, y el tablero mostraba cero despachos de servicio
    especial como si la central no los despachara.

    >>> parse_key("10-4")
    (10, 4)
    >>> parse_key("12")
    (12,)
    >>> parse_key("CLAVE 12")
    (12,)
    >>> parse_key("10-4-2026") is None
    True
    """
    limpio = normalise_text(token)
    if not limpio:
        return None

    # La forma con familia (`10-4`, `10-0-4`, `10.4`) es la de siempre y manda:
    # se prueba primero para que nada de lo que ya funcionaba cambie de camino.
    code = normalise_code(limpio)
    if code is not None:
        return code

    # Formas de una sola clave: `12`, `clave 12`, `clave n° 12`. Se reutiliza
    # `_CLAVE_LITERAL` en vez de escribir otra expresión — es el mismo lenguaje
    # y tener dos definiciones de "clave suelta" es cómo divergen.
    literal = _CLAVE_LITERAL.search(limpio)
    if literal is not None:
        return (int(literal.group(1)),)

    if limpio.isdigit() and len(limpio) <= _MAX_GROUP_DIGITS:
        return (int(limpio),)

    return None


def matches_key(text: str, keys: Sequence[str]) -> str | None:
    """Devuelve la clave buscada que aparece en el texto, o None.

    La comparación es **por prefijo de tupla**: un aviso con `10-4-1` responde a
    la clave configurada `10-4` porque `(10, 4)` es prefijo de `(10, 4, 1)`. Ese
    sufijo es un subtipo del mismo despacho —rescate con víctima atrapada—, no
    otra emergencia, y descartarlo perdería justo los casos más graves.

    `10-40` produce `(10, 40)`, que no tiene a `(10, 4)` por prefijo, así que no
    coincide. Sin lookaheads y sin ambigüedad.

    Del lado del texto se usa `find_claves` y no `find_codes`, y ese cambio es
    lo que hace visible la forma **`CLAVE 12`** que el Cuerpo de Valparaíso
    publica a secas, sin familia por delante. `find_codes` no puede verla por
    diseño —exige dos grupos de dígitos— así que mientras fuera la única fuente
    de este filtro, ninguna configuración podía capturar esos despachos.

    Lo que NO cambia con eso: `find_claves` es un superconjunto estricto de
    `find_codes` sobre el mismo texto, y `_CLAVE_LITERAL` lleva un lookahead que
    impide que `clave 10-4` se lea como la clave literal `10`. Las impostoras de
    siempre —`10-40`, `10-41`, `10-0-1`, la fecha `10-4-2026`, el `Carro 104`,
    la `Unidad 110-4`— siguen sin coincidir, y `test_matches_key_distingue_la_
    clave_de_sus_impostoras` sigue siendo el que lo garantiza.

    Tampoco hay aliasing entre familias: `12` responde a "CLAVE 12" y **no** a
    "10-12", aunque `CLAVE_MEANINGS` les dé el mismo significado. Son tuplas
    distintas —`(12,)` y `(10, 12)`— y hacerlas equivalentes exigiría una tabla
    de sinónimos que hoy no existe. Para capturar las dos formas, configurar
    las dos claves.
    """
    present = find_claves(text)
    if not present:
        return None

    for key in keys:
        wanted = parse_key(key)
        if wanted is None:
            continue
        for code in present:
            if code[: len(wanted)] == wanted:
                return key
    return None


# =============================================================================
#  Léxico por familia de siniestro
# =============================================================================

#: Tránsito. `accidente` a secas entra porque en estas fuentes casi siempre es
#: vial; cuando no lo es, `classify_event_type` lo manda igual a la familia
#: correcta o a `OTHER`.
TRAFFIC_TERMS: frozenset[str] = frozenset(
    {
        "choque",
        "colision",
        "volcamiento",
        "atropell",  # atropello, atropellado, atropellaron
        "desbarrancamiento",
        "desbarranc",  # desbarrancó, desbarrancado
        "accidente de transito",
        "accidente vehicular",
        "accidente",
        "alta energia",  # trauma de alta energía: jerga de rescate vehicular
        "transito suspendido",
        "siniestro vial",
        "vuelco",
    }
)

#: Intervención de la vía: desvíos, faenas, cortes y restricciones.
#:
#: **NO forma parte de `CRITICAL_TERMS` y eso es la mitad del diseño.** Una faena
#: programada no es una emergencia, y `is_emergency` la alimenta a los workers de
#: prensa e Instagram, donde un "corte de tránsito por obras" entrando como
#: siniestro sería ruido puro. Este conjunto existe para la capa táctica del MTT
#: —una fuente que publica avisos operativos por diseño— y se consulta aparte,
#: por `es_operacion_vial`.
#:
#: La palabra peligrosa del bloque es **"corte"**, y por eso no aparece sola en
#: ningún término. Este archivo lo comparten tres workers, y en dos de ellos
#: "corte" es casi siempre eléctrico o de agua: "corte de luz", "corte de
#: suministro", "corte programado de agua". Un `"corte"` suelto acá convertiría
#: cada corte de Chilquinta en una intervención vial. Todos los términos con esa
#: raíz llevan el sustantivo de la vía pegado, y esa redundancia es la que hace
#: que el conjunto sea seguro de importar desde cualquier parte.
#:
#: Al revés que el resto del archivo, este bloque se inclina a la **precisión**.
#: La regla general —ante la duda, pasa— vale cuando el costo de un falso
#: positivo es una llamada al modelo. Acá el falso positivo es un punto en el
#: mapa que dice que una calle está cortada cuando no lo está, y eso es peor que
#: no decir nada: un vecino que se desvía por un corte inventado pierde el viaje
#: y deja de creerle a la capa.
ROAD_OPS_TERMS: frozenset[str] = frozenset(
    {
        # -- Desvíos ---------------------------------------------------------
        "desvio de transito",
        "desvio vehicular",
        "desvio obligatorio",
        "transito desviado",
        "se desvia el transito",
        "desvios de transito",
        # -- Faenas y obras ---------------------------------------------------
        # "trabajos" a secas queda fuera a propósito: "trabajan en el lugar" es
        # `OPERATIONAL_TERMS` y describe a Bomberos operando en una emergencia,
        # que es justo lo contrario de una faena programada.
        "trabajos en la via",
        "trabajos en la calzada",
        "trabajos en la ruta",
        "trabajos viales",
        "faena",
        "obras viales",
        "obra vial",
        "mantencion vial",
        "mantenimiento de la via",
        "repavimentacion",
        "bacheo",
        # -- Cortes y cierres. Siempre con el sustantivo de la vía. ------------
        "corte de transito",
        "corte de calle",
        "corte de via",
        "corte de ruta",
        "corte de pista",
        "corte de calzada",
        "corte vehicular",
        "cierre de la via",
        "cierre vial",
        "cierre de pista",
        "cierre de calzada",
        "via cerrada",
        "calzada cerrada",
        "pista cerrada",
        "ruta cerrada",
        "camino cerrado",
        "transito cortado",
        "transito suspendido",
        "paso restringido",
        # -- Restricciones ----------------------------------------------------
        "restriccion vehicular",
        "restriccion de circulacion",
        "prohibido el estacionamiento",
        "solo transito local",
    }
)

#: Incendios y materiales peligrosos.
FIRE_TERMS: frozenset[str] = frozenset(
    {
        "incendio",
        "fuego",
        "emanacion",
        "siniestro",
        "primera alarma",
        "segunda alarma",
        "tercera alarma",
        "estructural",
        "pastizales",
        "pastizal",
        "forestal",
        "llamas",
        "amago",
    }
)

#: Rescate de personas.
RESCUE_TERMS: frozenset[str] = frozenset(
    {
        "rescate",
        "persona atrapada",
        "atrapad",  # atrapado, atrapada, atrapados
        "caida de altura",
    }
)

# -- Hidrometeorología: el vacío que este módulo vino a llenar -----------------
#
# Hasta esta versión el sistema no tenía **una sola palabra** para nombrar un
# anegamiento ni un socavón, mientras `EventType.FLOOD` y `EventType.LANDSLIDE`
# existían en el enum desde el principio y `INCIDENT_FAMILY` ya les tenía
# reservada la familia `hydro`. Es decir: la plataforma sabía representar estos
# fenómenos y no sabía reconocerlos. En una región donde el invierno se mide en
# calles cortadas por barro, eso no era una omisión menor.
#
# Que caigan en la familia `hydro` importa y conviene tenerlo presente al leer
# el mapa: un anegamiento NO se fusiona con un incendio ni con un choque por
# más que compartan esquina y minuto. Se corroboran entre ellos y con nada más.

#: Inundaciones y aguas fuera de cauce.
#:
#: **"anegad" y no "anega".** La raíz corta cubriría también "anegamiento" y
#: "anegan", y era la tentación obvia — pero "anega" está dentro de **"fanega"**,
#: la unidad agraria que aparece en la crónica rural. Es el mismo error que ya
#: dejó a "arde" fuera del diccionario de titulares por vivir dentro de "tarde".
#: Se pagan dos entradas para no pagar ese ruido.
#:
#: "desborde" y "desbordamiento" van completos en vez de la raíz "desbord",
#: que es la que produce "desbordante" y "desbordado de alegría".
FLOOD_TERMS: frozenset[str] = frozenset(
    {
        "anegamiento",
        "anegad",  # anegado, anegada, anegados: cubre "calle anegada"
        "inundacion",
        "inundad",  # inundado, inundada, inundados
        "desborde",
        "desbordamiento",
        "salida de cauce",
        "crecida",
        "rebalse",
        "colapso de colector",
        "colector colapsado",
        "colapso del alcantarillado",
        "aguas servidas en la calle",
    }
)

#: Remoción en masa. Nombre técnico de la familia: incluye el derrumbe, el
#: deslizamiento y el **aluvión**.
#:
#: El aluvión vive acá y no en `FLOOD_TERMS` aunque lo dispare la lluvia: en la
#: nomenclatura que usa SENAPRED es un flujo de detritos —barro y piedra bajando
#: una quebrada—, no agua acumulada. La distinción no es académica: lo que
#: destruye no es el nivel del agua sino el material que arrastra, y quien lea el
#: mapa necesita ver eso. Ambas familias caen igual en `hydro`, así que un
#: aluvión y el anegamiento que lo acompaña sí se corroboran entre sí.
#:
#: **"desprendimiento" no entra a secas**: está dentro de "desprendimiento de
#: retina", que aparece en la sección de salud. Van las tres formas calificadas.
#:
#: "derrumb" sí va como raíz —cubre derrumbe, derrumbó, derrumbado,
#: derrumbamiento— y se paga su acepción figurada ("se derrumbó el mercado")
#: con las tres frases correspondientes en `PRESS_NOISE_PHRASES`.
LANDSLIDE_TERMS: frozenset[str] = frozenset(
    {
        "derrumb",  # derrumbe, derrumbó, derrumbado, derrumbamiento
        "deslizamiento",
        "remocion en masa",
        "socavon",
        "socavamiento",
        "caida de rocas",
        "caida de muro",
        "caida de material",
        "desprendimiento de rocas",
        "desprendimiento de tierra",
        "desprendimiento de material",
        "aluvion",
        "flujo de detritos",
        "colapso de talud",
        "ladera colapsada",
        # El sustantivo entero en vez del verbo, y a propósito. La comparación
        # es por subcadena y no sabe de artículos opcionales: "cede muro de
        # contención" y "cedió el muro de contención" son dos titulares
        # igualmente probables, y ninguna raíz verbal los cubre a los dos. El
        # objeto sí. El precio es la nota de obras públicas —"licitan la
        # reparación del muro de contención"—, que se paga en
        # `PRESS_NOISE_PHRASES`.
        "muro de contencion",
    }
)

#: Términos que por sí solos bastan para pasar el filtro.
CRITICAL_TERMS: frozenset[str] = (
    TRAFFIC_TERMS | FIRE_TERMS | RESCUE_TERMS | FLOOD_TERMS | LANDSLIDE_TERMS
)

#: Entidades de respuesta. **Deliberadamente NO disparan solas.**
#:
#: Es la decisión menos obvia del bloque y la que más ruido evita. "Bomberos de
#: Valparaíso celebró su aniversario junto al alcalde", "Carabineros lanza
#: campaña de seguridad escolar" y "SENAPRED capacita a dirigentes vecinales"
#: contienen la entidad y no son emergencias — y son, además, exactamente el
#: tipo de publicación que estas fuentes producen a diario. Una entidad dice
#: QUIÉN podría estar involucrado, nunca QUÉ pasó.
AGENCY_TERMS: frozenset[str] = frozenset(
    {
        "bomberos",
        "carabineros",
        "samu",
        "senapred",
        "conaf",
        "ambulancia",
    }
)

#: Lo que convierte la mención de una entidad en un hecho. Entidad + contexto
#: pasa el filtro; cualquiera de los dos por separado, no.
OPERATIONAL_TERMS: frozenset[str] = frozenset(
    {
        "emergencia",
        "evacuacion",
        "evacuar",
        "lesionad",  # lesionado, lesionados, lesionada
        "herid",  # herido, heridos, herida
        "fallecid",
        "damnificad",
        "de urgencia",
        "concurr",  # concurre, concurren, concurrió
        "acudi",  # acudió, acudieron
        "trabajan en el lugar",
        "transito cortado",
        "via cerrada",
    }
)


# =============================================================================
#  Claves radiales → tipo de evento
# =============================================================================

#: Clave normalizada → naturaleza de la señal. La comparación es por PREFIJO de
#: tupla, igual que en `matches_key`: `10-4-1` (rescate con víctima atrapada)
#: responde a `10-4` porque es un subtipo del mismo despacho, no otra
#: emergencia.
#:
#: Ojo con la familia `10-0`: `normalise_code` colapsa el cero intermedio, así
#: que `10-0-4` normaliza a `(10, 4)` y NO a `(10, 0)`. Esta tabla reconoce el
#: `10-0` escrito tal cual. Si el Cuerpo de la zona despacha los estructurales
#: como `10-0-1`, hay que agregar `(10, 1)` acá.
CODE_TYPES: dict[tuple[int, ...], EventType] = {
    (10, 0): EventType.STRUCTURAL_FIRE,  # incendio estructural
    # `10-1` es la forma en que el Cuerpo de Valparaíso despacha el estructural,
    # y estaba SÓLO en `CLAVE_MEANINGS`: nombraba sin clasificar. El desfase no
    # era teórico — `BOMBEROS_ACCIDENT_KEYS` la incluye desde que la ingesta se
    # abrió a la familia 10 entera, así que el despacho entraba y caía al tipo
    # por defecto. Un incendio estructural de la fuente de confianza 1.00
    # quedaba fuera de la familia `fire`, que es donde el mapa lo busca.
    (10, 1): EventType.STRUCTURAL_FIRE,  # incendio estructural
    (10, 2): EventType.WILDFIRE,  # pastizales
    (10, 3): EventType.RESCUE,  # rescate de personas
    (10, 4): EventType.ACCIDENT,  # rescate vehicular
}

#: `10-12` (apoyo) va aparte y **necesita compañía** para pasar el filtro. Dos
#: razones, y la primera es de dominio, no un parche:
#:
#: * Un apoyo no describe una emergencia nueva: es un despacho adicional a una
#:   que ya está en curso. Por sí solo no aporta un hecho al mapa.
#: * `10-12` colisiona con una fecha escrita corta ("el 10-12 se realizará…").
#:   Las fechas con año —`10-12-2026`— ya las rechaza `normalise_code`, pero la
#:   forma sin año pasaría.
#:
#: Para que dispare solo, mover esta entrada a `CODE_TYPES`. Es una línea.
SUPPORT_CODES: dict[tuple[int, ...], EventType] = {
    (10, 12): EventType.OTHER,  # apoyo / llamado a servicio especial
}


# =============================================================================
#  Significado de la clave: el diccionario que también lee el modelo
# =============================================================================
#
# `CODE_TYPES` responde "¿qué EventType es esto?". Esta tabla responde otra
# pregunta, la que hace un humano mirando el mapa: "¿qué significa 10-4?".
#
# Son dos tablas y no una a propósito. `CODE_TYPES` sólo puede contener claves
# que el sistema sepa clasificar, y meter acá el `3-1` —que no describe un
# siniestro sino una petición de concurrencia— convertiría cada solicitud de
# Carabineros en un evento del mapa. La separación deja poner nombre a claves
# que **no** deben disparar nada.
#
# **Esta tabla es la fuente del prompt de Gemini.** `traffic/gemini.py` la
# renderiza dentro de su instrucción de sistema en vez de repetir el diccionario
# en prosa, y esa indirección es el punto entero del bloque: un diccionario
# escrito a mano dentro de un prompt es un segundo diccionario, y este módulo
# existe porque dos diccionarios de emergencia que se editan por separado
# terminan divergiendo. Agregar una clave acá la agrega en los dos lugares.
#
# Además, el significado que llega al resumen **se busca acá, no se le cree al
# modelo**: si Gemini devuelve "10-4" y de significado "incendio forestal", gana
# esta tabla. Ver `gemini.format_dispatch_summary`.
#
# Las claves se escriben normalizadas (ver `normalise_code`), así que `10-0-4`
# ya entra como `(10, 4)`. La comparación es por PREFIJO, igual que en todo el
# módulo: `10-4-1` responde a `(10, 4)`.
#
# Ojo con `(10, 1)`: entra por el rango "10-0 al 10-2 son incendios" que usa la
# central, pero **no** está en `CODE_TYPES`, así que hoy nombra sin clasificar.
# Si el Cuerpo de Valparaíso despacha estructurales como 10-1, hay que agregarlo
# también allá; es una línea y cambia qué se pinta en el mapa.
CLAVE_MEANINGS: dict[tuple[int, ...], str] = {
    # -- Familia 10: qué está pasando -------------------------------------
    (10, 0): "Incendio estructural",
    (10, 1): "Incendio estructural",
    (10, 2): "Incendio de pastizales",
    (10, 3): "Rescate o salvamento",
    (10, 4): "Rescate vehicular",
    (10, 12): "Llamado a servicio especial",
    # -- Familia 3: a quién se pide que concurra ---------------------------
    #
    # Estas tres NO describen un siniestro: describen un recurso solicitado.
    # Tienen nombre para que un resumen pueda decirlo, y deliberadamente no
    # están en `CODE_TYPES` ni en `SUPPORT_CODES` — un `3-2` no puede crear un
    # evento por sí solo, porque la emergencia que motivó la ambulancia ya
    # entró (o no entró) por su propia clave.
    (3, 1): "Solicita concurrencia de Carabineros",
    (3, 2): "Solicita ambulancia",
    (3, 3): "Solicita concurrencia de la empresa eléctrica",
    # -- Forma literal "CLAVE N" ------------------------------------------
    #
    # El Cuerpo de Bomberos de Valparaíso publica "CLAVE 12" a secas, sin la
    # familia por delante. `normalise_code` no la ve —exige dos grupos— así que
    # se reconoce aparte (ver `_CLAVE_LITERAL`) y se representa como tupla de un
    # solo elemento. Significa lo mismo que `10-12` y por eso comparte texto:
    # el día que alguien edite uno, `test_clave_12_y_10_12_significan_lo_mismo`
    # avisa del otro.
    (12,): "Llamado a servicio especial",
}

#: "CLAVE 12", "clave n° 12", "Clave 3" — la forma literal, sin familia.
#:
#: El lookahead de la derecha es lo único que evita que "clave 10-4" se lea como
#: la clave literal 10: si el número viene seguido de un separador y otro
#: dígito, es un código de familia y le corresponde a `find_codes`, no a esta
#: expresión. Sin él, todo despacho de la familia 10 se resumiría como "Clave
#: 10", que no existe.
_CLAVE_LITERAL = re.compile(r"\bclave\s*(?:n[o°º]?\s*)?(\d{1,2})(?!\s*[-–—/.]\s*\d)(?!\d)")


def find_claves(texto: str) -> list[tuple[int, ...]]:
    """Códigos y claves literales del texto, en orden de aparición.

    Superconjunto de `find_codes`: suma la forma "CLAVE N" que aquella no puede
    ver por diseño (exige dos grupos de dígitos). Se ordena por posición para
    que "10-4, se solicita 3-2" resuelva al rescate vehicular y no a la
    ambulancia — la primera clave del aviso es la que lo motiva.

    Trabaja sobre texto ya normalizado; si le llega crudo, lo normaliza.
    """
    limpio = normalise_text(texto)
    if not limpio:
        return []

    encontrados: list[tuple[int, tuple[int, ...]]] = []

    for match in _CODE_TOKEN.finditer(limpio):
        code = normalise_code(match.group(1))
        if code is not None:
            encontrados.append((match.start(), code))

    for match in _CLAVE_LITERAL.finditer(limpio):
        encontrados.append((match.start(), (int(match.group(1)),)))

    encontrados.sort(key=lambda par: par[0])

    ordenados: list[tuple[int, ...]] = []
    for _, code in encontrados:
        if code not in ordenados:
            ordenados.append(code)
    return ordenados


def clave_label(code: tuple[int, ...]) -> str:
    """Tupla → la clave tal como la escribe la central.

    Dos formas, porque la central usa dos: la familia va con guiones y la clave
    suelta va con la palabra delante. Escribir `(12,)` como "12" a secas dejaría
    un resumen que empieza con un número huérfano.

    >>> clave_label((10, 4))
    '10-4'
    >>> clave_label((12,))
    'Clave 12'
    """
    if len(code) == 1:
        return f"Clave {code[0]}"
    return "-".join(str(group) for group in code)


def clave_meaning(code: tuple[int, ...]) -> str | None:
    """Significado de una clave normalizada. None si no está en el diccionario.

    Comparación por prefijo: `10-4-1` (rescate con víctima atrapada) hereda el
    significado de `10-4`. Se prueba de lo más específico a lo más general para
    que una entrada futura de `(10, 4, 1)` gane sobre `(10, 4)`.
    """
    for largo in range(len(code), 0, -1):
        meaning = CLAVE_MEANINGS.get(code[:largo])
        if meaning is not None:
            return meaning
    return None


#: Tipo con el que entra un despacho cuya clave no clasifica nada.
#:
#: `OTHER` y no `ACCIDENT`, que es lo que hacía el worker antes. La diferencia
#: importa porque `EVENT_TO_INCIDENT_TYPE` manda `ACCIDENT` a la familia
#: `traffic`: una "CLAVE 12" —llamado a servicio especial, que puede ser
#: cualquier cosa— entraba afirmando que hubo un choque. `OTHER` cae en la
#: familia `other`, que en el mapa es "Otras emergencias" y es exactamente lo
#: que el sistema sabe de ese despacho.
DISPATCH_DEFAULT_TYPE = EventType.OTHER


def dispatch_event_type(texto: str) -> EventType:
    """Texto de un despacho → naturaleza de la señal, según su clave.

    Es el reemplazo del `EventType.ACCIDENT` fijo que tenía
    `bomberos_10_4_worker.dispatches_to_events`. Ese literal era correcto cuando
    la ingesta sólo aceptaba `10-4`; hoy `BOMBEROS_ACCIDENT_KEYS` trae la
    familia 10 entera, y con el literal en su sitio un incendio estructural de
    Bomberos —la fuente de confianza 1.00 del catálogo— entraba al sistema
    afirmando que era un choque. Consecuencias, en orden de gravedad:

      * El motor particiona por familia antes de agrupar, así que ese incendio
        quedaba en `traffic` y **no podía corroborar** ninguna señal de fuego
        del mismo lugar y minuto: ni una detección de FIRMS, ni un reporte
        ciudadano, ni un aviso de CONAF.
      * En la interfaz sumaba al contador de "Accidentes viales" y nunca al de
        "Incendios", que es donde alguien lo iba a buscar.

    Se decide por la PRIMERA clave del aviso, igual que `resolve_clave`, y por
    el mismo motivo: la central abre el despacho con lo que ocurrió y después
    pide recursos, así que un `3-2` (ambulancia) escrito al final no puede
    ganarle a la clave de familia 10 que lo motivó.

    `SUPPORT_CODES` se consulta después de `CODE_TYPES` y no antes: `10-12` es
    un apoyo a una emergencia en curso, y si el aviso trae también la clave de
    lo que está pasando, esa es la que describe el hecho.

    >>> dispatch_event_type("81 * DIEGO COOK / GUACOLDA * 10-4")
    <EventType.ACCIDENT: 'accident'>
    >>> dispatch_event_type("10-1 * ALDUNATE 1200")
    <EventType.STRUCTURAL_FIRE: 'structural_fire'>
    >>> dispatch_event_type("81 * DIEGO COOK / GUACOLDA * CLAVE 12")
    <EventType.OTHER: 'other'>
    """
    for code in find_claves(texto):
        for tabla in (CODE_TYPES, SUPPORT_CODES):
            for wanted, event_type in tabla.items():
                if code[: len(wanted)] == wanted:
                    return event_type
    return DISPATCH_DEFAULT_TYPE


def resolve_clave(texto: str) -> tuple[str, str] | None:
    """Texto de un despacho → `(etiqueta, significado)`. None si no hay clave.

    Es la función que usa el resumen: devuelve la PRIMERA clave del aviso que
    esté en el diccionario. "Primera" y no "más específica" porque el orden en
    que la central escribe importa —el despacho abre con lo que pasó y después
    pide recursos— y porque un `3-2` sin su clave de familia delante describe
    una ambulancia, no una emergencia.

    >>> resolve_clave("81 * DIEGO COOK / GUACOLDA * CLAVE 12")
    ('Clave 12', 'Llamado a servicio especial')
    """
    for code in find_claves(texto):
        meaning = clave_meaning(code)
        if meaning is not None:
            return (clave_label(code), meaning)
    return None


# =============================================================================
#  Ruido con forma de emergencia
# =============================================================================
#
# Dos listas, y la separación es deliberada: la primera es ruido de cualquier
# fuente, la segunda es lo que sólo escribe un diario. Se aplican en cascada.

#: Frases que CONTIENEN un término crítico y no son una emergencia. Se **borran
#: del texto** antes de buscar, en vez de vetar la publicación entera: la
#: excisión es quirúrgica y un veto mal puesto perdería el accidente real que
#: viniera en el mismo párrafo.
#:
#: `fuegos artificiales` no es un ejemplo de manual: el show de Año Nuevo en el
#: Mar es la publicación más replicada del año en estas cuentas, y "fuego" la
#: habría mandado entera al modelo cada 31 de diciembre.
#:
#: El bloque de simulacros creció con las familias nuevas y por el mismo motivo:
#: la Región de Valparaíso hace **simulacros de aluvión** en las quebradas cada
#: invierno, con cobertura de prensa y despliegue de SENAPRED. Sin excindirlos,
#: cada ejercicio municipal entraría al mapa como una remoción en masa real —y
#: con Bomberos y Carabineros mencionados, que es el peor falso positivo posible:
#: el que además parece corroborado.
#:
#: **El orden de esta lista es irrelevante**, y eso costó un rediseño. Ver
#: `_excindir`: la excisión se calcula como unión de tramos sobre el texto
#: original, no como una cadena de reemplazos. Antes de eso el orden era una
#: invariante frágil —había que borrar de la frase más larga a la más corta— y
#: ni siquiera bastaba: "prevencion de incendios forestales" y "campana de
#: prevencion de incendios" miden exactamente lo mismo y se solapan, así que
#: ninguna regla de longitud podía decidir entre las dos. Se ordena alfabético
#: por legibilidad y nada más.
NOISE_PHRASES: tuple[str, ...] = tuple(
    sorted(
        {
            # Fuego que no quema
            "prevencion de incendios forestales",
            "campana de prevencion de incendios",
            "fuegos artificiales",
            "fuego artificial",
            "show de fuegos",
            "aniversario del incendio",
            "anos del incendio",
            "prevencion de incendios",
            "seguro contra incendios",
            "a fuego lento",
            # Ejercicios. Es una emergencia anunciada, que es justo lo contrario
            # de una emergencia.
            "simulacro de incendio",
            "simulacro de emergencia",
            "simulacro de evacuacion",
            "simulacro de aluvion",
            "simulacro de inundacion",
            "simulacro de derrumbe",
            "simulacro de sismo",
            "ejercicio de evacuacion",
            # Metáforas. Van acá y no en `PRESS_NOISE_PHRASES` por una razón que
            # conviene dejar como regla: **una figura retórica no describe una
            # emergencia en ninguna fuente**, así que excindirla no puede perder
            # nada y sí evita ruido en las dos. Lo que sí es propio de un diario
            # —y por eso vive en la otra lista— es la referencia *fechada* a un
            # hecho real y pasado.
            #
            # "Un aluvión de críticas" y "el derrumbe del mercado" son giros de
            # uso diario en la crónica política y económica, que estas cuentas
            # republican tanto como los diarios. Son el precio de haber admitido
            # "aluvion" y la raíz "derrumb".
            "aluvion de criticas",
            "aluvion de denuncias",
            "aluvion de reclamos",
            "aluvion de mensajes",
            "aluvion de consultas",
            "derrumbe del mercado",
            "derrumbe de la bolsa",
            "derrumbe economico",
            "choque de trenes politico",
            "accidente de la temporada",
        }
    )
)

#: Ruido específico de la prensa. Complementa a `NOISE_PHRASES` con lo que
#: aparece en un diario y no en una red social.
#:
#: La línea que separa las dos listas: acá va lo **fechado y lo institucional**
#: —la referencia a un hecho real y pasado, o la cobertura de un trámite—, que
#: es lo que produce una redacción. La figura retórica va en `NOISE_PHRASES`,
#: porque no describe una emergencia en ninguna fuente.
#:
#: Casi todo el bloque de incendios gira alrededor de un solo hecho, y no es
#: casualidad: el **megaincendio de febrero de 2024** es el suceso más
#: referenciado de la prensa regional. Reconstrucción, subsidios, juicios,
#: aniversarios, columnas de opinión — todo eso contiene la palabra "incendio" y
#: nada de eso es una emergencia en curso.
#:
#: La regla que define qué entra acá: **sólo formas fechadas o inequívocamente
#: figuradas**. "megaincendio" a secas NO está en la lista y no debe estarlo —
#: contiene "incendio" como subcadena, así que excindirlo dejaría ciego al
#: sistema justo el día que ocurra el siguiente.
#:
#: El bloque figurado creció con las familias nuevas, y por una razón concreta:
#: "un aluvión de críticas" y "el derrumbe del mercado" son giros de uso diario
#: en la crónica política y económica. Sin ellos, la sección de política entera
#: empezaría a pagar llamadas al modelo desde el día que se sumó `aluvion`.
PRESS_NOISE_PHRASES: tuple[str, ...] = tuple(
    sorted(
        {
            "megaincendio de febrero de 2024",
            "megaincendio de 2024",
            "incendio de febrero de 2024",
            "incendio de febrero de 2023",
            "incendio de 2024",
            "incendio del 2024",
            "aniversario del megaincendio",
            "aniversario del incendio",
            "a un ano del megaincendio",
            "a dos anos del megaincendio",
            "reconstruccion tras el megaincendio",
            "damnificados del megaincendio",
            "victimas del megaincendio",
            # Cobertura de obras públicas: el muro de contención que todavía no
            # cede. Esto sí es propio de un diario —licitaciones, concejo
            # municipal, presupuesto regional— y por eso no está en la lista
            # general.
            "reparacion del muro de contencion",
            "construccion del muro de contencion",
            "licitacion del muro de contencion",
            "nuevo muro de contencion",
        }
    )
)


# =============================================================================
#  Normalización y excisión
# =============================================================================


def _excindir(texto: str, frases: Sequence[str]) -> str:
    """Borra del texto todo tramo cubierto por alguna frase de ruido.

    La versión obvia de esto es una cadena de `str.replace`, y fue lo que hubo
    durante un tiempo. Falla de una forma que no es evidente hasta que muerde:
    borrar una frase deja un texto distinto del que la siguiente esperaba, así
    que el resultado depende del **orden** de la lista.

    El caso real que lo destapó, con el texto ya normalizado:

        campana de prevencion de incendios forestales de conaf
        ├── "campana de prevencion de incendios"   → tramo [0, 34)
        └── "prevencion de incendios forestales"   → tramo [11, 45)

    Reemplazando en cadena, borrar la primera deja suelto un "forestales" que es
    término crítico por sí mismo, y la campaña de CONAF entra igual como
    incendio. Borrar la segunda primero sí funciona — y las dos frases **miden
    exactamente lo mismo**, así que ninguna regla de longitud puede elegir entre
    ellas. La invariante no era «de la más larga a la más corta»: era una
    coincidencia que se sostenía sobre el orden en que alguien escribió la lista.

    Acá se buscan todas las apariciones sobre el texto **original**, se unen los
    tramos y se borra la unión de una vez. `[0, 34) ∪ [11, 45) = [0, 45)`: se va
    "forestales" con lo demás, y el orden de la lista deja de importar.

    No hace falta iterar hasta punto fijo: una excisión sólo borra, nunca
    inserta, y el hueco se rellena con un espacio — así que no puede fabricar una
    frase de ruido que no estuviera ya en el texto original.
    """
    tramos: list[tuple[int, int]] = []
    for frase in frases:
        desde = texto.find(frase)
        while desde != -1:
            tramos.append((desde, desde + len(frase)))
            desde = texto.find(frase, desde + 1)

    if not tramos:
        return texto

    tramos.sort()
    piezas: list[str] = []
    cursor = 0
    for inicio, fin in tramos:
        if inicio > cursor:
            piezas.append(texto[cursor:inicio])
        cursor = max(cursor, fin)
    piezas.append(texto[cursor:])
    return " ".join(" ".join(piezas).split())


def haystack(texto: str) -> str:
    """Texto listo para buscar: normalizado y con el ruido conocido excindido."""
    normalizado = normalise_text(texto)
    if not normalizado:
        return ""
    return _excindir(normalizado, NOISE_PHRASES)


def haystack_prensa(texto: str) -> str:
    """`haystack` más la capa de ruido propia de un diario.

    `normalise_text` es idempotente (descompone a NFD, descarta las marcas
    combinantes y pasa a minúsculas: aplicarla dos veces da lo mismo), así que
    las dos capas de excisión se encadenan sin copiar la primera acá.

    Las dos capas sí se aplican en orden —primero la de prensa, después la
    general— y eso es correcto por el mismo argumento de `_excindir`: una
    excisión sólo borra. Lo que la segunda capa ve es un subconjunto de lo que
    vio la primera, nunca algo nuevo.
    """
    normalizado = normalise_text(texto)
    if not normalizado:
        return ""
    return haystack(_excindir(normalizado, PRESS_NOISE_PHRASES))


def _codes_in(texto: str, table: dict[tuple[int, ...], EventType]) -> EventType | None:
    """Primer tipo cuya clave aparece en el texto. Comparación por prefijo."""
    for code in find_codes(texto):
        for wanted, event_type in table.items():
            if code[: len(wanted)] == wanted:
                return event_type
    return None


# =============================================================================
#  Decisión: ¿es una emergencia, y de qué tipo?
# =============================================================================


def is_emergency(texto: str) -> bool:
    """¿Este texto habla de una emergencia? Síncrono, en memoria, sin red.

    Es el guardián del gasto: lo que devuelve `False` no llega nunca al
    extractor. Cuatro caminos para pasar, y sólo uno de ellos involucra
    entidades:

    1. Un **término crítico** (tránsito, incendio, rescate, inundación o
       remoción en masa).
    2. Una **clave radial** de `CODE_TYPES` — la central diciendo qué despachó.
    3. **Entidad + contexto operativo**: "Bomberos concurre a…", "SAMU trasladó
       a un lesionado". Nunca la entidad sola (ver `AGENCY_TERMS`).
    4. El **`10-12` de apoyo** acompañado de una entidad o de contexto (ver
       `SUPPORT_CODES`).

    El costo es un puñado de búsquedas de subcadena sobre un texto de 1.500
    caracteres como máximo. No hay I/O, no hay `await` y no hay nada que ceda el
    control: se puede llamar dentro del bucle de un `fetch()` sin tocar el event
    loop que comparte con el motor de correlación.
    """
    texto_limpio = haystack(texto)
    if not texto_limpio:
        return False

    if any(term in texto_limpio for term in CRITICAL_TERMS):
        return True

    if _codes_in(texto_limpio, CODE_TYPES) is not None:
        return True

    tiene_entidad = any(term in texto_limpio for term in AGENCY_TERMS)
    tiene_contexto = any(term in texto_limpio for term in OPERATIONAL_TERMS)

    if tiene_entidad and tiene_contexto:
        return True

    if _codes_in(texto_limpio, SUPPORT_CODES) is not None:
        return tiene_entidad or tiene_contexto

    return False


# -- Clasificación determinista ------------------------------------------------
#
# El orden importa: se evalúa de lo más específico a lo más genérico y gana la
# primera coincidencia. "incendio forestal" tiene que mirarse antes que
# "incendio", o todo fuego terminaría siendo estructural.

_WILDFIRE = (
    "incendio forestal",
    "quema de pastizal",
    "pastizales",
    "pastizal",
    "foco de incendio",
)

_STRUCTURAL_FIRE = (
    "incendio estructural",
    "incendio en una vivienda",
    "incendio de vivienda",
    "incendio en local",
    "se quema una casa",
    "casa en llamas",
)

#: Marcador genérico de fuego. Sólo se consulta si ninguno de los específicos
#: coincidió, y produce `OTHER` a propósito — ver `classify_event_type`.
_GENERIC_FIRE = ("incendio", "llamas", "amago", "fuego", "emanacion")

#: Cadena de clasificación, de lo más específico a lo más genérico.
#:
#: Dos decisiones de orden que conviene dejar escritas:
#:
#: * **Remoción en masa antes que inundación.** Un temporal produce las dos
#:   cosas y el texto suele nombrarlas juntas ("socavón por el colapso del
#:   colector"). Cuando hay material desplazado, eso es lo que corta la calle y
#:   lo que hay que pintar; el agua es la causa. Las dos caen en `hydro`, así que
#:   la elección no rompe ninguna corroboración: sólo decide la etiqueta.
#: * **Ambas antes que tránsito.** No por jerarquía sino por especificidad, que
#:   es la regla del bloque: `TRAFFIC_TERMS` contiene "accidente" y "siniestro"
#:   a secas, los dos términos más genéricos del archivo, y "derrumbe" es
#:   inequívoco. "Camión volcado tras el derrumbe" es un derrumbe con un camión
#:   dentro.
_CLASSIFIERS: tuple[tuple[frozenset[str] | tuple[str, ...], EventType], ...] = (
    (_WILDFIRE, EventType.WILDFIRE),
    (_STRUCTURAL_FIRE, EventType.STRUCTURAL_FIRE),
    (LANDSLIDE_TERMS, EventType.LANDSLIDE),
    (FLOOD_TERMS, EventType.FLOOD),
    (TRAFFIC_TERMS, EventType.ACCIDENT),
    (RESCUE_TERMS, EventType.RESCUE),
)


def classify_event_type(texto: str) -> EventType | None:
    """¿Qué describe este texto? None si no describe una emergencia.

    Reglas, no modelo. El contrato de Gemini en este proyecto son tres campos
    geográficos y ningún juicio sobre el hecho (ver
    `app/collectors/traffic/gemini.py`), y esa frontera no se mueve porque la
    fuente sea nueva.

    El fuego sin calificar (`incendio` a secas, que es como lo escribe la mitad
    de estas fuentes) devuelve `OTHER` y **no** `WILDFIRE` ni `SMOKE`. Es
    deliberado y es la decisión más discutible del módulo, así que conviene
    dejarla escrita:

    * `WILDFIRE` afirmaría que hay un incendio forestal, que es lo que CONAF
      confirma yendo al lugar. Una publicación de Instagram no puede afirmar eso.
    * `SMOKE` es un avistamiento de humo. Tampoco: el texto dice fuego.
    * `OTHER` cae en la familia `other`, así que **no se fusiona** con los
      incendios que CONAF o FIRMS reporten a 500 m. Pierde corroboración, y ese
      es exactamente el intercambio buscado: preferimos un punto huérfano en el
      mapa antes que subirle la confianza a un incendio con evidencia que no
      vale lo que parece.

    El agua no recibe ese trato y la asimetría es intencional: no existe un
    "posible anegamiento" con el que se pueda confundir. Una calle está cortada
    por barro o no lo está, no hay una versión satelital del hecho que pueda
    inflarse por corroboración, y `FLOOD`/`LANDSLIDE` viven solos en la familia
    `hydro`. El riesgo que justifica degradar el fuego genérico sencillamente no
    tiene equivalente acá.

    **Invariante con el pre-filtro**: devuelve `None` si y sólo si
    `is_emergency` devolvió `False`. Sin eso, un texto podría pasar el filtro
    —pagando su llamada al modelo— y desaparecer después en el `if event_type is
    not None` del `fetch()` que lo llamó, que es la peor combinación posible: se
    gasta y no se guarda.
    """
    texto_limpio = haystack(texto)
    if not texto_limpio:
        return None

    # 1. La clave radial primero: es la central diciendo qué despachó, y eso
    #    vale más que adivinar por vocabulario. Un "10-0 en calle Serrano" es
    #    más específico que cualquier sinónimo de fuego que traiga el texto.
    code_type = _codes_in(texto_limpio, CODE_TYPES)
    if code_type is not None:
        return code_type

    # 2. Vocabulario, de lo más específico a lo más genérico.
    for markers, event_type in _CLASSIFIERS:
        if any(marker in texto_limpio for marker in markers):
            return event_type

    if any(marker in texto_limpio for marker in _GENERIC_FIRE):
        return EventType.OTHER

    # 3. El pre-filtro dijo que sí y no sabemos de qué se trata (un apoyo, una
    #    entidad con contexto). `OTHER` es impreciso pero cierto; `None` sería
    #    tirar algo por lo que ya se pagó.
    if is_emergency(texto):
        return EventType.OTHER

    return None


# =============================================================================
#  La capa táctica de tránsito
# =============================================================================
#
# Entrada aparte del resto del módulo, y conviene decir por qué no se resolvió
# metiendo `ROAD_OPS_TERMS` en `_CLASSIFIERS`, que es lo que uno haría primero.
#
# `classify_event_type` tiene una invariante escrita en su docstring: devuelve
# `None` si y sólo si `is_emergency` devolvió `False`. Un desvío por obras no es
# una emergencia y `is_emergency` tiene que seguir diciendo que no —de eso
# dependen los workers de prensa e Instagram, donde un aviso de faena es ruido—.
# Así que agregar la familia vial a la cadena general habría roto la invariante
# o habría convertido las faenas en emergencias. Ninguna de las dos.
#
# La capa táctica es, en cambio, algo que sólo tiene sentido en una fuente que
# publica avisos operativos por diseño: el MTT. Un portal de tránsito diciendo
# "corte de calzada en Av. España" está informando un hecho de su competencia.
# El mismo texto en un titular de diario es contexto de otra historia.

#: Términos de accidente para la capa vial: `TRAFFIC_TERMS` menos lo que también
#: nombra una intervención programada.
#:
#: Hoy la intersección es exactamente `{"transito suspendido"}`, y resolverla
#: hacia el cierre es lo correcto: un aviso del MTT que sólo dice "tránsito
#: suspendido" describe una vía cerrada, no un choque. Cuando además hubo un
#: choque, el texto lo dice con todas sus letras —"Accidente vehicular; tránsito
#: suspendido hacia el poniente"— y `accidente` gana igual, porque se consulta
#: primero.
#:
#: Se calcula como diferencia de conjuntos en vez de escribirse a mano para que
#: no pueda divergir: si mañana alguien agrega un término a los dos lados, esto
#: lo resuelve solo y `test_la_interseccion_vial_es_la_esperada` avisa del
#: cambio en vez de dejarlo pasar.
ACCIDENT_TERMS: frozenset[str] = TRAFFIC_TERMS - ROAD_OPS_TERMS


def es_operacion_vial(texto: str) -> bool:
    """¿El texto informa una intervención de la vía? Síncrono, sin red.

    Es el equivalente de `is_emergency` para la capa táctica, y como aquél, es
    el guardián del gasto: lo que devuelve `False` no llega al extractor de
    calles ni al geocodificador.

    Deliberadamente NO consulta `is_emergency`: son dos preguntas
    independientes. "Accidente en Ruta 68; tránsito desviado" responde que sí a
    las dos, y eso está bien — quien decide qué tipo emitir es
    `clasificar_transito`.
    """
    texto_limpio = haystack(texto)
    if not texto_limpio:
        return False
    return any(term in texto_limpio for term in ROAD_OPS_TERMS)


def es_accidente_vial(texto: str) -> bool:
    """¿El texto describe un siniestro vial? Síncrono, sin red.

    Esta decisión **no la toma el modelo**, y no es una preferencia estilística.
    El contrato de Gemini en este proyecto son tres campos geográficos y ningún
    juicio sobre el hecho (ver `app/collectors/traffic/gemini.py`).

    El motivo es la asimetría del daño. Si el modelo se equivoca extrayendo una
    calle, el resultado es una geocodificación fallida o un punto discutible:
    visible, marcado en `raw_data._geocoding`, corregible. Si se le permitiera
    decidir "esto es un accidente", podría inventar un siniestro que nadie
    reportó, y eso llegaría al mapa con la confianza 0.80 de una fuente oficial
    detrás.
    """
    texto_limpio = haystack(texto)
    if not texto_limpio:
        return False
    return any(term in texto_limpio for term in ACCIDENT_TERMS)


def clasificar_transito(texto: str) -> EventType | None:
    """Aviso de tránsito → `ACCIDENT`, `ROAD_CLOSURE` o `None`.

    **El accidente se consulta primero, y ese orden es el diseño.** Casi todo
    siniestro en la vía produce un desvío, así que la mayoría de los avisos de
    choque contienen también vocabulario de cierre: "Colisión en Av. España con
    Uno Norte; tránsito desviado por Errázuriz". Si el cierre ganara, el sistema
    archivaría el choque como una faena y la capa de accidentes perdería su
    fuente oficial más rápida — en silencio, que es como duelen estos fallos.

    Al revés no hay riesgo simétrico: una faena programada no menciona una
    colisión. El orden equivocado pierde accidentes; el correcto, en el peor
    caso, etiqueta como accidente un cierre que alguien redactó raro. Se elige
    el error barato.

    Devuelve `None` cuando el aviso no es ninguna de las dos cosas: un paso
    fronterizo habilitado, un cambio de recorrido de micros, la nota sobre el
    nuevo horario del terminal. El portal publica bastante de eso.
    """
    if es_accidente_vial(texto):
        return EventType.ACCIDENT
    if es_operacion_vial(texto):
        return EventType.ROAD_CLOSURE
    return None


# =============================================================================
#  El registro del titular
# =============================================================================
#
# Este bloque salió de un test que falló, y conviene contar por qué, porque es
# un defecto del léxico compartido y no un capricho de la prensa.
#
# El diccionario se calibró contra captions de Instagram, donde una emergencia
# se anuncia con un **sustantivo**: "RESCATE en el Tranque La Luz", "CHOQUE en la
# Ruta 68". Un titular de prensa usa **verbo conjugado**: "Rescatan a
# excursionistas perdidos", "Chocan dos vehículos en San Felipe". Y la
# comparación es por subcadena, así que "rescate" NO empareja con "rescatan" ni
# con "rescataron": comparten cinco letras y difieren en la sexta.
#
# El resultado, antes de este bloque, era que la única emergencia real del feed
# de Sitio del Suceso del 31 de agosto —el rescate de dos excursionistas— se caía
# del sistema en silencio. Exactamente el fallo que el pre-filtro existe para no
# cometer: un falso negativo no deja rastro en ninguna parte.
#
# La regla al elegir raíces es la del resto del archivo, y acá mordió al primer
# intento: **la raíz no puede aparecer dentro de otra palabra**. "arde" quedó
# fuera de esta tabla porque está dentro de "tarde", y "de la tarde" aparece en
# la mitad de las crónicas. "rescatar" quedó fuera por otro motivo, más
# específico del dominio: en infinitivo casi siempre es figurado —"rescatar
# espacios públicos", "rescatar el patrimonio"— mientras que las formas
# conjugadas y el participio describen un hecho.
#
# Las entradas de agua y barro son menos de las que parecerían necesarias, y
# tiene explicación: las raíces de `LANDSLIDE_TERMS` ya cubren sus propias
# conjugaciones ("derrumb" empareja con "se derrumba" y con "derrumbaron"), así
# que acá sólo van las formas que ninguna raíz alcanza.
#
# El orden importa: se devuelve la primera coincidencia, así que va de lo
# específico a lo genérico.
HEADLINE_VERBS: dict[str, EventType] = {
    # Rescate
    "rescatan": EventType.RESCUE,
    "rescataron": EventType.RESCUE,
    "rescatad": EventType.RESCUE,  # rescatado, rescatada, rescatados
    # La remoción en masa NO tiene entradas acá, y no es un olvido. Sus raíces
    # ya cubren la conjugación —"derrumb" empareja con "se derrumba", "derrumban"
    # y "derrumbaron"— y el titular chileno para estos hechos es nominal:
    # "Derrumbe deja la ruta cortada", "Deslizamiento de tierra en Placeres".
    # El único caso verbal frecuente, "cede muro de contención", se resuelve por
    # el objeto y no por el verbo (ver `LANDSLIDE_TERMS`).
    # Inundación. Ninguna de estas formas está contenida en "inundacion",
    # "inundad", "anegamiento" ni "anegad": comparten prefijo y divergen justo
    # en la letra que importa.
    "inundan": EventType.FLOOD,
    "inundaron": EventType.FLOOD,
    "se inundo": EventType.FLOOD,  # se inundó
    "anegan": EventType.FLOOD,
    "anegaron": EventType.FLOOD,
    "se desbordo": EventType.FLOOD,  # se desbordó
    "se desbordaron": EventType.FLOOD,
    # Tránsito
    "chocan": EventType.ACCIDENT,
    "chocaron": EventType.ACCIDENT,
    "colisionan": EventType.ACCIDENT,
    "colisionaron": EventType.ACCIDENT,
    "colisiono": EventType.ACCIDENT,  # colisionó
    "vuelca": EventType.ACCIDENT,
    "volcaron": EventType.ACCIDENT,
    "volco": EventType.ACCIDENT,  # volcó; «volcán» normaliza a «volcan»
    "se estrello": EventType.ACCIDENT,  # se estrelló
    # Fuego sin calificar. `OTHER` y no `WILDFIRE`, por la misma razón que
    # documenta `classify_event_type`: un titular que dice "se incendia" no
    # afirma que sea forestal, y afirmarlo por él lo fundiría con los incendios
    # que CONAF reporte a 500 m, subiéndoles la confianza con evidencia que no
    # vale lo que parece.
    "incendia": EventType.OTHER,  # se incendia, incendian, incendiario
}


def tipo_por_verbo(texto_limpio: str) -> EventType | None:
    """Primer tipo cuyo verbo de titular aparece en el texto ya normalizado."""
    for termino, tipo in HEADLINE_VERBS.items():
        if termino in texto_limpio:
            return tipo
    return None


def es_emergencia(texto: str) -> bool:
    """¿Esta noticia habla de una emergencia? Variante de prensa de `is_emergency`.

    Delega en `is_emergency` —cuatro caminos para pasar— después de quitarle al
    texto el ruido propio de un diario, y añade un quinto camino: el verbo de
    titular que los sustantivos no cubren (ver `HEADLINE_VERBS`).

    Sigue siendo un filtro de **recall**, no de precisión. Un falso positivo
    cuesta una llamada al modelo y se descarta más adelante; un falso negativo
    pierde un siniestro para siempre. Ante la duda, pasa.
    """
    texto_limpio = haystack_prensa(texto)
    if not texto_limpio:
        return False
    if is_emergency(texto_limpio):
        return True
    return tipo_por_verbo(texto_limpio) is not None


def clasificar_noticia(texto: str) -> EventType | None:
    """¿Qué describe esta noticia? Variante de prensa de `classify_event_type`.

    **Invariante con el pre-filtro**: devuelve `None` si y sólo si
    `es_emergencia` devolvió `False`. Hay que sostenerla explícitamente porque
    `HEADLINE_VERBS` amplía el pre-filtro: si la clasificación no se ampliara
    igual, una noticia pasaría el filtro y desaparecería después en el `if
    event_type is not None` de `fetch()`. No costaría dinero —el descarte ocurre
    antes del modelo— pero perdería la señal sin dejar rastro, que es la peor
    forma de perderla.
    """
    texto_limpio = haystack_prensa(texto)
    if not texto_limpio:
        return None
    tipo = classify_event_type(texto_limpio)
    if tipo is not None:
        return tipo
    return tipo_por_verbo(texto_limpio)


__all__ = [
    "ACCIDENT_TERMS",
    "AGENCY_TERMS",
    "CLAVE_MEANINGS",
    "CODE_TYPES",
    "CRITICAL_TERMS",
    "DISPATCH_DEFAULT_TYPE",
    "FIRE_TERMS",
    "FLOOD_TERMS",
    "HEADLINE_VERBS",
    "LANDSLIDE_TERMS",
    "NOISE_PHRASES",
    "OPERATIONAL_TERMS",
    "PRESS_NOISE_PHRASES",
    "RESCUE_TERMS",
    "ROAD_OPS_TERMS",
    "SUPPORT_CODES",
    "TRAFFIC_TERMS",
    "clasificar_noticia",
    "clasificar_transito",
    "classify_event_type",
    "clave_label",
    "clave_meaning",
    "dispatch_event_type",
    "es_accidente_vial",
    "es_emergencia",
    "es_operacion_vial",
    "find_claves",
    "find_codes",
    "haystack",
    "haystack_prensa",
    "is_emergency",
    "matches_key",
    "normalise_code",
    "parse_key",
    "resolve_clave",
    "tipo_por_verbo",
]
