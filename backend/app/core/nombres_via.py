"""Cómo comparar el nombre de una calle escrito por una fuente con el de OSM.

La central escribe «AVENIDA ALEMANIA», «PDTE. ERRAZURIZ» o «UNO NORTE»; OSM
tiene «Avenida Alemania», «Presidente Errázuriz» y «1 Norte». Las dos formas
nombran la misma vía y ninguna comparación de cadenas lo ve.

Este módulo es puro (sin red, sin base, sin `settings`) porque lo usan dos
lados que tienen que estar de acuerdo al pie de la letra: el script que arma
el índice de cruces (`scripts/osm_cruces.py`, en GitHub Actions) y la búsqueda
en ese índice (`collectors/cruces_locales.py`, en Render). Si normalizaran
distinto, el índice guardaría claves que la búsqueda nunca pide.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

from app.core.texto import sin_tildes

#: Palabras que indican el tipo de vía y no su nombre. Se sacan sólo al
#: comienzo: en «Pasaje Alemania Norte» la vía es «Alemania Norte».
TIPOS_DE_VIA = frozenset(
    {
        "avenida", "av", "avda", "calle", "pasaje", "pje", "psje", "camino", "callejon",
        # Erratas que escribe la central de Los Andes.
        "avendia", "aveninda",
    }
)

#: Abreviaturas frecuentes en despachos y prensa, a su forma de OSM.
ABREVIATURAS = {
    "pdte": "presidente",
    "pte": "presidente",
    "gral": "general",
    "dr": "doctor",
    "tte": "teniente",
    "cap": "capitan",
    "cptn": "capitan",
    "sta": "santa",
    "sto": "santo",
    "sn": "san",
    "ing": "ingeniero",
    "prof": "profesor",
    "almte": "almirante",
    "cmdte": "comandante",
}

#: Los números escritos con letras pasan a cifras: Viña tiene «1 Norte» en OSM
#: y «UNO NORTE» en los despachos; los pasajes van de «Pasaje Dos» a
#: «Pasaje 2» según quién los haya cargado. Sólo cardinales: «Primero de Mayo»
#: y «Segundo» como nombre propio son demasiado ambiguos para tocarlos.
_CARDINALES = (
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete",
    "dieciocho", "diecinueve", "veinte",
)
NUMEROS = {palabra: str(valor) for valor, palabra in enumerate(_CARDINALES)}

#: Palabras que no distinguen una calle de otra.
VACIAS = frozenset({"de", "del", "la", "las", "los", "el", "y"})

_NO_ALFANUM = re.compile(r"[^a-z0-9 ]+")
#: Numeración pegada al final de la calle: «1 PONIENTE 640», «ALVAREZ 1150».
_NUMERACION_FINAL = re.compile(r"\s+(?:n[°º]?\s*)?\d{3,5}\s*$", re.IGNORECASE)


def sin_numeracion(calle: str | None) -> str:
    """La calle sin el número de casa que la fuente le dejó pegado al final.

    Sólo números de tres o más cifras, y nunca en una ruta: «CALLE 13» y «RUTA
    160» son nombres. Se aplica a lo que escribe la fuente, no a OSM.
    """
    texto = " ".join(str(calle or "").split())
    if not texto or sin_tildes(texto).startswith("ruta"):
        return texto
    recortada = _NUMERACION_FINAL.sub("", texto)
    return recortada if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", recortada) else texto


def nombre_nucleo(nombre: str | None) -> tuple[str, ...]:
    """Las palabras que nombran la calle, sin tildes, tipo de vía ni abreviaturas.

    «AVENIDA ALEMANIA» → («alemania»,); «Av. Pdte. Errázuriz» → («presidente»,
    «errazuriz»). Las palabras vacías se conservan salvo al comienzo («Las
    Monjas» → («las», «monjas»)) porque a veces son parte del nombre.
    """
    texto = _NO_ALFANUM.sub(" ", " ".join(sin_tildes(nombre).split()).replace(".", " "))
    palabras = [NUMEROS.get(p, ABREVIATURAS.get(p, p)) for p in texto.split()]
    while palabras and palabras[0] in TIPOS_DE_VIA:
        palabras.pop(0)
    return tuple(palabras)


def significativas(nucleo: Sequence[str]) -> tuple[str, ...]:
    return tuple(p for p in nucleo if p not in VACIAS)


def nombres_calzan(buscado: str | None, osm: str | None) -> bool:
    """¿El nombre de OSM es la calle que nombró la fuente?

    Calza si, sin tildes ni tipo de vía, las palabras significativas son las
    mismas, o si las de la fuente son el final de las de OSM: la central
    escribe «ALVAREZ» y OSM «Doctor Álvarez». Al revés no: si la fuente nombra
    más que OSM («ANDRES BELLO» contra una «Bello»), es otra calle.
    """
    a = significativas(nombre_nucleo(buscado))
    b = significativas(nombre_nucleo(osm))
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) < len(b) and b[-len(a) :] == a


def ultima_palabra(nombre: str | None) -> str | None:
    """La última palabra significativa: la clave por la que se indexa una vía.

    Si `nombres_calzan(a, b)`, las dos tienen la misma última palabra (la
    fuente es un sufijo de OSM), así que buscar por ella no pierde ningún
    candidato y descarta casi todo lo demás.
    """
    palabras = significativas(nombre_nucleo(nombre))
    return palabras[-1] if palabras else None


def parecidos(buscado: str | None, osm: str | None, minimo: float) -> float | None:
    """Similitud entre dos nombres que no calzan exacto, o None si no alcanza `minimo`.

    Para los errores de tipeo de la central («AYQUINA» por «Aiquina», «RIO
    AYSEN» por «Río Aisén»). Se compara lo significativo y, si la fuente nombra
    menos palabras que OSM, contra el final de igual largo, con la misma regla
    del sufijo de `nombres_calzan`. Nombres de menos de cinco letras no se
    comparan: entre «Lira» y «Lima» no hay forma de saber cuál quiso decir.
    """
    a = significativas(nombre_nucleo(buscado))
    b = significativas(nombre_nucleo(osm))
    if not a or not b or len(a) > len(b):
        return None
    texto_a = " ".join(a)
    texto_b = " ".join(b[-len(a) :])
    if len(texto_a) < 5:
        return None
    razon = difflib.SequenceMatcher(None, texto_a, texto_b).ratio()
    return razon if razon >= minimo else None


__all__ = [
    "ABREVIATURAS",
    "NUMEROS",
    "TIPOS_DE_VIA",
    "VACIAS",
    "nombre_nucleo",
    "nombres_calzan",
    "parecidos",
    "significativas",
    "sin_numeracion",
    "ultima_palabra",
]
