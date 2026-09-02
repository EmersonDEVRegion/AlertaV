"""Confidence Engine — cuánto vale un incidente dadas sus señales.

Todo este módulo es puro: entra una lista de `SignalView`, sale un número con su
derivación. Sin sesión, sin red, sin reloj. Es la parte del sistema que hay que
poder recalibrar con fixtures reales en segundos.

Las reglas
----------

1. **CONAF y Bomberos confirman.** Son los organismos que van al fuego; su
   registro *es* la confirmación del hecho. Una sola señal suya lleva el
   incidente a 1.0.

2. **Los despachos oficiales pesan 0.80.** Un despacho por radio —Broadcastify,
   la central municipal— es una *persona con autoridad* diciendo que mandó
   carros a un punto. No es todavía la constatación en terreno que hace CONAF,
   pero está mucho más cerca de eso que de un indicio: un solo despacho basta
   para cruzar a `confirmed`.

3. **SENAPRED declara la respuesta, no el fenómeno.** Su alerta es cierta al
   100 % —el acto administrativo existe, lo firmó el organismo— y eso se refleja
   en `alert_confidence = 1.0` y en `alert_level`. Sobre el fenómeno aporta
   corroboración muy fuerte, pero **no saturante**, por dos razones concretas:
   la alerta no observa el incendio (declara la respuesta del Estado a él) y su
   vínculo con este incidente en particular se estableció por coincidencia de
   comuna, que es una heurística sobre texto, no una medición geométrica.
   Confundir ambos ejes sería declarar "aquí hay fuego, 100 % seguro" en un
   punto que ninguna fuente observó.

4. **FIRMS vale 0.40 y su techo está bajo 0.60.** Es la recalibración central de
   esta política. NASA FIRMS detecta *anomalías térmicas*, y en la V Región eso
   incluye chimeneas de Ventanas, quemas agrícolas autorizadas y hornos de
   ladrillo. Un píxel satelital no confirma nada: entra en 0.40 —mitad de la
   banda "posible emergencia"— y ninguna cantidad de píxeles lo saca de ahí,
   porque `ceiling = 0.55 < 0.60`. Sólo el cruce con otra fuente lo escala.

5. **Los reportes ciudadanos parten en la banda 0.25–0.40.** Un reporte suelto
   queda por debajo de 0.30, o sea en `unsafe`: se registra, pero el mapa no
   afirma nada con él. Suben de forma progresiva con cada reporte cercano
   adicional, con rendimientos decrecientes y un techo por debajo de la
   confirmación institucional.

Cómo se combinan
----------------

Dentro de una misma fuente las señales son **parcialmente redundantes**: cuatro
píxeles de la misma pasada de VIIRS son casi una sola observación, y tres
vecinos del mismo cerro miran el mismo humo. Por eso el aporte de la señal
`k`-ésima de una fuente se descuenta por `decay^k` y el total de la fuente se
recorta en su `ceiling`.

Entre fuentes distintas hay independencia —ahí está todo el valor de
correlacionar— y desde la v2.0.0 los aportes se **suman**, saturando en 1.0:
``min(Σ wᵢ, 1.0)``. Es una lectura deliberadamente directa: si el satélite pone
40 % y un vecino pone 25 %, el incidente vale 65 % y el operador puede rehacer
esa cuenta de cabeza mirando `by_source` en el breakdown. La versión anterior
usaba *noisy-OR* (``1 - Π(1 - wᵢ)``), que daba 55 % para ese mismo caso: más
conservador, pero imposible de explicar en una sala de operaciones.

El riesgo conocido de sumar es que satura rápido. Se contiene en tres puntos, no
en la fórmula: los pesos base son bajos, cada fuente tiene `ceiling` propio, y
`UNCONFIRMED_CEILING` impide que nada llegue a la certeza sin una fuente que
haya ido al lugar.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.collectors.geoservices import normalise_text
from app.models.enums import (
    CONFIRMED_THRESHOLD,
    DEFAULT_FAMILY,
    EVENT_TO_INCIDENT_TYPE,
    LEVEL_STYLES,
    UNSAFE_THRESHOLD,
    ConfidenceLevel,
    EventSource,
    EventType,
    IncidentStatus,
    IncidentType,
    level_for,
    style_for,
)

#: Los tramos y sus cortes viven en `app.models.enums` —ver el docstring de ese
#: módulo: `app.schemas` también los necesita y no puede depender de services sin
#: cerrar un ciclo—. Se re-exportan acá porque conceptualmente son parte de esta
#: política: quien viene a leer cómo se calibra el motor los espera en este
#: archivo, no en el de las enumeraciones.
__all__ = [
    "CONFIRMED_THRESHOLD",
    "LEVEL_STYLES",
    "OFFICIAL_DISPATCH_WEIGHT",
    "POLICY_VERSION",
    "RULES",
    "UNCONFIRMED_CEILING",
    "UNSAFE_THRESHOLD",
    "ConfidenceLevel",
    "ConfidenceResult",
    "SignalView",
    "SourceRule",
    "build_title",
    "level_for",
    "resolve_status",
    "resolve_type",
    "rule_for",
    "score",
    "signal_weight",
    "style_for",
]

#: Se versiona porque queda escrito en `incidents.confidence_breakdown`. Cuando
#: la calibración cambie los pesos, los incidentes viejos siguen diciendo con qué
#: reglas se calcularon.
#:
#: 2.0.0 — recalibración con datos geoespaciales reales: FIRMS baja a 0.40 con
#: techo bajo 0.60, el ciudadano baja a la banda 0.25–0.40, los despachos
#: oficiales suben a 0.80, y la combinación entre fuentes pasa de noisy-OR a
#: suma saturada. Es un cambio de mayor porque los números dejan de ser
#: comparables con los de la v1: un incidente reprocesado puede cambiar de tramo.
POLICY_VERSION = "2.0.0"

#: Ninguna combinación de señales no confirmatorias alcanza la certeza. Sólo un
#: organismo que fue al lugar puede cerrar ese último tramo.
UNCONFIRMED_CEILING = 0.95

#: Peso de un despacho oficial por radio. Es una confirmación *humana* con
#: autoridad —alguien mandó carros— aunque todavía no sea constatación en
#: terreno. Ver la regla 2 del docstring.
OFFICIAL_DISPATCH_WEIGHT = 0.80

#: Tipos de señal que constituyen un despacho.
_DISPATCH_TYPES: frozenset[EventType] = frozenset(
    {EventType.DISPATCH, EventType.RESCUE}
)


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
    #: ¿Sus despachos son oficiales? Un `dispatch` de esta fuente pesa al menos
    #: `OFFICIAL_DISPATCH_WEIGHT`, por encima de su banda habitual.
    official_dispatch: bool = False


RULES: dict[EventSource, SourceRule] = {
    # -- Confirman el hecho --------------------------------------------------
    EventSource.CONAF: SourceRule(1.0, 1.0, 1.0, 1.0, confirming=True),
    EventSource.BOMBEROS: SourceRule(
        1.0, 1.0, 1.0, 1.0, confirming=True, official_dispatch=True
    ),
    # -- Autoridad sobre su propia infraestructura ---------------------------
    # Una distribuidora eléctrica reportando un corte en SU red es el caso más
    # limpio de `confirming` que hay en todo el catálogo, y por un motivo
    # distinto al de CONAF: CONAF confirma porque fue al lugar a mirar; la
    # distribuidora confirma porque el corte lo registran sus propios equipos.
    # No hay observación indirecta que pueda equivocarse.
    #
    # Ojo con el alcance: son autoridad sobre el CORTE, no sobre su causa. Que
    # Chilquinta reporte 800 clientes sin luz no dice nada sobre si hubo un
    # incendio — y como `power` es familia propia, el motor tampoco puede
    # inferirlo.
    EventSource.CHILQUINTA: SourceRule(1.0, 1.0, 1.0, 1.0, confirming=True),
    EventSource.CGE: SourceRule(1.0, 1.0, 1.0, 1.0, confirming=True),
    # -- Declara la respuesta del Estado -------------------------------------
    # Techo 0.85 sobre el fenómeno; 1.0 sobre el estado de alerta, que va aparte.
    EventSource.SENAPRED: SourceRule(0.85, 0.85, 0.5, 0.85, declares_alert=True),
    # -- Despachos oficiales: 0.80 -------------------------------------------
    # Un despacho basta para cruzar a `CONFIRMED` (0.80 > 0.60). Es intencional:
    # cuando la central manda carros, ya hay una decisión humana con autoridad
    # detrás del punto en el mapa.
    EventSource.BROADCASTIFY: SourceRule(
        0.60, 0.80, 0.4, 0.90, official_dispatch=True
    ),
    EventSource.MUNICIPALITY: SourceRule(
        0.60, 0.80, 0.5, 0.90, official_dispatch=True
    ),
    # -- Corroboran ----------------------------------------------------------
    EventSource.MEDIA: SourceRule(0.40, 0.60, 0.5, 0.75),
    # FIRMS: 0.40 fijo, techo 0.55. La banda se colapsa a propósito —la
    # "confianza" que trae un píxel VIIRS mide la certeza del ALGORITMO sobre la
    # anomalía térmica, no la probabilidad de que esa anomalía sea un incendio,
    # que es lo único que este número debería decir. Un píxel de alta calidad
    # sobre la fundición de Ventanas es una lectura excelente de una chimenea.
    # El techo bajo 0.60 es la regla dura: ningún racimo puramente satelital,
    # por grande que sea, se rotula "incendio confirmado".
    EventSource.NASA_FIRMS: SourceRule(0.40, 0.40, 0.35, 0.55),
    EventSource.CAMERA: SourceRule(0.35, 0.55, 0.5, 0.70),
    # -- Capa de accidentes viales -------------------------------------------
    # Transporte Informa (MTT): 0.80. Un organismo del Estado publicando por su
    # canal oficial, así que un solo aviso cruza a `confirmed` (0.80 > 0.60). No
    # llega a 1.0 porque no constata en terreno: informa lo que le reportaron.
    # Techo 0.90 para que la certeza siga requiriendo a alguien que fue al lugar.
    #
    # Sin `official_dispatch`: ese flag existe para quien MANDA carros, y el MTT
    # no despacha nada. El 0.80 sale de la banda, no de una excepción.
    #
    # Hay una segunda razón, específica de esta fuente: su coordenada NO viene
    # informada, la reconstruye este backend geocodificando texto libre. El peso
    # 0.80 califica la credibilidad del HECHO ("hay un accidente en la Ruta 68"),
    # no la del PUNTO. El error de la geocodificación queda en `raw_data` para
    # que sea auditable, y por eso su decay es alto: dos avisos del MTT sobre el
    # mismo tramo son el mismo hecho contado dos veces.
    EventSource.TRANSPORTE_INFORMA: SourceRule(0.60, 0.80, 0.5, 0.90),
    # Waze: 0.40 fijo, techo 0.65. Misma lógica que FIRMS y por el mismo motivo:
    # la banda se colapsa porque la "confiabilidad" que trae un reporte de Waze
    # mide cuántos conductores confirmaron el ícono, no si hubo un accidente.
    # Un atasco por obras acumula confirmaciones igual que un choque.
    #
    # El techo 0.65 sí supera 0.60 —a diferencia de FIRMS— porque un racimo de
    # reportes independientes de Waze en el mismo punto es evidencia real de que
    # ALGO interrumpe el tránsito ahí. Lo que no puede es alcanzar la certeza:
    # para eso hace falta Bomberos o el MTT.
    EventSource.WAZE: SourceRule(0.40, 0.40, 0.40, 0.65),
    # Ciudadanos: banda 0.25–0.40. Uno solo queda en `unsafe` (<0.30); con foto o
    # verificación llega a 0.40 y entra a `possible`. Progresivo, con techo por
    # debajo de la confirmación institucional.
    EventSource.CITIZEN: SourceRule(0.25, 0.40, 0.45, 0.75),
    EventSource.SOCIAL_MEDIA: SourceRule(0.20, 0.35, 0.5, 0.55),
    EventSource.OTHER: SourceRule(0.15, 0.30, 0.5, 0.45),
    # -- Contexto, no evidencia ----------------------------------------------
    # Que haya viento y 34 °C no es prueba de que algo se esté quemando.
    EventSource.WEATHER: SourceRule(0.0, 0.0, 0.0, 0.0),
    # USGS mide un sismo con instrumentos: el hecho es cierto. Pero eso no
    # corrobora nada sobre el incidente al que se acercara geométricamente, y un
    # epicentro no es la ubicación de un siniestro. Hoy `earthquake` ni siquiera
    # está en CORRELATABLE_EVENT_TYPES, así que esta regla no se ejecuta nunca:
    # está escrita para que, si alguien decide correlacionar sismos más adelante,
    # el peso por defecto (0.15–0.30) no le regale corroboración inventada.
    EventSource.USGS: SourceRule(0.0, 0.0, 0.0, 0.0),
    # El CSN mide mejor que el USGS en Chile, y eso no cambia nada acá: la
    # calidad de la medición no convierte un epicentro en un siniestro.
    EventSource.CSN: SourceRule(0.0, 0.0, 0.0, 0.0),
    # Vialidad es la autoridad sobre el estado de sus rutas y aun así vale 0.
    # Igual que en USGS, la regla está escrita para un futuro que hoy no ocurre:
    # `road_closure` no está en CORRELATABLE_EVENT_TYPES, así que esto no se
    # ejecuta nunca. Existe para que, si alguien decide más adelante que el MOP
    # emita algo correlacionable, el peso por defecto (0.15–0.30) no le regale
    # corroboración: una socavación de hace tres semanas no confirma el choque
    # que alguien está reportando hoy en esa misma cuesta.
    EventSource.MOP: SourceRule(0.0, 0.0, 0.0, 0.0),
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
    IncidentType.POWER_OUTAGE: "Corte de suministro",
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
    level: ConfidenceLevel
    is_official_confirmed: bool
    alert_confidence: float
    alert_level: str | None
    sources: tuple[EventSource, ...]
    breakdown: dict[str, Any]


def rule_for(source: EventSource) -> SourceRule:
    return RULES.get(source, DEFAULT_RULE)


def signal_weight(rule: SourceRule, signal: SignalView) -> float:
    """Peso de UNA señal, antes de descontar redundancia.

    La confianza que trae la señal desde la ingesta modula dentro de la banda de
    su fuente; nunca la saca de ella. Un despacho oficial es la única excepción:
    entra por `OFFICIAL_DISPATCH_WEIGHT` aunque el colector le haya puesto menos,
    porque lo que pesa ahí no es la calidad del audio sino quién lo emitió.
    """
    if rule.max_weight <= 0.0:
        return 0.0
    bounded = min(max(float(signal.confidence), rule.min_weight), rule.max_weight)
    if rule.official_dispatch and signal.type in _DISPATCH_TYPES:
        return max(bounded, min(OFFICIAL_DISPATCH_WEIGHT, rule.ceiling))
    return bounded


def _source_contribution(rule: SourceRule, weights: Sequence[float]) -> float:
    """Aporte agregado de una fuente, con redundancia intra-fuente descontada.

    Suma `w_k * decay^k` sobre las señales ordenadas de mayor a menor peso: la
    más creíble pesa entera y las siguientes son, en el mejor de los casos, la
    misma observación repetida. El total se recorta en el `ceiling` de la fuente,
    que es lo que impide que veinte píxeles de una misma pasada de VIIRS
    acumulen la certeza que no tiene ninguno de ellos.
    """
    if not weights or rule.max_weight <= 0.0:
        return 0.0

    total = sum(
        weight * (rule.redundancy_decay**rank)
        for rank, weight in enumerate(sorted(weights, reverse=True))
    )
    return min(total, rule.ceiling)


def score(
    signals: Iterable[SignalView], *, family: str = DEFAULT_FAMILY
) -> ConfidenceResult:
    """Confianza del incidente y su derivación completa.

    `family` **no participa del cálculo**: la aritmética de la confianza es la
    misma para un incendio que para un choque, y mezclar el tipo de fenómeno con
    el peso de la evidencia sería empezar a decidir que ciertos siniestros
    merecen más credulidad que otros. Entra sólo para rotular el resultado —el
    `level_label` del breakdown— con el sustantivo correcto.

    Por defecto la familia es genérica. Un llamador que la omita obtiene
    "Emergencia confirmada": impreciso, pero nunca falso.
    """
    grouped: dict[EventSource, list[float]] = defaultdict(list)
    alert_level: str | None = None
    alert_severity = -1

    for signal in signals:
        rule = rule_for(signal.source)
        grouped[signal.source].append(signal_weight(rule, signal))

        if rule.declares_alert and signal.type in (
            EventType.ALERT,
            EventType.EVACUATION,
        ):
            level = str(signal.raw_data.get("_alert_level") or "otra")
            severity = _ALERT_SEVERITY.get(level, 0)
            if severity > alert_severity:
                alert_severity, alert_level = severity, level

    by_source: dict[str, Any] = {}
    confirmed = False
    combined = 0.0

    for source, weights in grouped.items():
        rule = rule_for(source)
        contribution = _source_contribution(rule, weights)
        confirmed = confirmed or (rule.confirming and bool(weights))
        # Suma entre fuentes. Ver "Cómo se combinan" en el docstring del módulo:
        # el operador tiene que poder rehacer esta cuenta leyendo `by_source`.
        combined += contribution
        by_source[source.value] = {
            "signals": len(weights),
            "contribution": round(contribution, 4),
            "ceiling": rule.ceiling,
            "confirming": rule.confirming,
        }

    combined = min(combined, 1.0)

    if confirmed:
        confidence, applied = 1.0, "confirming_source"
    elif combined > UNCONFIRMED_CEILING:
        confidence, applied = UNCONFIRMED_CEILING, "unconfirmed_ceiling"
    else:
        # El techo por fuente aislada ya quedó aplicado dentro de
        # `_source_contribution`: una fuente sola nunca supera su `ceiling`.
        confidence, applied = combined, None

    confidence = round(confidence, 4)
    alert_confidence = 1.0 if alert_level is not None else 0.0
    level = level_for(confidence)

    return ConfidenceResult(
        confidence=confidence,
        level=level,
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
            "combination": "additive_capped",
            "level": level.value,
            "family": family,
            "level_label": style_for(level, family).label,
            "thresholds": {
                "unsafe_below": UNSAFE_THRESHOLD,
                "confirmed_above": CONFIRMED_THRESHOLD,
            },
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
