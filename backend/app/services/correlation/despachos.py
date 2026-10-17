"""¿Dos despachos de Bomberos son el mismo llamado?

Para el resto del motor una señal es un punto con hora: si dos caen cerca, son
el mismo hecho. Para los despachos eso no alcanza. La central ya dijo con la
dirección si es el mismo llamado: un refuerzo repite la esquina («SALE CJ-3 A
Clave 1-1 CALLE 3 / MATEO DE TORO Y ZAMBRANO» después de «MATEO DE TORO Y
ZAMBRANO / ALMIRANTE GOMEZ CARRENO»), y un llamado nuevo nombra otras calles.

En los 30 días anteriores al 17-10-2026, de 23 incidentes con varios despachos
los refuerzos legítimos repetían siempre al menos una calle, y los mal fundidos
nunca: un 1-1 en Urriola / Álvaro Besa a las 22:23 terminó junto a un 2-1
forestal en Serrano / Almirante Goñi a las 03:49, a 437 m; en Los Andes, un
llamado por clima, uno eléctrico, una caída de árbol y una persona encerrada
quedaron en un solo incidente. Ver `docs/arquitectura.md`.

La regla, siempre dentro de `brecha` y sólo entre despachos:

* **La misma esquina** (las dos calles calzan): mismo llamado hasta
  `ESQUINA_RADIO_M`. La distancia ahí es error de geocodificación: en la Ruta
  68, «SECTOR PASARELA PLACILLA KM 99» repetido a los 7 minutos cayó a 927 m.
* **Una calle compartida**: mismo llamado a `radio_m` o menos; o, si el segundo
  sale enseguida (`REFUERZO_BRECHA`), hasta `REFUERZO_FACTOR` veces ese radio.
  En Quilpué el CJ-3 salió a los 22 minutos a «CALLE 3 / MATEO DE TORO Y
  ZAMBRANO», a 870 m del primero; en la Ruta 68, dos accidentes en kilómetros
  distintos a 96 minutos y 927 m son dos accidentes.
* **Ninguna calle compartida**: otro llamado, aunque esté en la manzana del lado.
* **Kilómetros distintos** de la misma ruta (más de `KM_TOLERANCIA`): otro
  llamado aunque el punto caiga en el mismo lugar. Sin Viper, «RUTA LAS PALMAS /
  KILOMETRO 4» y «/ KILOMETRO 7» se geocodifican al mismo punto de la ruta.

Entre un despacho y una señal de otra fuente (FIRMS, CONAF, un vecino) la regla
no aplica: ahí sigue mandando la geometría del perfil de la familia.

Módulo puro, sin base ni `settings`, para testearlo con los casos reales.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from app.core.nombres_via import nombres_calzan
from app.models.enums import EventSource

_RADIO_TIERRA_M = 6_371_008.8

#: La misma esquina repetida: hasta acá la diferencia es de geocodificación.
ESQUINA_RADIO_M = 2_000.0
#: Un refuerzo que sale enseguida puede entrar por otro punto de la misma calle.
REFUERZO_BRECHA = timedelta(minutes=30)
REFUERZO_FACTOR = 2.0
#: Diferencia de kilómetro de ruta a partir de la cual son dos lugares.
KM_TOLERANCIA = 1.0

_KILOMETRO = re.compile(r"\b(?:km|kil[oó]metro)\.?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def kilometro(calles: Sequence[str]) -> float | None:
    """El kilómetro de ruta que nombra la dirección, si nombra uno."""
    for calle in calles:
        encontrado = _KILOMETRO.search(calle)
        if encontrado:
            return float(encontrado.group(1).replace(",", "."))
    return None


class SenalConLugar(Protocol):
    @property
    def lat(self) -> float: ...
    @property
    def lon(self) -> float: ...
    @property
    def timestamp(self) -> datetime: ...
    @property
    def source(self) -> EventSource: ...
    @property
    def calles(self) -> tuple[str, ...]: ...


def _distancia_m(a: SenalConLugar, b: SenalConLugar) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _RADIO_TIERRA_M * math.asin(min(1.0, math.sqrt(h)))


def es_despacho(senal: SenalConLugar) -> bool:
    return senal.source is EventSource.BOMBEROS


def _calzan(x: str, y: str) -> bool:
    return nombres_calzan(x, y) or nombres_calzan(y, x)


def calles_compartidas(a: Sequence[str], b: Sequence[str]) -> bool:
    """¿Nombran alguna calle en común? En los dos sentidos: «ALVAREZ» y
    «DOCTOR ALVAREZ» son la misma, la escriba quien la escriba primero."""
    return any(_calzan(x, y) for x in a for y in b)


def misma_esquina(a: Sequence[str], b: Sequence[str]) -> bool:
    """¿Las dos nombran el mismo par de calles, en cualquier orden?"""
    if len(a) < 2 or len(b) < 2:
        return False
    return all(any(_calzan(x, y) for y in b) for x in a[:2]) and all(
        any(_calzan(y, x) for x in a) for y in b[:2]
    )


def mismo_llamado(a: SenalConLugar, b: SenalConLugar, *, radio_m: float, brecha: timedelta) -> bool:
    """¿Dos despachos describen el mismo llamado? Sólo entre despachos."""
    if not (es_despacho(a) and es_despacho(b)):
        return False
    separacion = abs(a.timestamp - b.timestamp)
    if separacion > brecha:
        return False
    km_a, km_b = kilometro(a.calles), kilometro(b.calles)
    if km_a is not None and km_b is not None and abs(km_a - km_b) > KM_TOLERANCIA:
        return False
    distancia = _distancia_m(a, b)
    if misma_esquina(a.calles, b.calles):
        return distancia <= ESQUINA_RADIO_M
    if not calles_compartidas(a.calles, b.calles):
        return False
    if distancia <= radio_m:
        return True
    return separacion <= REFUERZO_BRECHA and distancia <= radio_m * REFUERZO_FACTOR


def compatibles(
    nuevos: Sequence[SenalConLugar],
    existentes: Sequence[SenalConLugar],
    *,
    radio_m: float,
    brecha: timedelta,
) -> bool:
    """¿Pueden ir al mismo incidente estos dos grupos de señales?

    Si alguno de los dos no tiene despachos, la regla no tiene nada que decir
    (lo decide la geometría). Si los dos tienen, basta con que un despacho de
    cada lado sea el mismo llamado: un refuerzo encadena con el anterior aunque
    no repita la misma esquina que el primero.
    """
    despachos_nuevos = [s for s in nuevos if es_despacho(s)]
    despachos_existentes = [s for s in existentes if es_despacho(s)]
    if not despachos_nuevos or not despachos_existentes:
        return True
    return any(
        mismo_llamado(a, b, radio_m=radio_m, brecha=brecha)
        for a in despachos_nuevos
        for b in despachos_existentes
    )


def partir_por_llamado[S: SenalConLugar](
    miembros: Sequence[S],
    *,
    radio_m: float,
    brecha: timedelta,
    radio_ajenas_m: float,
) -> list[list[S]]:
    """Corta un racimo en llamados distintos.

    Los despachos se agrupan por la relación «mismo llamado», encadenada (A con
    B y B con C: un solo llamado aunque A y C no compartan calle). Cada señal de
    otra fuente va al llamado con el despacho más cercano si está a
    `radio_ajenas_m` o menos; si no, queda en un tramo propio. Así un píxel de
    FIRMS a 1,2 km de una casa que se quema no se suma a ese incendio aunque
    DBSCAN los haya encadenado con el radio de 1500 m de la familia.

    Un racimo sin despachos vuelve entero; lo mismo uno con un solo llamado y
    todas las demás señales cerca.
    """
    despachos = [m for m in miembros if es_despacho(m)]
    if not despachos:
        return [list(miembros)]

    padre = list(range(len(despachos)))

    def raiz(i: int) -> int:
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(despachos)):
        for j in range(i + 1, len(despachos)):
            if mismo_llamado(despachos[i], despachos[j], radio_m=radio_m, brecha=brecha):
                padre[raiz(j)] = raiz(i)

    llamados: dict[int, list[S]] = {}
    for i, despacho in enumerate(despachos):
        llamados.setdefault(raiz(i), []).append(despacho)
    grupos = list(llamados.values())

    sueltas: list[S] = []
    for senal in miembros:
        if es_despacho(senal):
            continue
        distancias = [min(_distancia_m(senal, d) for d in grupo) for grupo in grupos]
        mas_cerca = min(range(len(grupos)), key=distancias.__getitem__)
        if distancias[mas_cerca] <= radio_ajenas_m:
            grupos[mas_cerca].append(senal)
        else:
            sueltas.append(senal)

    if len(grupos) == 1 and not sueltas:
        return [list(miembros)]
    tramos = [sorted(grupo, key=lambda s: s.timestamp) for grupo in grupos]
    if sueltas:
        tramos.append(sorted(sueltas, key=lambda s: s.timestamp))
    return sorted(tramos, key=lambda tramo: tramo[0].timestamp)


__all__ = [
    "ESQUINA_RADIO_M",
    "KM_TOLERANCIA",
    "REFUERZO_BRECHA",
    "REFUERZO_FACTOR",
    "calles_compartidas",
    "compatibles",
    "es_despacho",
    "kilometro",
    "misma_esquina",
    "mismo_llamado",
    "partir_por_llamado",
]
