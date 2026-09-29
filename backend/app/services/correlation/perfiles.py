"""Radio y tiempo de correlación por familia de fenómeno.

Hasta el 2026-09-23 el motor usaba un solo radio (1500 m) y una sola ventana para
todo. Tres defectos salieron de ahí, los tres de la auditoría:

* **Un radio no sirve para todo.** 1500 m es razonable para un incendio forestal
  y absurdo para un choque: en Viña, dos accidentes a un kilómetro terminaban en
  el mismo incidente. Un corte de luz, al revés, cubre un sector y sus señales
  (CGE, Chilquinta, prensa) caen más lejos entre sí.
* **DBSCAN con `minpoints=1` encadena.** A–B–C a 1,4 km cada uno formaban un solo
  racimo aunque A y C estuvieran a 4 km y a tres horas. `brecha` corta el racimo
  donde se abre el tiempo (`partir_por_brecha`).
* **Una señal se pegaba a cualquier incidente «vivo»**, sin mirar cuándo había
  ocurrido. Una nota de prensa de las 15:00 se adhería a un choque de las 09:00
  a 900 m. `brecha` también es la tolerancia temporal de esa adhesión.

Además, `edad_max`: cuánto puede llegar tarde una señal y todavía agruparse.
FIRMS publica unas tres horas después de la pasada del satélite, y Render dormido
atrasa cualquier collector; antes, lo que llegaba fuera de la ventana por
`timestamp` quedaba sin incidente para siempre.

**Son hipótesis de partida, como los `CORRELATION_*` de siempre.** Se calibran
con `scripts/replay_correlacion.py` y con `/events/{id}/neighbours`. El
interruptor es `CORRELATION_PERFILES`: en `false` el motor vuelve exactamente al
comportamiento anterior.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, TypeVar

from app.models.enums import DEFAULT_FAMILY


@dataclass(frozen=True, slots=True)
class Perfil:
    #: `eps` del DBSCAN y radio de adhesión a un incidente, en metros.
    radio_m: float
    #: Separación temporal máxima entre dos señales del mismo hecho.
    brecha: timedelta
    #: Antigüedad máxima (por `timestamp`) de una señal que llegó tarde.
    edad_max: timedelta


PERFILES: dict[str, Perfil] = {
    # Un incendio crece y se desplaza; FIRMS llega ~3 h tarde y CONAF actualiza
    # durante días. El radio de siempre, con más paciencia.
    "fire": Perfil(1500.0, timedelta(hours=8), timedelta(hours=24)),
    # Un choque es un punto y dura poco. 700 m cubre el error de geocodificar
    # «Av. España con Uno Norte» sin juntar dos esquinas de la misma avenida.
    "traffic": Perfil(700.0, timedelta(hours=2), timedelta(hours=6)),
    # Un corte cubre un sector entero y la reposición tarda horas.
    "power": Perfil(2000.0, timedelta(hours=6), timedelta(hours=12)),
    # Una inundación o un deslizamiento afectan un área y duran.
    "hydro": Perfil(2000.0, timedelta(hours=12), timedelta(hours=24)),
    # Rescates y despachos sin familia propia.
    DEFAULT_FAMILY: Perfil(1000.0, timedelta(hours=3), timedelta(hours=6)),
}

#: Holgura de radio según la precisión del punto (`raw_data._geocoding.precision`).
#: Un punto `street` puede estar en cualquier parte de una avenida de dos
#: kilómetros; uno `sector` es el centroide de un barrio. Sin `_geocoding`
#: —FIRMS, CONAF, el GPS de un reporte ciudadano— el punto cuenta como exacto.
HOLGURA_M: dict[str | None, float] = {
    None: 0.0,
    "intersection": 0.0,
    "street": 800.0,
    "sector": 1200.0,
}

#: La mayor `edad_max`: acota la consulta de candidatos antes de filtrar por familia.
EDAD_MAX_GLOBAL: timedelta = max(p.edad_max for p in PERFILES.values())


def perfil(familia: str | None) -> Perfil:
    return PERFILES.get(familia or DEFAULT_FAMILY, PERFILES[DEFAULT_FAMILY])


def holgura(precisiones: Iterable[str | None]) -> float:
    """La holgura del punto MENOS preciso del grupo. Precisión desconocida = exacto."""
    return max((HOLGURA_M.get(p, 0.0) for p in precisiones), default=0.0)


class _ConHora(Protocol):
    @property
    def timestamp(self) -> datetime: ...


T = TypeVar("T", bound=_ConHora)


def partir_por_brecha(miembros: Sequence[T], brecha: timedelta) -> list[list[T]]:
    """Corta un racimo donde dos señales consecutivas se separan más que `brecha`.

    DBSCAN con `minpoints=1` es un enlace simple: basta una cadena de vecinos
    para unir dos extremos que no tienen nada que ver. Ordenar por hora y cortar
    en los huecos devuelve tramos que sí describen un mismo hecho. Un racimo sin
    huecos vuelve entero, en una sola lista.
    """
    if not miembros:
        return []
    ordenados = sorted(miembros, key=lambda m: m.timestamp)
    tramos: list[list[T]] = [[ordenados[0]]]
    for miembro in ordenados[1:]:
        if miembro.timestamp - tramos[-1][-1].timestamp > brecha:
            tramos.append([miembro])
        else:
            tramos[-1].append(miembro)
    return tramos


__all__ = [
    "EDAD_MAX_GLOBAL",
    "HOLGURA_M",
    "PERFILES",
    "Perfil",
    "holgura",
    "partir_por_brecha",
    "perfil",
]
