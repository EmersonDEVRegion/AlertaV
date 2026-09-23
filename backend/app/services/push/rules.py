"""Reglas del notificador: a quién, de qué y cuándo. Puro, sin I/O.

Todo lo que decide si un aviso sale vive acá, separado de las consultas y del
envío, por la misma razón que `correlation/confidence.py` está separado del
motor: son las reglas que hay que poder discutir y recalibrar con un test de
diez líneas, sin levantar PostGIS.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.models.enums import (
    CONFIDENCE_EPSILON,
    IncidentStatus,
    IncidentType,
)

# ---------------------------------------------------------------------------
#  Incidentes
# ---------------------------------------------------------------------------


def incident_is_notifiable(
    *,
    status: IncidentStatus,
    incident_type: IncidentType,
    confidence: float,
    source_count: int,
    is_official_confirmed: bool,
    min_confidence: float,
    min_sources: int,
) -> bool:
    """¿Este incidente justifica despertar a alguien?

    Tres caminos, y basta uno:

    1. **Lo confirmó quien fue al lugar.** CONAF, Bomberos y las distribuidoras
       son fuentes confirmatorias: `is_official_confirmed` ya lo dice.
    2. **Es un corte de luz.** Lo informa la distribuidora sobre su propia red;
       no hay duda que resolver. Hoy siempre llega con `is_official_confirmed`,
       pero se nombra aparte para que la regla no dependa de esa casualidad.
    3. **Hay corroboración.** La confianza alcanza el umbral (0.30, el borde del
       tramo «posible») *y* la sostienen al menos `min_sources` fuentes
       distintas.

    El tercer camino exige las dos cosas porque una sola alcanza el umbral con
    demasiada facilidad: un píxel de FIRMS vale 0.40 y una alerta de Waze
    también. El umbral solo dejaría que la chimenea de Ventanas despierte a
    Quintero cada noche; la cuenta de fuentes pide que alguien más vea lo mismo.

    Sólo `active`: un incidente controlado no es una novedad, y uno `stale` es
    uno del que dejó de llegar información.
    """
    if status is not IncidentStatus.ACTIVE:
        return False
    if is_official_confirmed or incident_type is IncidentType.POWER_OUTAGE:
        return True
    # La confianza vuelve de la base como float4; ver `CONFIDENCE_EPSILON`.
    return confidence + CONFIDENCE_EPSILON >= min_confidence and source_count >= min_sources


# ---------------------------------------------------------------------------
#  Sismos
# ---------------------------------------------------------------------------

#: Relación de atenuación y umbral de percepción. Son los mismos números que
#: `frontend/src/domain/seismicReach.ts`, y tienen que serlo: el círculo que el
#: mapa dibuja alrededor de un sismo es la promesa de a quién se le avisa. Hay
#: un test que los compara con el archivo del frontend.
ATTENUATION_A = 1.7
ATTENUATION_B = 1.5
ATTENUATION_C = 3.0
PERCEPTION_INTENSITY = 2.5
ASSUMED_DEPTH_KM = 15.0
MIN_REACH_KM = 1.5


def perception_reach_km(
    magnitude: float | None, depth_km: float | None, *, max_reach_km: float
) -> float | None:
    """Radio en superficie dentro del cual el sismo probablemente se sintió.

    `I(D) = a + b·M − c·log₁₀(D)` despejada para la intensidad de percepción
    (entre los grados II y III de Mercalli), y luego el cateto horizontal desde
    el hipocentro. La derivación completa está en `seismicReach.ts`.

    Es una estimación indicativa, no un ShakeMap: no conoce el suelo ni la
    directividad de la ruptura. Sirve para que un M 3,6 en Quillota le avise a
    Quillota y no a Los Andes, que es lo que se le pide.

    `None` cuando no se puede afirmar que se haya sentido en ningún lado: sin
    magnitud, o con el foco más lejos que el alcance.
    """
    if magnitude is None or math.isnan(magnitude):
        return None
    depth = abs(depth_km) if depth_km is not None else ASSUMED_DEPTH_KM
    hypocentral = 10 ** (
        (ATTENUATION_A + ATTENUATION_B * magnitude - PERCEPTION_INTENSITY) / ATTENUATION_C
    )
    squared = hypocentral**2 - depth**2
    if squared <= 0:
        return None
    radius = math.sqrt(squared)
    if radius < MIN_REACH_KM:
        return None
    return min(radius, max_reach_km)


@dataclass(frozen=True, slots=True)
class QuakeView:
    """Un sismo tal como lo reportó UNA red."""

    key: str
    """`raw_events.external_id`: `csn:379889` o `usgs:us6000tlm3`."""
    provider: str
    timestamp: datetime
    lat: float
    lon: float
    magnitude: float | None
    mag_type: str | None = None
    depth_km: float | None = None
    place: str | None = None
    url: str | None = None


@dataclass(slots=True)
class QuakeGroup:
    """El mismo sismo visto por una o más redes."""

    representative: QuakeView
    keys: list[str] = field(default_factory=list)


#: Dos reportes son el mismo sismo si ocurrieron a menos de esto…
SAME_QUAKE_SECONDS = 90.0
#: …y sus epicentros distan menos de esto. Las soluciones del CSN y del USGS
#: para un mismo evento suelen diferir en decenas de kilómetros, sobre todo mar
#: adentro; en una ventana de 90 s, dos sismos reales a menos de 150 km son una
#: coincidencia rarísima, y el costo de confundirlos es un aviso de menos por un
#: sismo que igual se sintió en el mismo lugar.
SAME_QUAKE_KM = 150.0

#: Orden de preferencia para elegir qué versión se cuenta. El CSN primero: es la
#: red oficial de Chile, publica la referencia en castellano («25 km al O de
#: Valparaíso») y sus soluciones las revisa un analista.
_PROVIDER_RANK = {"csn": 0, "usgs": 1}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def group_quakes(quakes: Sequence[QuakeView]) -> list[QuakeGroup]:
    """Junta los reportes del CSN y del USGS que describen el mismo sismo.

    Sin esto, cada sismo sentido llegaría dos veces al teléfono, con dos
    magnitudes distintas y la sensación de que hubo dos.

    El grupo guarda las claves de TODOS sus miembros, no sólo la del elegido, y
    eso es lo que hace funcionar la deduplicación en el tiempo: si el USGS
    publica primero y se avisa con su clave, y cinco minutos después aparece la
    versión del CSN, el grupo nuevo contiene las dos claves y el notificador ve
    que una ya se avisó.
    """
    ordered = sorted(quakes, key=lambda q: q.timestamp)
    groups: list[QuakeGroup] = []
    for quake in ordered:
        home = next(
            (
                group
                for group in groups
                if abs((quake.timestamp - group.representative.timestamp).total_seconds())
                <= SAME_QUAKE_SECONDS
                and haversine_km(
                    quake.lat, quake.lon, group.representative.lat, group.representative.lon
                )
                <= SAME_QUAKE_KM
            ),
            None,
        )
        if home is None:
            groups.append(QuakeGroup(representative=quake, keys=[quake.key]))
            continue
        home.keys.append(quake.key)
        if _preferred(quake, home.representative):
            home.representative = quake
    return groups


def _preferred(candidate: QuakeView, current: QuakeView) -> bool:
    """¿`candidate` es mejor versión del sismo que `current`?"""
    rank_new = _PROVIDER_RANK.get(candidate.provider, 9)
    rank_old = _PROVIDER_RANK.get(current.provider, 9)
    if rank_new != rank_old:
        return rank_new < rank_old
    # Misma red: la que trae magnitud le gana a la que no.
    return current.magnitude is None and candidate.magnitude is not None


__all__ = [
    "ASSUMED_DEPTH_KM",
    "QuakeGroup",
    "QuakeView",
    "group_quakes",
    "haversine_km",
    "incident_is_notifiable",
    "perception_reach_km",
]
