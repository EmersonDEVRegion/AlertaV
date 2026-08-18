"""Paso B — correlación por comuna. Todo puro, sin base de datos.

El problema que resuelve este módulo viene directo de la Fase 2: la capa de
alertas vigentes de SENAPRED es **tabular**. No trae geometría, y en su momento
se decidió no inventarle un centroide porque el correlacionador lo trataría como
una ubicación real. Esa decisión fue correcta, y su precio es este archivo: para
unir una alerta a un incendio hay que hacerlo por el único dato territorial que
la alerta sí tiene, el nombre de la comuna.

Es una heurística sobre texto, y se la trata como tal: el vínculo queda marcado
con `link_method = 'commune_text'` y un `link_confidence` menor que el de una
coincidencia geométrica. Si mañana aparece la capa de polígonos comunales, se
borran sólo estos enlaces y se recalcula.

Dos reglas que evitan falsos positivos caros:

* **Las alertas regionales y nacionales no se adosan a incidentes concretos**
  (configurable). Una alerta temprana preventiva nacional por temporada de
  incendios está vigente todo el verano: pegarla a cada incidente teñiría el
  mapa entero sin decir nada sobre ninguno.
* **La familia del fenómeno tiene que coincidir.** Una alerta roja por crecida
  no se une a un incendio que casualmente ocurre en la misma comuna.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.collectors.geoservices import normalise_text
from app.models.enums import INCIDENT_FAMILY, IncidentType

#: Alias del campo comuna en las capas institucionales.
COMMUNE_ALIASES: tuple[str, ...] = (
    "comuna",
    "Comuna",
    "COMUNA",
    "nom_comuna",
    "comunas",
    "Comunas",
    "COMUNAS",
)

PROVINCE_ALIASES: tuple[str, ...] = (
    "provincia",
    "Provincia",
    "PROVINCIA",
    "nom_provincia",
)

#: Textos que significan "toda la región" o "todo el país".
_WHOLE_AREA_MARKERS: tuple[str, ...] = (
    "toda la region",
    "todas las comunas",
    "todo el pais",
    "region completa",
    "nivel nacional",
    "todo el territorio",
)

#: Separadores con que las capas listan varias comunas en un solo campo.
_SPLIT_PATTERN = re.compile(r"\s*(?:,|;|/|\||\sy\s|\se\s)\s*", flags=re.IGNORECASE)

#: `build_text` de los collectors deja el dato en un formato conocido. Es el
#: último recurso, después de los campos estructurados.
_TEXT_COMMUNE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Comunas?\s*:\s*([^.]+)", flags=re.IGNORECASE),
    re.compile(r"Ubicaci[oó]n\s*:\s*([^.]+)", flags=re.IGNORECASE),
)

#: Marcadores de familia del fenómeno declarado por la alerta.
_FAMILY_MARKERS: tuple[tuple[str, str], ...] = (
    ("incendio", "fire"),
    ("forestal", "fire"),
    ("fuego", "fire"),
    ("humo", "fire"),
    ("inundacion", "hydro"),
    ("crecida", "hydro"),
    ("aluvion", "hydro"),
    ("remocion", "hydro"),
    ("deslizamiento", "hydro"),
    ("lluvia", "hydro"),
    ("marejada", "hydro"),
)

#: Una comuna más corta que esto no se compara por inclusión: "Los" haría match
#: con media región.
_MIN_CONTAINMENT_LENGTH = 5

#: Peso del vínculo según cómo se llegó a él. Siempre por debajo de 1.0, que es
#: lo que vale una coincidencia geométrica.
LINK_CONFIDENCE_EXACT = 0.70
LINK_CONFIDENCE_CONTAINED = 0.55
LINK_CONFIDENCE_REGIONAL = 0.35
#: Descuento cuando la alerta no declara de qué fenómeno se trata.
UNKNOWN_FAMILY_PENALTY = 0.15


def _first_alias(raw_data: Mapping[str, Any], aliases: Sequence[str]) -> str | None:
    lowered = {str(key).lower(): value for key, value in raw_data.items()}
    for alias in aliases:
        value = raw_data.get(alias)
        if value is None:
            value = lowered.get(alias.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def split_communes(value: str | None) -> tuple[str, ...]:
    """Separa un campo que lista varias comunas. Preserva el nombre original."""
    if not value:
        return ()
    parts = (part.strip(" .;") for part in _SPLIT_PATTERN.split(value))
    return tuple(part for part in parts if part)


def is_whole_area(value: str | None) -> bool:
    haystack = normalise_text(value)
    return any(marker in haystack for marker in _WHOLE_AREA_MARKERS)


def extract_commune(
    *, commune: str | None, raw_data: Mapping[str, Any], text: str | None
) -> str | None:
    """Comuna de una señal, en orden de fiabilidad decreciente.

    1. La columna `raw_events.commune`, si algún día se enriquece.
    2. El campo estructurado de la capa de origen (CONAF sí lo trae).
    3. El texto generado por el collector, que tiene formato conocido.

    Devuelve `None` sin adivinar. Un incidente sin comuna simplemente queda
    fuera del alcance del Paso B, y el motor lo cuenta como métrica: es la señal
    de que hace falta la capa de polígonos comunales.
    """
    if commune and commune.strip():
        return commune.strip()

    structured = _first_alias(raw_data, COMMUNE_ALIASES)
    if structured and not is_whole_area(structured):
        candidates = split_communes(structured)
        if candidates:
            return candidates[0]

    if text:
        for pattern in _TEXT_COMMUNE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            candidates = split_communes(match.group(1))
            if candidates and not is_whole_area(candidates[0]):
                return candidates[0]
    return None


def extract_province(
    *, province: str | None, raw_data: Mapping[str, Any]
) -> str | None:
    if province and province.strip():
        return province.strip()
    return _first_alias(raw_data, PROVINCE_ALIASES)


def phenomenon_family(*fragments: Any) -> str:
    """Familia del fenómeno a partir de texto libre: fire | hydro | other."""
    haystack = " ".join(normalise_text(fragment) for fragment in fragments)
    for marker, family in _FAMILY_MARKERS:
        if marker in haystack:
            return family
    return "unknown"


@dataclass(frozen=True, slots=True)
class AlertView:
    """Una alerta vigente sin geometría, lista para el Paso B."""

    event_id: int
    communes: tuple[str, ...]
    is_regional: bool
    level: str
    family: str

    @property
    def normalised(self) -> frozenset[str]:
        return frozenset(normalise_text(name) for name in self.communes)


@dataclass(frozen=True, slots=True)
class IncidentView:
    """Lo que el Paso B necesita saber de un incidente ya construido."""

    incident_id: int
    commune: str | None
    type: IncidentType

    @property
    def family(self) -> str:
        return INCIDENT_FAMILY.get(self.type, "other")


@dataclass(frozen=True, slots=True)
class AlertMatch:
    alert_event_id: int
    incident_id: int
    matched_commune: str
    link_confidence: float
    note: str


def build_alert_view(
    *, event_id: int, raw_data: Mapping[str, Any], text: str | None
) -> AlertView:
    """Construye la vista de una alerta desde su payload original.

    `_alert_level` y `_national` los dejó el collector de SENAPRED en `raw_data`
    durante la ingesta, justamente para no volver a parsear strings en cada
    consulta.
    """
    raw_communes = _first_alias(raw_data, COMMUNE_ALIASES)
    if raw_communes is None and text:
        for pattern in _TEXT_COMMUNE_PATTERNS:
            match = pattern.search(text)
            if match:
                raw_communes = match.group(1)
                break

    regional = bool(raw_data.get("_national")) or is_whole_area(raw_communes)
    ambito = normalise_text(raw_data.get("Ambito") or raw_data.get("ambito"))
    if ambito in ("regional", "nacional"):
        regional = True

    return AlertView(
        event_id=event_id,
        communes=() if regional else split_communes(raw_communes),
        is_regional=regional,
        level=str(raw_data.get("_alert_level") or "otra"),
        family=phenomenon_family(
            raw_data.get("Evento"),
            raw_data.get("Razon"),
            raw_data.get("evento"),
            raw_data.get("razon"),
            text,
        ),
    )


def _commune_match(alert_name: str, incident_commune: str) -> tuple[bool, float]:
    """¿Coincide la comuna de la alerta con la del incidente?

    Se compara sin tildes ni mayúsculas. La inclusión tolera "Valparaíso" contra
    "Valparaíso, Valparaíso", pero exige un largo mínimo para que un fragmento
    corto no haga match con media región.
    """
    left, right = normalise_text(alert_name), normalise_text(incident_commune)
    if not left or not right:
        return (False, 0.0)
    if left == right:
        return (True, LINK_CONFIDENCE_EXACT)
    if len(left) >= _MIN_CONTAINMENT_LENGTH and (left in right or right in left):
        return (True, LINK_CONFIDENCE_CONTAINED)
    return (False, 0.0)


def match_alerts_to_incidents(
    alerts: Iterable[AlertView],
    incidents: Iterable[IncidentView],
    *,
    attach_regional: bool = False,
) -> list[AlertMatch]:
    """Empareja alertas vigentes con incidentes espaciales activos.

    Función pura y determinista: mismos argumentos, mismos vínculos. Es la que
    permite verificar el Paso B con fixtures reales sin levantar PostGIS.

    Una misma alerta puede producir varios `AlertMatch`: una alerta roja comunal
    cubre de verdad todos los incendios activos de esa comuna.
    """
    open_incidents = [item for item in incidents if item.commune]
    matches: list[AlertMatch] = []

    for alert in alerts:
        if alert.is_regional:
            if not attach_regional:
                continue
            for incident in open_incidents:
                if not _families_compatible(alert.family, incident.family):
                    continue
                matches.append(
                    AlertMatch(
                        alert_event_id=alert.event_id,
                        incident_id=incident.incident_id,
                        matched_commune=incident.commune or "",
                        link_confidence=LINK_CONFIDENCE_REGIONAL,
                        note=f"alerta de ámbito regional/nacional (nivel {alert.level})",
                    )
                )
            continue

        for incident in open_incidents:
            if not _families_compatible(alert.family, incident.family):
                continue
            best: tuple[str, float] | None = None
            for name in alert.communes:
                matched, weight = _commune_match(name, incident.commune or "")
                if matched and (best is None or weight > best[1]):
                    best = (name, weight)
            if best is None:
                continue

            weight = best[1]
            note = f"comuna «{best[0]}» (nivel {alert.level})"
            if alert.family == "unknown":
                weight = max(0.0, weight - UNKNOWN_FAMILY_PENALTY)
                note += "; la alerta no declara el fenómeno"

            matches.append(
                AlertMatch(
                    alert_event_id=alert.event_id,
                    incident_id=incident.incident_id,
                    matched_commune=best[0],
                    link_confidence=round(weight, 4),
                    note=note,
                )
            )

    return matches


def _families_compatible(alert_family: str, incident_family: str) -> bool:
    """Una alerta por crecida no se adosa a un incendio de la misma comuna.

    Si la alerta no declara el fenómeno (`unknown`), se admite el vínculo pero
    con un descuento: es mejor un enlace anotado y penalizado que perder la
    única alerta roja del día por un campo mal poblado.
    """
    if alert_family == "unknown":
        return True
    return alert_family == incident_family
