"""Confidence Engine — cuánto vale un incidente dadas sus señales.

Todo este módulo es puro: entra una lista de `SignalView`, sale un número con su
derivación. Sin sesión, sin red, sin reloj. Es la parte del sistema que hay que
poder recalibrar con fixtures reales en segundos.

Las reglas
----------

1. **CONAF y Bomberos confirman.** Son los organismos que van al fuego; su
   registro *es* la confirmación del hecho. Una sola señal suya lleva el
   incidente a 1.0.

2. **SENAPRED declara la respuesta, no el fenómeno.** Su alerta es cierta al
   100 % —el acto administrativo existe, lo firmó el organismo— y eso se refleja
   en `alert_confidence = 1.0` y en `alert_level`. Sobre el fenómeno aporta
   corroboración muy fuerte, pero **no saturante**, por dos razones concretas:
   la alerta no observa el incendio (declara la respuesta del Estado a él) y su
   vínculo con este incidente en particular se estableció por coincidencia de
   comuna, que es una heurística sobre texto, no una medición geométrica.
   Confundir ambos ejes sería declarar "aquí hay fuego, 100 % seguro" en un
   punto que ninguna fuente observó.

3. **FIRMS corrobora, no confirma.** Un techo explícito (`ceiling`) impide que
   cualquier cantidad de píxeles satelitales llegue sola a 1.0. Cruzada con otra
   fuente sí empuja hacia arriba: para eso existe el motor.

4. **Los reportes ciudadanos parten en la banda 0.40–0.60** y suben de forma
   progresiva con cada reporte cercano adicional, con rendimientos decrecientes
   y un techo por debajo de la confirmación institucional.

Cómo se combinan
----------------

Dentro de una misma fuente las señales son **parcialmente redundantes**: cuatro
píxeles de la misma pasada de VIIRS son casi una sola observación, y tres
vecinos del mismo cerro miran el mismo humo. Por eso el aporte de la señal
`k`-ésima de una fuente se descuenta por `decay^k`.

Entre fuentes distintas sí hay independencia —ahí está todo el valor de
correlacionar— y se combinan con *noisy-OR*: ``1 - Π(1 - wᵢ)``. Dos indicios
mediocres de origen distinto valen más que cuatro del mismo origen, que es
exactamente lo que uno querría que dijera el número.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.collectors.geoservices import normalise_text
from app.models.enums import (
    EVENT_TO_INCIDENT_TYPE,
    EventSource,
    EventType,
    IncidentStatus,
    IncidentType,
)

#: Se versiona porque queda escrito en `incidents.confidence_breakdown`. Cuando
#: la calibración cambie los pesos, los incidentes viejos siguen diciendo con qué
#: reglas se calcularon.
POLICY_VERSION = "1.0.0"

#: Ninguna combinación de señales no confirmatorias alcanza la certeza. Sólo un
#: organismo que fue al lugar puede cerrar ese último tramo.
UNCONFIRMED_CEILING = 0.95


@dataclass(frozen=True, slots=True)
class SourceRule:
    """Cómo pesa una fuente.

    `min_weight`/`max_weight` acotan la confianza que trae la propia señal desde
    la ingesta. Así una detección VIIRS de calidad alta (0.80) pesa más que una
    de calidad baja (0.30) sin que ninguna se salga de la banda de su fuente.
    """

    min_weight: float
    max_weight: float
    #: Descuento de la señal k-ésima de la misma fuente: `weight * decay**k`.
    redundancy_decay: float
    #: Techo del aporte de esta fuente por sí sola.
    ceiling: float
    #: ¿Confirma el fenómeno? Sólo quien fue al lugar.
    confirming: bool = False
    #: ¿Declara un estado de alerta oficial? Alimenta `alert_confidence`.
    declares_alert: bool = False


RULES: dict[EventSource, SourceRule] = {
    # -- Confirman el hecho --------------------------------------------------
    EventSource.CONAF: SourceRule(1.0, 1.0, 1.0, 1.0, confirming=True),
    EventSource.BOMBEROS: SourceRule(1.0, 1.0, 1.0, 1.0, confirming=True),
    # -- Declara la respuesta del Estado -------------------------------------
    # Techo 0.85 sobre el fenómeno; 1.0 sobre el estado de alerta, que va aparte.
    EventSource.SENAPRED: SourceRule(0.85, 0.85, 0.5, 0.85, declares_alert=True),
    # -- Corroboran ----------------------------------------------------------
    EventSource.MUNICIPALITY: SourceRule(0.60, 0.80, 0.5, 0.85),
    EventSource.MEDIA: SourceRule(0.40, 0.60, 0.5, 0.75),
    EventSource.BROADCASTIFY: SourceRule(0.40, 0.60, 0.6, 0.80),
    # FIRMS: por sí sola nunca da 100 %. Es la regla explícita del proyecto.
    EventSource.NASA_FIRMS: SourceRule(0.30, 0.60, 0.5, 0.75),
    EventSource.CAMERA: SourceRule(0.35, 0.55, 0.5, 0.70),
    # Ciudadanos: banda de partida 0.40–0.60, progresivo, techo por debajo de
    # la confirmación institucional.
    EventSource.CITIZEN: SourceRule(0.40, 0.60, 0.6, 0.80),
    EventSource.SOCIAL_MEDIA: SourceRule(0.25, 0.45, 0.5, 0.65),
    EventSource.OTHER: SourceRule(0.15, 0.30, 0.5, 0.45),
    # -- Contexto, no evidencia ----------------------------------------------
    # Que haya viento y 34 °C no es prueba de que algo se esté quemando.
    EventSource.WEATHER: SourceRule(0.0, 0.0, 0.0, 0.0),
}

DEFAULT_RULE = SourceRule(0.15, 0.30, 0.5, 0.45)

#: Alias del campo de estado en la capa de CONAF.
_STATE_ALIASES: tuple[str, ...] = ("estado", "estado_incendio", "situacion", "status")

#: Estado declarado por la fuente → estado del incidente.
_STATE_MARKERS: tuple[tuple[str, IncidentStatus], ...] = (
    ("extinguido", IncidentStatus.EXTINGUISHED),
    ("apagado", IncidentStatus.EXTINGUISHED),
    ("controlado", IncidentStatus.CONTROLLED),
    ("combate", IncidentStatus.ACTIVE),
    ("activo", IncidentStatus.ACTIVE),
    ("en curso", IncidentStatus.ACTIVE),
)

#: Severidad relativa de los niveles de alerta de SENAPRED.
_ALERT_SEVERITY: dict[str, int] = {
    "roja": 4,
    "amarilla": 3,
    "temprana_preventiva": 2,
    "verde": 1,
    "otra": 0,
}

_TITLE_BY_TYPE: dict[IncidentType, str] = {
    IncidentType.POSSIBLE_FIRE: "Posible incendio",
    IncidentType.WILDFIRE: "Incendio forestal",
    IncidentType.STRUCTURAL_FIRE: "Incendio estructural",
    IncidentType.FLOOD: "Inundación",
    IncidentType.LANDSLIDE: "Remoción en masa",
    IncidentType.ACCIDENT: "Accidente",
    IncidentType.RESCUE: "Rescate",
    IncidentType.OTHER: "Emergencia",
}


@dataclass(frozen=True, slots=True)
class SignalView:
    """Lo mínimo que el motor necesita de una señal.

    Existe para desacoplar el cálculo del ORM: los tests arman `SignalView` a
    mano y no necesitan una base de datos para verificar una regla de confianza.
    """

    source: EventSource
    type: EventType
    confidence: float
    timestamp: datetime
    raw_data: Mapping[str, Any] = field(default_factory=dict)
    commune: str | None = None
    text: str | None = None

    @classmethod
    def from_orm(cls, event: Any) -> SignalView:
        return cls(
            source=EventSource(getattr(event.source, "value", event.source)),
            type=EventType(getattr(event.type, "value", event.type)),
            confidence=float(event.confidence),
            timestamp=event.timestamp,
            raw_data=event.raw_data or {},
            commune=event.commune,
            text=event.text,
        )


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    confidence: float
    is_official_confirmed: bool
    alert_confidence: float
    alert_level: str | None
    sources: tuple[EventSource, ...]
    breakdown: dict[str, Any]


def rule_for(source: EventSource) -> SourceRule:
    return RULES.get(source, DEFAULT_RULE)


def _source_contribution(rule: SourceRule, confidences: Sequence[float]) -> float:
    """Aporte agregado de una fuente, con redundancia intra-fuente descontada.

    Las señales se ordenan de mayor a menor confianza para que la más creíble
    sea la que pesa sin descuento; las siguientes son, en el mejor de los casos,
    la misma observación repetida.
    """
    if not confidences or rule.max_weight <= 0.0:
        return 0.0

    complement = 1.0
    for rank, raw in enumerate(sorted(confidences, reverse=True)):
        bounded = min(max(raw, rule.min_weight), rule.max_weight)
        complement *= 1.0 - bounded * (rule.redundancy_decay**rank)
    return min(1.0 - complement, rule.ceiling)


def score(signals: Iterable[SignalView]) -> ConfidenceResult:
    """Confianza del incidente y su derivación completa."""
    grouped: dict[EventSource, list[float]] = defaultdict(list)
    alert_level: str | None = None
    alert_severity = -1

    for signal in signals:
        grouped[signal.source].append(float(signal.confidence))

        rule = rule_for(signal.source)
        if rule.declares_alert and signal.type in (
            EventType.ALERT,
            EventType.EVACUATION,
        ):
            level = str(signal.raw_data.get("_alert_level") or "otra")
            severity = _ALERT_SEVERITY.get(level, 0)
            if severity > alert_severity:
                alert_severity, alert_level = severity, level

    by_source: dict[str, Any] = {}
    contributions: list[float] = []
    confirmed = False

    for source, confidences in grouped.items():
        rule = rule_for(source)
        contribution = _source_contribution(rule, confidences)
        confirmed = confirmed or (rule.confirming and bool(confidences))
        contributions.append(contribution)
        by_source[source.value] = {
            "signals": len(confidences),
            "contribution": round(contribution, 4),
            "ceiling": rule.ceiling,
            "confirming": rule.confirming,
        }

    complement = 1.0
    for contribution in contributions:
        complement *= 1.0 - contribution
    combined = 1.0 - complement

    if confirmed:
        confidence, applied = 1.0, "confirming_source"
    elif combined > UNCONFIRMED_CEILING:
        confidence, applied = UNCONFIRMED_CEILING, "unconfirmed_ceiling"
    else:
        # El techo por fuente aislada ya quedó aplicado dentro de
        # `_source_contribution`: una fuente sola nunca supera su `ceiling`.
        confidence, applied = combined, None

    alert_confidence = 1.0 if alert_level is not None else 0.0

    return ConfidenceResult(
        confidence=round(confidence, 4),
        is_official_confirmed=confirmed,
        alert_confidence=alert_confidence,
        alert_level=alert_level,
        sources=tuple(sorted(grouped, key=lambda item: item.value)),
        breakdown={
            "policy_version": POLICY_VERSION,
            "signals": sum(len(values) for values in grouped.values()),
            "by_source": by_source,
            "combined": round(combined, 4),
            "ceiling_applied": applied,
            "alert": {"level": alert_level, "confidence": alert_confidence},
        },
    )


def resolve_type(signals: Iterable[SignalView]) -> IncidentType:
    """Tipo del fenómeno.

    Si una fuente confirmatoria dice qué es, eso es lo que es. Si no, gana el
    tipo mejor sostenido por confianza acumulada — pero `smoke` y
    `thermal_anomaly` ya vienen degradados a `possible_fire` por el mapa de
    `enums`, así que un racimo puramente satelital jamás se rotula `wildfire`.
    """
    weighted: dict[IncidentType, float] = defaultdict(float)
    confirmed: dict[IncidentType, float] = defaultdict(float)

    for signal in signals:
        incident_type = EVENT_TO_INCIDENT_TYPE.get(signal.type)
        if incident_type is None:
            continue
        weighted[incident_type] += float(signal.confidence)
        if rule_for(signal.source).confirming:
            confirmed[incident_type] += float(signal.confidence)

    pool = confirmed or weighted
    if not pool:
        return IncidentType.POSSIBLE_FIRE
    return max(pool.items(), key=lambda item: (item[1], item[0].value))[0]


def resolve_status(
    signals: Iterable[SignalView],
) -> tuple[IncidentStatus, datetime | None]:
    """Estado del incidente y, si corresponde, cuándo se dio por resuelto.

    Sólo una fuente confirmatoria puede cerrar una emergencia. Que dejen de
    llegar detecciones satelitales no significa que el fuego se apagó: significa
    que no pasó un satélite. Esa diferencia la marca `stale`, que decide el
    motor por silencio, no esta función.
    """
    latest: datetime | None = None
    status = IncidentStatus.ACTIVE
    resolved_at: datetime | None = None

    for signal in signals:
        if not rule_for(signal.source).confirming:
            continue
        if latest is not None and signal.timestamp < latest:
            continue

        haystack = normalise_text(
            next(
                (
                    signal.raw_data[alias]
                    for alias in _STATE_ALIASES
                    if signal.raw_data.get(alias) is not None
                ),
                "",
            )
        )
        for marker, candidate in _STATE_MARKERS:
            if marker in haystack:
                latest = signal.timestamp
                status = candidate
                resolved_at = (
                    signal.timestamp
                    if candidate is IncidentStatus.EXTINGUISHED
                    else None
                )
                break

    return status, resolved_at


def build_title(incident_type: IncidentType, commune: str | None) -> str:
    base = _TITLE_BY_TYPE.get(incident_type, "Emergencia")
    return f"{base} — {commune}" if commune else base
