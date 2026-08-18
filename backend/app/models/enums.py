"""Enumeraciones de dominio.

Estos valores deben coincidir exactamente con los tipos ENUM de PostgreSQL
definidos en `sql/001_schema.sql`. Agregar un valor requiere una migración con
`ALTER TYPE ... ADD VALUE`.
"""

from __future__ import annotations

from enum import Enum


class EventSource(str, Enum):
    """Origen de la señal."""

    CITIZEN = "citizen"
    BROADCASTIFY = "broadcastify"
    NASA_FIRMS = "nasa_firms"
    CONAF = "conaf"
    SENAPRED = "senapred"
    BOMBEROS = "bomberos"
    MUNICIPALITY = "municipality"
    MEDIA = "media"
    SOCIAL_MEDIA = "social_media"
    WEATHER = "weather"
    CAMERA = "camera"
    OTHER = "other"


class EventType(str, Enum):
    """Naturaleza de la señal.

    Distinción crítica del proyecto:
      - THERMAL_ANOMALY: detección satelital. NO es un incendio confirmado.
      - SMOKE: avistamiento. NO es un incendio confirmado.
      - WILDFIRE / STRUCTURAL_FIRE: sólo cuando la fuente lo confirma.
    """

    WILDFIRE = "wildfire"
    STRUCTURAL_FIRE = "structural_fire"
    SMOKE = "smoke"
    THERMAL_ANOMALY = "thermal_anomaly"
    DISPATCH = "dispatch"
    ALERT = "alert"
    EVACUATION = "evacuation"
    RESCUE = "rescue"
    ACCIDENT = "accident"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    WEATHER_OBSERVATION = "weather_observation"
    OTHER = "other"
    UNKNOWN = "unknown"


class CollectorStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class IncidentType(str, Enum):
    """Naturaleza del FENÓMENO consolidado.

    Deliberadamente NO es `EventType`. Un incidente representa un hecho del
    mundo, no la respuesta administrativa a ese hecho: por eso aquí no existen
    `alert` ni `evacuation`. Una alerta de SENAPRED se adjunta a un incidente y
    fija su `alert_level`; jamás lo tipifica.

    `POSSIBLE_FIRE` es la pieza que evita el falso positivo más caro del
    sistema: un racimo de detecciones satelitales o de avistamientos de humo es
    exactamente eso —un posible incendio— y pintarlo como `WILDFIRE` en el mapa
    sería afirmar algo que ninguna fuente confirmó.
    """

    POSSIBLE_FIRE = "possible_fire"
    WILDFIRE = "wildfire"
    STRUCTURAL_FIRE = "structural_fire"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    ACCIDENT = "accident"
    RESCUE = "rescue"
    OTHER = "other"


class IncidentStatus(str, Enum):
    """Ciclo de vida del incidente.

    `STALE` no es lo mismo que `EXTINGUISHED`: significa que dejaron de llegar
    señales, no que alguien haya declarado el fin de la emergencia. Confundirlos
    sería inventar un dato institucional que nadie entregó.

    `MERGED` existe porque dos racimos que crecen pueden terminar siendo el
    mismo incendio. En vez de borrar el más nuevo se lo marca y se apunta a su
    sucesor: los enlaces históricos siguen siendo navegables.
    """

    ACTIVE = "active"
    CONTROLLED = "controlled"
    EXTINGUISHED = "extinguished"
    STALE = "stale"
    MERGED = "merged"
    DISMISSED = "dismissed"


#: Estados en los que el incidente sigue siendo relevante para el mapa.
OPEN_INCIDENT_STATUSES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.ACTIVE, IncidentStatus.CONTROLLED}
)


class LinkMethod(str, Enum):
    """Por qué una señal quedó unida a un incidente.

    Guardar el método —y no sólo el vínculo— es lo que permite auditar el motor:
    un `COMMUNE_TEXT` es una heurística sobre texto y merece un escrutinio
    distinto al de un `SPATIAL`, que es una coincidencia geométrica medible.
    """

    SPATIAL = "spatial"
    COMMUNE_TEXT = "commune_text"
    MANUAL = "manual"


#: Tipos de señal que el Paso A puede agrupar geométricamente.
#: Se excluyen los actos administrativos (`alert`, `evacuation`), que entran por
#: el Paso B, y el contexto meteorológico, que no es evidencia de emergencia.
CORRELATABLE_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.WILDFIRE,
        EventType.STRUCTURAL_FIRE,
        EventType.SMOKE,
        EventType.THERMAL_ANOMALY,
        EventType.DISPATCH,
        EventType.RESCUE,
        EventType.ACCIDENT,
        EventType.FLOOD,
        EventType.LANDSLIDE,
        EventType.OTHER,
        EventType.UNKNOWN,
    }
)

#: Traducción señal → fenómeno. `smoke` y `thermal_anomaly` degradan a
#: `possible_fire`: son indicios, no confirmaciones.
EVENT_TO_INCIDENT_TYPE: dict[EventType, IncidentType] = {
    EventType.WILDFIRE: IncidentType.WILDFIRE,
    EventType.STRUCTURAL_FIRE: IncidentType.STRUCTURAL_FIRE,
    EventType.SMOKE: IncidentType.POSSIBLE_FIRE,
    EventType.THERMAL_ANOMALY: IncidentType.POSSIBLE_FIRE,
    EventType.FLOOD: IncidentType.FLOOD,
    EventType.LANDSLIDE: IncidentType.LANDSLIDE,
    EventType.ACCIDENT: IncidentType.ACCIDENT,
    EventType.RESCUE: IncidentType.RESCUE,
    EventType.DISPATCH: IncidentType.OTHER,
    EventType.OTHER: IncidentType.OTHER,
    EventType.UNKNOWN: IncidentType.OTHER,
}

#: Familias de fenómeno. El Paso B sólo une una alerta a un incidente si ambos
#: pertenecen a la misma familia: una alerta roja por crecida no debe adosarse a
#: un incendio que casualmente ocurre en la misma comuna.
INCIDENT_FAMILY: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "fire",
    IncidentType.WILDFIRE: "fire",
    IncidentType.STRUCTURAL_FIRE: "fire",
    IncidentType.FLOOD: "hydro",
    IncidentType.LANDSLIDE: "hydro",
    IncidentType.ACCIDENT: "other",
    IncidentType.RESCUE: "other",
    IncidentType.OTHER: "other",
}


#: Confianza base por fuente. Espejo de `alertav.source_confidence`; sirve como
#: fallback en memoria cuando la fuente no entrega confianza propia.
#: Calibrar con datos reales tras la ventana de recolección.
SOURCE_BASE_CONFIDENCE: dict[EventSource, float] = {
    EventSource.BOMBEROS: 1.00,
    EventSource.SENAPRED: 1.00,
    EventSource.CONAF: 1.00,
    EventSource.MUNICIPALITY: 0.90,
    EventSource.MEDIA: 0.70,
    EventSource.BROADCASTIFY: 0.65,
    EventSource.NASA_FIRMS: 0.55,
    EventSource.CITIZEN: 0.50,
    EventSource.CAMERA: 0.50,
    EventSource.SOCIAL_MEDIA: 0.45,
    EventSource.OTHER: 0.30,
    EventSource.WEATHER: 0.10,
}
