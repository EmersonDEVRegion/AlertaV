"""Contrato de salida de los incidentes consolidados.

Lo que sale por aquí es lo que la PWA pinta en el mapa, así que el schema está
diseñado para que sea **difícil dibujar algo falso**:

* `confidence` viaja siempre acompañada de `confidence_level`, `confidence_label`
  y `is_official_confirmed`. Un cliente que sólo quiera "¿lo pinto en rojo?"
  tiene un enum y un booleano explícitos, y no necesita inventarse un umbral.
  `confidence_level` sale de `models.enums`, no de este módulo: el corte que
  decide el color del mapa y el corte que usa el motor son el mismo número o no
  sirven.
* `alert_level` y `alert_confidence` van separados de `confidence`. Son ejes
  distintos: uno dice cuán seguros estamos de que hay fuego, el otro qué
  declaró SENAPRED. Mezclarlos en un solo número perdería justo la distinción
  que ordena todo el proyecto.
* `confidence_breakdown` va incluido. Cualquiera puede auditar de dónde salió
  el número sin acceso a la base.
* `family` y `level_label` se calculan acá y viajan en la respuesta. Antes el
  cliente tenía que replicar la tabla de familias y descartar nuestra etiqueta
  —que decía "Incendio confirmado" sobre un choque—. Toda decisión de
  presentación que dependa del dominio se toma en el backend: si mañana entra
  una familia nueva, el mapa la pinta sin desplegar el frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.enums import (
    ConfidenceLevel,
    EventSource,
    IncidentStatus,
    IncidentType,
    LinkMethod,
    family_of_incident,
    level_for,
    style_for,
)

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

#: Cortes de la etiqueta legible, **independientes** de `ConfidenceLevel`.
#:
#: Conviven dos escalas a propósito y no hay que fusionarlas sin decidirlo:
#: `confidence_label` es prosa para la tarjeta de detalle ("muy probable") y ya
#: es contrato con la PWA (`ConfidenceLabel` en `api/types.ts`);
#: `confidence_level` es el tramo operativo de tres estados que decide el color.
#: Cambiar los strings de acá rompe el tipo del frontend; agregar el enum, no.
CONFIDENCE_LABELS: tuple[tuple[float, str], ...] = (
    (0.95, "confirmado"),
    (0.75, "muy probable"),
    (0.50, "probable"),
    (0.0, "sin confirmar"),
)


def confidence_label(value: float, *, confirmed: bool = False) -> str:
    if confirmed:
        return "confirmado"
    for threshold, label in CONFIDENCE_LABELS:
        if value >= threshold:
            return label
    return "sin confirmar"


class OutageDetail(BaseModel):
    """Metadatos de un corte de suministro.

    Viven en `raw_events.raw_data` porque sólo aplican a una familia, y dejar
    tres columnas nulas en cada incendio de CONAF para siempre sería el
    intercambio equivocado. Pero el mapa los necesita **al abrir la ficha**, y
    obligar al cliente a pedir el detalle, sacar los `raw_event_id` y volver a
    consultar `/events` sería hacerle reensamblar algo que acá está contiguo.

    `provider` sale de la fuente de la señal (`chilquinta` | `cge`), no de un
    campo de texto libre: es la misma enumeración que ya viaja en `sources`.
    """

    model_config = ConfigDict(from_attributes=True)

    provider: str = Field(..., description="chilquinta | cge")
    affected_clients: int | None = Field(
        default=None,
        description=(
            "Suma de clientes afectados de todas las señales del incidente. "
            "`null` si ninguna lo informó: los feeds a veces publican el corte "
            "antes de contar los clientes, y un 0 afirmaría que no hay ninguno."
        ),
    )
    estimated_restoration: datetime | None = Field(
        default=None,
        description=(
            "Reposición estimada más TARDÍA entre las señales del incidente. "
            "Un corte con varios frentes queda resuelto cuando vuelve el último."
        ),
    )
    sector: str | None = None
    outage_count: int = Field(
        default=1, description="Cuántos cortes distintos componen el incidente."
    )


class IncidentRead(BaseModel):
    """Un incidente consolidado, listo para el mapa."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="Folio legible, p. ej. INC-2026-00142.")
    public_id: UUID
    type: IncidentType
    status: IncidentStatus

    lat: float
    lon: float

    confidence: Confidence = Field(
        ..., description="Confianza en que el FENÓMENO es real."
    )
    is_official_confirmed: bool = Field(
        ...,
        description=(
            "¿Una fuente que fue al lugar (CONAF, Bomberos) confirmó el hecho? "
            "Es el único booleano que autoriza a pintarlo como confirmado."
        ),
    )
    alert_confidence: Confidence = Field(
        ...,
        description=(
            "Confianza en el ESTADO DE ALERTA. 1.0 si hay una alerta vigente de "
            "SENAPRED adosada. Eje distinto de `confidence`."
        ),
    )
    alert_level: str | None = Field(
        default=None, description="roja | amarilla | temprana_preventiva | verde"
    )

    title: str | None = None
    commune: str | None = None
    province: str | None = None

    event_count: int
    source_count: int
    sources: list[str]

    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    correlated_at: datetime

    confidence_breakdown: dict[str, Any] = Field(default_factory=dict)

    outage: OutageDetail | None = Field(
        default=None,
        description=(
            "Sólo en incidentes de tipo `power_outage`. `null` en cualquier "
            "otra familia; un cliente no debería tener que preguntar el tipo "
            "para saber si mirar acá."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Tramo operativo: `unsafe` (<30 %), `possible` (30–60 %), `confirmed` (>60 %).

        Se deriva de `confidence` y NO de `is_official_confirmed`: son las dos
        preguntas distintas que el sistema responde. Un incidente puede salir
        `confirmed` por acumulación de despachos radiales con
        `is_official_confirmed = False`, y el cliente tiene que poder distinguirlo.
        """
        return level_for(self.confidence)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def family(self) -> str:
        """Familia de fenómeno: `fire`, `traffic`, `hydro` u `other`.

        Decide con qué paleta se pinta el incidente y qué casilla del panel lo
        enciende. Se expone porque el corte ya existía en el backend —es el que
        impide que una alerta por crecida se adose a un incendio— y tenerlo
        duplicado en el cliente significaba que agregar un `IncidentType` exigía
        desplegar las dos mitades del sistema, en orden, o el mapa dejaba de
        pintar algo sin decir nada.
        """
        return family_of_incident(self.type)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def level_label(self) -> str:
        """Etiqueta del tramo de confianza, con el sustantivo de su familia.

        "Incendio confirmado" para un incendio, "Accidente confirmado" para un
        choque. Es el par legible de `confidence_level`, que es el enum
        operativo.

        No confundir con `confidence_label`, que convive con éste a propósito:
        aquél es el adverbio de certeza ("muy probable", "sin confirmar") y no
        depende del fenómeno; éste nombra el fenómeno y su nivel.
        """
        return style_for(self.confidence_level, self.family).label

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_label(self) -> str:
        return confidence_label(
            self.confidence, confirmed=self.is_official_confirmed
        )

    @model_validator(mode="after")
    def _reconcile_breakdown_label(self) -> IncidentRead:
        """Corrige la etiqueta guardada en el breakdown de incidentes viejos.

        `confidence_breakdown` es una columna: los incidentes correlacionados
        antes de este cambio llevan grabado `level_label: "Incendio confirmado"`
        aunque sean accidentes. Sin esto, la misma respuesta se contradiría —un
        `level_label` correcto arriba y el viejo dentro del breakdown—, que es
        peor que el problema original: el cliente no sabría a cuál creerle.

        Se reescribe **sólo la presentación**. Los pesos, los techos, los aportes
        por fuente y la versión de la política quedan intactos: son la auditoría
        de cómo se calculó el número y falsificarlos sería mentir sobre el
        pasado. La etiqueta no se calculó, se eligió, y elegirla de nuevo al
        leer es legítimo.

        Se construye un dict nuevo en vez de mutar el existente porque el que
        llega es el del objeto ORM, y mutarlo marcaría la fila como sucia:
        serializar un incidente dispararía un UPDATE.
        """
        breakdown = self.confidence_breakdown
        if not breakdown:
            return self

        family = self.family
        esperado = style_for(self.confidence_level, family).label
        if breakdown.get("level_label") == esperado and breakdown.get("family") == family:
            return self

        self.confidence_breakdown = {
            **breakdown,
            "family": family,
            "level_label": esperado,
        }
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_multi_source(self) -> bool:
        """¿Lo sostiene más de una fuente independiente?

        Es la pregunta que justifica que exista este sistema, así que se
        responde explícitamente en vez de hacer que el cliente cuente el array.
        """
        return self.source_count > 1


class IncidentEventLink(BaseModel):
    """Una señal y el motivo por el que quedó unida al incidente."""

    model_config = ConfigDict(from_attributes=True)

    raw_event_id: int
    public_id: UUID | None = None
    source: EventSource
    type: str
    timestamp: datetime
    confidence: float
    text: str | None = None
    lat: float | None = None
    lon: float | None = None

    link_method: LinkMethod
    link_confidence: float
    distance_m: float | None = None
    matched_commune: str | None = None
    note: str | None = None


class IncidentDetail(IncidentRead):
    """Incidente con todas sus señales y la trazabilidad de cada vínculo."""

    events: list[IncidentEventLink] = Field(default_factory=list)


class IncidentStats(BaseModel):
    total: int
    confirmed: int
    with_official_alert: int
    avg_confidence: float | None
    last_seen_at: datetime | None
    by_status: dict[str, int]
    by_type: dict[str, int]


class CorrelationRunResult(BaseModel):
    """Traza de una pasada del motor."""

    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float
    events_considered: int
    clusters: int
    clusters_deferred: int
    incidents_created: int
    incidents_updated: int
    spatial_links: int
    alerts_considered: int
    alert_links: int
    incidents_merged: int
    incidents_stale: int
    #: Descartados por no conseguir corroboración dentro de su TTL corto.
    incidents_dismissed: int = 0
    incidents_without_commune: int
    warnings: list[str] = Field(default_factory=list)
