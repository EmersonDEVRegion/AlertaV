"""Vínculos entre familias: dos hechos distintos, cerca y con poco tiempo de diferencia.

Qué es y qué no
---------------
El motor separa las familias a propósito (ver «Aislamiento entre familias» en
`engine.py`): un choque y un corte de luz en la misma cuadra no son el mismo
hecho y no se funden. Pero a veces uno explica al otro —un auto contra un poste
deja sin luz a la cuadra— y quien mira el choque querría saber que hay un corte
a 150 m que empezó 12 minutos después.

Este módulo deja dicho eso, y nada más:

* **No funde** incidentes ni mueve señales de uno a otro.
* **No suma confianza**: un corte cerca no hace más cierto un choque.
* **No afirma causa.** La ficha dice «cerca y poco después», nunca «por».

Con `CORRELATION_RELACIONES=sombra` (el valor de partida) los vínculos se
calculan y se guardan, pero la API no los entrega: primero se revisan a mano
(`scripts/revisar_relaciones.py`) para saber cuántos aparecen y cuántos son
plausibles. Con `visible`, la ficha los muestra. Con `off`, no se calculan.

Las reglas
----------
Son pocas y explícitas, cada una con su radio y su ventana. El desfase es la
hora del otro hecho menos la de éste: el corte suele aparecer después del choque,
pero la distribuidora a veces publica primero, de ahí los minutos negativos.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.enums import EventType, IncidentType


@dataclass(frozen=True, slots=True)
class Regla:
    nombre: str
    #: Tipos del incidente desde el que se mira.
    origen: frozenset[IncidentType]
    #: Tipos del otro: incidentes o señales (`EventType`), uno de los dos.
    destino_incidentes: frozenset[IncidentType] = frozenset()
    destino_eventos: frozenset[EventType] = frozenset()
    radio_m: float = 300.0
    #: Desfase admitido (hora del otro − hora del origen).
    desde: timedelta = timedelta(0)
    hasta: timedelta = timedelta(0)


REGLAS: tuple[Regla, ...] = (
    # Un auto contra un poste o un cable cortado: el corte cae en la cuadra y
    # aparece en el mapa de la distribuidora minutos u horas después.
    Regla(
        "choque_corte_luz",
        origen=frozenset({IncidentType.ACCIDENT}),
        destino_incidentes=frozenset({IncidentType.POWER_OUTAGE}),
        radio_m=300.0,
        desde=timedelta(minutes=-10),
        hasta=timedelta(minutes=90),
    ),
    # Bomberos corta la energía para trabajar, o el fuego alcanza el empalme.
    Regla(
        "incendio_corte_luz",
        origen=frozenset({IncidentType.STRUCTURAL_FIRE}),
        destino_incidentes=frozenset({IncidentType.POWER_OUTAGE}),
        radio_m=300.0,
        desde=timedelta(minutes=-10),
        hasta=timedelta(minutes=120),
    ),
    # Un deslizamiento o un anegamiento que corta el camino. El corte de ruta
    # (MOP, MTT) es una señal, no un incidente: por eso el destino es un evento.
    Regla(
        "deslizamiento_corte_ruta",
        origen=frozenset({IncidentType.LANDSLIDE, IncidentType.FLOOD}),
        destino_eventos=frozenset({EventType.ROAD_CLOSURE}),
        radio_m=500.0,
        desde=timedelta(hours=-3),
        hasta=timedelta(hours=3),
    ),
)


@dataclass(frozen=True, slots=True)
class Punto:
    """Lo mínimo de un incidente o una señal para emparejar."""

    id: int
    tipo: str
    lat: float
    lon: float
    hora: datetime


@dataclass(frozen=True, slots=True)
class Par:
    regla: str
    origen_id: int
    otro_id: int
    #: `True` si el otro es un incidente; `False` si es una señal.
    otro_es_incidente: bool
    distancia_m: float
    desfase_s: int


def tipos_de_origen(reglas: Iterable[Regla] = REGLAS) -> set[IncidentType]:
    return {t for r in reglas for t in r.origen}


def tipos_de_destino(reglas: Iterable[Regla] = REGLAS) -> tuple[set[IncidentType], set[EventType]]:
    incidentes = {t for r in reglas for t in r.destino_incidentes}
    eventos = {t for r in reglas for t in r.destino_eventos}
    return incidentes, eventos


def margen(reglas: Iterable[Regla] = REGLAS) -> timedelta:
    """Cuánto antes y después de un origen puede estar su destino."""
    return max(max(abs(r.desde), abs(r.hasta)) for r in reglas)


def _valor(tipo: object) -> str:
    return str(getattr(tipo, "value", tipo))


def emparejar(
    origenes: Sequence[Punto],
    incidentes: Sequence[Punto],
    eventos: Sequence[Punto],
    *,
    distancia: Callable[[float, float, float, float], float],
    reglas: Iterable[Regla] = REGLAS,
) -> list[Par]:
    """Todos los pares que alguna regla admite. Función pura.

    `incidentes` y `eventos` son los candidatos a destino. Un mismo par puede
    salir de una sola regla (los tipos de origen no se repiten entre reglas).
    """
    pares: list[Par] = []
    for regla in reglas:
        tipos_origen = {_valor(t) for t in regla.origen}
        destinos: list[tuple[Punto, bool]] = [
            (p, True) for p in incidentes if p.tipo in {_valor(t) for t in regla.destino_incidentes}
        ] + [(p, False) for p in eventos if p.tipo in {_valor(t) for t in regla.destino_eventos}]
        for origen in origenes:
            if origen.tipo not in tipos_origen:
                continue
            for otro, es_incidente in destinos:
                if es_incidente and otro.id == origen.id:
                    continue
                desfase = otro.hora - origen.hora
                if not (regla.desde <= desfase <= regla.hasta):
                    continue
                metros = distancia(origen.lat, origen.lon, otro.lat, otro.lon)
                if metros > regla.radio_m:
                    continue
                pares.append(
                    Par(
                        regla=regla.nombre,
                        origen_id=origen.id,
                        otro_id=otro.id,
                        otro_es_incidente=es_incidente,
                        distancia_m=round(metros, 1),
                        desfase_s=int(desfase.total_seconds()),
                    )
                )
    return pares


#: Cómo se nombra al otro hecho en la ficha.
NOMBRE_DESTINO: dict[str, str] = {
    IncidentType.POWER_OUTAGE.value: "Corte de luz",
    IncidentType.ACCIDENT.value: "Accidente",
    IncidentType.STRUCTURAL_FIRE.value: "Incendio estructural",
    IncidentType.LANDSLIDE.value: "Deslizamiento",
    IncidentType.FLOOD.value: "Anegamiento",
    EventType.ROAD_CLOSURE.value: "Corte de ruta",
}


__all__ = [
    "NOMBRE_DESTINO",
    "REGLAS",
    "Par",
    "Punto",
    "Regla",
    "emparejar",
    "margen",
    "tipos_de_destino",
    "tipos_de_origen",
]
