"""Lugares nombrados en texto libre: comunas y sectores.

Por qué existe
--------------
El 2026-09-03 un incendio en Miraflores Alto (Viña del Mar) llegó por dos
fuentes y el mapa lo dibujó en dos lugares, ninguno de los dos correcto:

* La nota de Pura Noticia —"Incendio en Viña del Mar: equipos de emergencia
  combatieron fuego que consumió una casa en el sector de Miraflores Alto"— cayó
  en el centro de Viña: el extractor leyó `"Viña del Mar:"` como calle y
  Nominatim devolvió la ciudad entera.
* El tuit —"… hasta calle once, en el sector de Miraflores Alto"— cayó a unos
  2,5 km, en Recreo: el extractor leyó `"el"` como calle.

Ninguna de las dos fuentes da coordenadas, y la prensa casi nunca da una
esquina. Lo que **sí** dan las dos, y con las mismas palabras, es el sector. Es
el dato de lugar más confiable que trae una nota, y hasta acá se tiraba.

Este módulo lo rescata del texto. Tres consumidores:

* `nominatim.geocode` lo usa como **guarda**: una calle que Nominatim ubica en
  otro sector se descarta, igual que hoy se descarta la que cae en otra comuna.
  Y como **respaldo**: si no hay calle, se geocodifica el sector.
* `raw_data._extraction.sector_clave` lo lleva al motor de correlación, que
  une dos señales de la misma familia que nombran el mismo sector aunque sus
  puntos no se toquen (ver `CorrelationEngine._step_a_sector`).
* `comuna_en_texto`, que vivía en el worker de prensa, se mudó acá porque ahora
  la necesitan también Instagram y X, y la prensa no puede importar de las redes
  sociales (`test_la_prensa_ya_no_depende_de_las_redes_sociales`).

Lo que NO hace
--------------
No tiene una tabla de sectores con coordenadas. Un centroide escrito a mano
parece un dato y no lo es; quien sabe dónde está Miraflores Alto es OSM, y se le
pregunta en tiempo de ejecución. Tampoco reconoce un sector sin la palabra que
lo introduce ("incendio en Achupallas"): sin una lista de nombres no hay forma
de distinguirlo de una calle, y ese caso ya lo resuelve Nominatim, que devuelve
el barrio con su rango de lugar (ver `nominatim.precision_de`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.collectors.geoservices import normalise_text
from app.collectors.weather.comunas import COMUNAS_V_REGION

# -- Comunas -------------------------------------------------------------------

#: Nombres normalizados de las 36 comunas continentales, de la más larga a la más
#: corta. El orden importa al buscar por subcadena: "La Calera" tiene que
#: probarse antes que "Calera", y "Villa Alemana" antes que "Alemana", o la
#: comuna detectada sería la equivocada.
COMUNAS_NORMALIZADAS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((normalise_text(comuna.nombre), comuna.nombre) for comuna in COMUNAS_V_REGION),
        key=lambda par: len(par[0]),
        reverse=True,
    )
)


def comuna_por_nombre(texto: str | None) -> str | None:
    """El nombre canónico si `texto` ES una comuna (igualdad, no subcadena)."""
    etiqueta = normalise_text(texto or "").strip(" ,.:;")
    if not etiqueta:
        return None
    for normalizada, nombre in COMUNAS_NORMALIZADAS:
        if etiqueta == normalizada:
            return nombre
    return None


def comuna_en_texto(texto: str) -> str | None:
    """Comuna nombrada en el texto. Respaldo del respaldo.

    La usan el camino HTML de la prensa, que no tiene categorías, y el RSS
    cuando las categorías del portal no nombran ninguna comuna.

    Acá sí se busca por subcadena, porque la entrada es prosa. Es más frágil que
    `comuna_en_categorias` —"vecinos de Valparaíso viajaron a Los Andes" devuelve
    la primera que aparezca— y por eso sólo se consulta cuando no hay categoría,
    y sólo alimenta un campo que el extractor puede sobrescribir.
    """
    haystack = normalise_text(texto)
    if not haystack:
        return None
    for normalizada, nombre in COMUNAS_NORMALIZADAS:
        if normalizada in haystack:
            return nombre
    return None


# -- Sectores ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sector:
    """Un sector nombrado en el texto, tal como lo escribió la fuente."""

    nombre: str
    #: Comuna pegada al nombre ("sector Miraflores Alto de Viña del Mar"), si la
    #: hubo. No se adivina: si el texto no la dice acá, queda en None.
    comuna: str | None = None


#: Palabras que introducen un sector. En `sector` la palabra NO es parte del
#: nombre ("sector de Miraflores Alto" → "Miraflores Alto"); en las demás sí
#: ("Villa Dulce", "Cerro Alegre", "Población Vergara"), y así lo escribe OSM.
_INTRODUCTOR = re.compile(r"\b(?i:(sector|cerro|poblaci[oó]n|villa|barrio))\b")
_INTRODUCTOR_FUERA_DEL_NOMBRE = frozenset({"sector"})

#: Unión permitida DENTRO de un nombre propio: "Cerro Los Placeres",
#: "Villa Independencia de Viña del Mar", "Población Santa Julia".
_CONECTORES = frozenset({"de", "del", "la", "las", "los", "el"})

#: Donde se corta el nombre. Hace falta sobre todo en los textos escritos en
#: mayúsculas —"SECTOR MIRAFLORES ALTO BOMBEROS TRABAJAN…"—, donde no hay una
#: minúscula que marque el final.
_CORTES = frozenset(
    {
        "en", "y", "con", "hasta", "donde", "tras", "por", "para", "a", "al",
        "comuna", "bomberos", "carabineros", "samu", "senapred", "conaf",
        "calle", "avenida", "av", "pasaje", "esquina", "altura", "frente",
    }
)

#: Un "nombre" que es sólo una orientación no es un sector: "el sector alto",
#: "el sector Poniente de Valparaíso".
_ORIENTACIONES = frozenset(
    {"norte", "sur", "oriente", "poniente", "centro", "alto", "alta", "bajo", "baja"}
)

#: Tope de palabras de un nombre. "Villa Independencia de Viña del Mar" son
#: seis; un nombre de sector con más que eso es texto en mayúsculas sin cortar.
_MAX_PALABRAS = 7

_PALABRA = re.compile(r"[^\s,.;:()¡!¿?\"«»“”]+|[,.;:()¡!¿?\"«»“”]")


def _es_nombre_propio(palabra: str) -> bool:
    return palabra[:1].isupper() or palabra[:1].isdigit()


def _recortar_comuna(palabras: list[str]) -> tuple[list[str], str | None]:
    """Separa una comuna pegada al final: "Miraflores Alto de Viña del Mar"."""
    for inicio in range(1, len(palabras)):
        cola = palabras[inicio:]
        if cola and cola[0].lower() in {"de", "del"}:
            comuna = comuna_por_nombre(" ".join(cola[1:]))
            if comuna:
                return (palabras[:inicio], comuna)
        comuna = comuna_por_nombre(" ".join(cola))
        if comuna:
            return (palabras[:inicio], comuna)
    return (palabras, None)


def _nombre_desde(texto: str, inicio: int) -> list[str]:
    """Palabras de nombre propio a partir de `inicio`, hasta un corte."""
    palabras: list[str] = []
    pendientes: list[str] = []  # conectores que esperan una palabra propia
    for token in _PALABRA.findall(texto[inicio:]):
        if len(token) == 1 and not token.isalnum():
            break  # puntuación: el nombre terminó
        normalizada = normalise_text(token)
        if normalizada in _CORTES:
            break
        # "de"/"del" siempre unen, nunca empiezan un nombre ("sector de
        # Miraflores", también en mayúsculas). Los artículos en minúscula
        # tampoco ("sector de la Villa Dulce"); con mayúscula SON parte del
        # nombre: "Cerro Los Placeres", "sector El Olivar".
        if normalizada in {"de", "del"} or (
            normalizada in _CONECTORES and not _es_nombre_propio(token)
        ):
            if palabras:
                pendientes.append(token)
            continue
        if not _es_nombre_propio(token):
            break
        palabras.extend(pendientes)
        pendientes = []
        palabras.append(token)
        if len(palabras) >= _MAX_PALABRAS:
            break
    return palabras


def sector_en_texto(texto: str | None) -> Sector | None:
    """Primer sector nombrado en el texto, o None.

    Exige la palabra que lo introduce —sector, cerro, población, villa, barrio—
    y un nombre propio a continuación. Lo que viene en minúscula no es un nombre
    ("el sector céntrico", "un cerro cercano") y se ignora.
    """
    crudo = " ".join(str(texto or "").split())
    if not crudo:
        return None

    for encontrado in _INTRODUCTOR.finditer(crudo):
        introductor = encontrado.group(1)
        palabras = _nombre_desde(crudo, encontrado.end())
        if not palabras:
            continue
        palabras, comuna = _recortar_comuna(palabras)
        if not palabras:
            continue

        clase = normalise_text(introductor)
        if clase not in _INTRODUCTOR_FUERA_DEL_NOMBRE:
            # "en el cerro Alegre" → "Cerro Alegre". Se respeta cómo lo escribió
            # la fuente salvo por la mayúscula inicial.
            palabras = [introductor[:1].upper() + introductor[1:], *palabras]

        nombre = " ".join(palabras)
        normalizado = normalise_text(nombre)
        if normalizado in _ORIENTACIONES or not nucleo(nombre):
            continue
        # "Villa Alemana" es una comuna, no un sector de nadie.
        if comuna_por_nombre(nombre):
            continue
        return Sector(nombre=nombre, comuna=comuna)
    return None


#: Lo que no distingue un sector de otro. Se quita para comparar con lo que OSM
#: escribe en `address.suburb`, que puede decir "Miraflores" donde la prensa
#: dice "sector Miraflores Alto".
_GENERICAS = frozenset(
    {
        "sector", "cerro", "poblacion", "villa", "barrio", "alto", "alta",
        "bajo", "baja", "el", "la", "los", "las", "de", "del", "y",
    }
)


def nucleo(nombre: str | None) -> frozenset[str]:
    """Palabras que identifican al sector, sin genéricos ni tildes."""
    return frozenset(
        palabra
        for palabra in re.split(r"[^a-z0-9]+", normalise_text(nombre or ""))
        if palabra and palabra not in _GENERICAS
    )


def sectores_compatibles(a: str | None, b: str | None) -> bool:
    """¿Pueden `a` y `b` nombrar el mismo lugar?

    Un núcleo contenido en el otro: "Miraflores" y "Miraflores Alto" sí;
    "Santa Inés" y "Santa Julia" no, aunque compartan "santa". Deliberadamente
    laxo con alto/bajo —OSM no siempre los separa— y estricto con el nombre.
    """
    nucleo_a, nucleo_b = nucleo(a), nucleo(b)
    if not nucleo_a or not nucleo_b:
        return False
    return nucleo_a <= nucleo_b or nucleo_b <= nucleo_a


def clave_sector(sector: str | None, comuna: str | None) -> str | None:
    """Clave con la que el motor reconoce dos menciones del mismo sector.

    Lleva la comuna porque un nombre de sector sólo es único dentro de ella, y
    sin comuna no hay clave: "Miraflores" a secas podría estar en cualquier
    parte, y unir por eso sería inventar la corroboración.

    Compara el nombre completo normalizado y **no** el núcleo: "Miraflores Alto"
    y "Miraflores Bajo" son sectores distintos, a un kilómetro, y dos incendios
    en ellos no son el mismo incendio.
    """
    nombre = " ".join(normalise_text(sector or "").split())
    ciudad = " ".join(normalise_text(comuna or "").split())
    if not nombre or not ciudad:
        return None
    return f"{ciudad}|{nombre}"


def anotar_sector(streets: Mapping[str, Any] | None, texto: str) -> dict[str, Any]:
    """La extracción de calles, con el sector del texto agregado si lo hay.

    Agrega `sector` y `sector_clave`, y rellena `city` **sólo si faltaba**, con
    la comuna pegada al sector o, en su defecto, la que nombre el texto. El
    extractor sigue mandando sobre la comuna: esto llena un hueco, no corrige.

    Devuelve un diccionario nuevo; el de entrada no se toca.
    """
    resultado: dict[str, Any] = dict(streets or {})
    sector = sector_en_texto(texto)
    if sector is None:
        return resultado

    resultado["sector"] = sector.nombre
    if not str(resultado.get("city") or "").strip():
        comuna = sector.comuna or comuna_en_texto(texto)
        if comuna:
            resultado["city"] = comuna
            resultado["city_origen"] = "sector" if sector.comuna else "texto"

    clave = clave_sector(sector.nombre, resultado.get("city"))
    if clave:
        resultado["sector_clave"] = clave
    return resultado


__all__ = [
    "COMUNAS_NORMALIZADAS",
    "Sector",
    "anotar_sector",
    "clave_sector",
    "comuna_en_texto",
    "comuna_por_nombre",
    "nucleo",
    "sector_en_texto",
    "sectores_compatibles",
]
