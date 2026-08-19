"""CorrelationEngine — el único componente del motor que toca la base.

Una pasada hace cuatro cosas, en este orden y por esta razón:

1. **Paso A — geometría.** Agrupa señales georreferenciadas y las convierte en
   incidentes. Es el paso que *crea* incidentes; ningún otro lo hace. Agrupa
   **dentro de cada familia de fenómeno**, nunca entre familias: ver más abajo.
2. **Fusión.** Dos racimos que crecieron uno hacia el otro son el mismo
   incendio. Se resuelve antes del Paso B para que una alerta no se adose a un
   incidente que está a punto de desaparecer absorbido.
3. **Paso B — texto.** Adosa las alertas vigentes de SENAPRED, que no tienen
   coordenadas, a los incidentes espaciales de su comuna.
4. **Caducidad.** Dos reglas distintas y con criterios distintos: los
   incidentes sin señales nuevas pasan a `stale` tras horas, y los sostenidos
   sólo por un reporte ciudadano sin corroborar se descartan tras minutos. Ver
   `_expire`.

El Paso B se **reconstruye entero** en cada pasada: sus enlaces se borran y se
recalculan. Una alerta levantada tiene que dejar de teñir el mapa, y
reconstruir es más simple de auditar que caducar enlace por enlace. Los vínculos
espaciales, en cambio, son historia: no se tocan nunca.

Aislamiento entre familias de fenómeno
--------------------------------------

Un incendio y un accidente vial pueden ocurrir en la misma esquina en el mismo
minuto sin tener nada que ver, y de hecho es lo esperable en una ciudad. El
motor sólo mide distancias y tiempos, así que sin una barrera explícita los
fundiría en un incidente que no existe. La barrera tiene **tres puertas**, y
hacen falta las tres porque cada una tapa un camino distinto hacia la misma
fusión:

1. `cluster_unassigned_events` particiona el DBSCAN por familia — señales nuevas
   entre sí.
2. `find_nearest_open_incident` filtra por familia — señal nueva contra
   incidente que ya existe.
3. `find_mergeable` exige familia común — dos incidentes que crecieron uno hacia
   el otro.

Dejar una sola abierta anula a las otras dos: bastaría con que el choque se
adhiriera al incendio ya existente para que todo el trabajo de particionar el
Paso A no sirviera de nada.

Lo que NO separa: los grados de certeza sobre un mismo fenómeno. `smoke`,
`thermal_anomaly` y `wildfire` caen todos en la familia `fire` y se corroboran
entre sí. Eso es el sistema funcionando, no una fuga.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import (
    EVENT_TO_INCIDENT_TYPE,
    EventSource,
    IncidentStatus,
    IncidentType,
    LinkMethod,
    family_of_incident,
)
from app.models.event import RawEvent
from app.models.incident import Incident
from app.repositories.incident_repository import (
    ClusteredEvent,
    EventLink,
    IncidentRepository,
)
from app.services.correlation.communes import (
    AlertView,
    IncidentView,
    build_alert_view,
    extract_commune,
    extract_province,
    match_alerts_to_incidents,
)
from app.services.correlation.confidence import (
    SignalView,
    build_title,
    resolve_status,
    resolve_type,
    rule_for,
    score,
)

logger = logging.getLogger(__name__)

#: Estados sobre los que el motor puede escribir. `merged` y `dismissed` son
#: decisiones ya tomadas —por el propio motor o por un operador— y recalcularlas
#: en cada pasada las desharía.
_MUTABLE_STATUSES = frozenset(
    {IncidentStatus.ACTIVE, IncidentStatus.CONTROLLED, IncidentStatus.STALE}
)

_EARTH_RADIUS_M = 6_371_008.8


@dataclass(slots=True)
class CorrelationPass:
    """Traza de una pasada. Lo que el operador necesita para confiar o dudar."""

    started_at: datetime
    finished_at: datetime | None = None
    events_considered: int = 0
    clusters: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    spatial_links: int = 0
    clusters_deferred: int = 0
    alerts_considered: int = 0
    alert_links: int = 0
    incidents_merged: int = 0
    incidents_stale: int = 0
    #: Descartados por no conseguir corroboración a tiempo. Es la métrica que
    #: dirá si el TTL de 5 minutos está bien calibrado o mata reportes válidos.
    incidents_dismissed: int = 0
    #: Incidentes que el Paso B no pudo alcanzar por no tener comuna. Es la
    #: métrica que dirá cuándo hace falta la capa de polígonos comunales.
    incidents_without_commune: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "events_considered": self.events_considered,
            "clusters": self.clusters,
            "clusters_deferred": self.clusters_deferred,
            "incidents_created": self.incidents_created,
            "incidents_updated": self.incidents_updated,
            "spatial_links": self.spatial_links,
            "alerts_considered": self.alerts_considered,
            "alert_links": self.alert_links,
            "incidents_merged": self.incidents_merged,
            "incidents_stale": self.incidents_stale,
            "incidents_dismissed": self.incidents_dismissed,
            "incidents_without_commune": self.incidents_without_commune,
            "warnings": list(self.warnings),
        }


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos puntos WGS84.

    Se calcula en Python, no en PostGIS, a propósito: el motor ya tiene ambas
    coordenadas en memoria y pedirle a la base una distancia por señal
    convertiría una pasada en cientos de consultas triviales.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def weighted_centroid(events: Sequence[ClusteredEvent]) -> tuple[float, float]:
    """Centro del racimo ponderado por la confianza de cada señal."""
    total = sum(max(event.confidence, 0.01) for event in events)
    lat = sum(event.lat * max(event.confidence, 0.01) for event in events) / total
    lon = sum(event.lon * max(event.confidence, 0.01) for event in events) / total
    return (lat, lon)


class CorrelationEngine:
    """Fusiona señales independientes en incidentes consolidados."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        radius_m: float | None = None,
        window_hours: int | None = None,
        match_window_hours: int | None = None,
        stale_hours: int | None = None,
        citizen_ttl_minutes: int | None = None,
        citizen_max_confidence: float | None = None,
        alert_validity_hours: int | None = None,
        min_signals: int | None = None,
        attach_regional_alerts: bool | None = None,
        max_events: int | None = None,
    ) -> None:
        self.session = session
        self.repo = IncidentRepository(session)
        self.radius_m = radius_m or settings.CORRELATION_RADIUS_M
        self.window_hours = window_hours or settings.CORRELATION_WINDOW_HOURS
        self.match_window_hours = (
            match_window_hours or settings.CORRELATION_MATCH_WINDOW_HOURS
        )
        self.stale_hours = stale_hours or settings.CORRELATION_STALE_HOURS
        self.citizen_ttl_minutes = (
            citizen_ttl_minutes or settings.CITIZEN_UNCORROBORATED_TTL_MINUTES
        )
        self.citizen_max_confidence = (
            settings.CITIZEN_UNCORROBORATED_MAX_CONFIDENCE
            if citizen_max_confidence is None
            else citizen_max_confidence
        )
        self.alert_validity_hours = (
            alert_validity_hours or settings.CORRELATION_ALERT_VALIDITY_HOURS
        )
        self.min_signals = min_signals or settings.CORRELATION_MIN_SIGNALS_FOR_INCIDENT
        self.attach_regional_alerts = (
            settings.CORRELATION_ATTACH_REGIONAL_ALERTS
            if attach_regional_alerts is None
            else attach_regional_alerts
        )
        self.max_events = max_events or settings.CORRELATION_MAX_EVENTS_PER_PASS

    # -- Orquestación ---------------------------------------------------------

    async def run(self) -> CorrelationPass:
        """Una pasada completa. Commit único al final.

        La pasada es atómica a propósito: un fallo a mitad del Paso B no puede
        dejar el mapa con incidentes creados pero sin sus alertas, ni con los
        enlaces del Paso B borrados y no reconstruidos.
        """
        now = datetime.now(UTC)
        result = CorrelationPass(started_at=now)

        if not await self.repo.try_advisory_lock():
            # Dos pasadas concurrentes leen `incident_id IS NULL` antes de que la
            # otra escriba, y crean dos incidentes para el mismo incendio. Con
            # una réplica esto nunca ocurre; con dos, ocurre el primer día.
            result.warnings.append("otra pasada en curso; ésta se omitió")
            result.finished_at = datetime.now(UTC)
            await self.session.rollback()
            logger.info("pasada omitida: el motor ya estaba corriendo")
            return result

        try:
            await self._step_a_spatial(result, now=now)
            await self._merge_converged(result, now=now)
            await self._step_b_commune(result, now=now)
            await self._expire(result, now=now)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception("la pasada de correlación falló; se revirtió entera")
            raise

        result.finished_at = datetime.now(UTC)
        logger.info("pasada de correlación", extra=result.as_dict())
        return result

    # -- Paso A ---------------------------------------------------------------

    async def _step_a_spatial(self, result: CorrelationPass, *, now: datetime) -> None:
        """Agrupa señales georreferenciadas y las vuelca en incidentes."""
        since = now - timedelta(hours=self.window_hours)
        match_since = now - timedelta(hours=self.match_window_hours)

        clustered = await self.repo.cluster_unassigned_events(
            since=since, radius_m=self.radius_m, limit=self.max_events
        )
        result.events_considered = len(clustered)
        if not clustered:
            return

        # La clave incluye la familia. `ST_ClusterDBSCAN` numera desde 0 en cada
        # partición, así que agrupar sólo por `cluster_id` volvería a mezclar
        # exactamente lo que el SQL acaba de separar — un incendio y un choque
        # compartirían el racimo 0 y terminarían en el mismo incidente.
        clusters: dict[tuple[str, int | None], list[ClusteredEvent]] = defaultdict(list)
        for event in clustered:
            clusters[event.cluster_key].append(event)
        result.clusters = len(clusters)

        for (family, _), members in clusters.items():
            lat, lon = weighted_centroid(members)

            nearby = await self.repo.find_nearest_open_incident(
                lat=lat,
                lon=lon,
                radius_m=self.radius_m,
                since=match_since,
                family=family,
            )

            if nearby is not None:
                incident = nearby.incident
            else:
                if not self._should_open_incident(members):
                    # Señal aislada de una fuente no confirmatoria: se deja sin
                    # incidente. No se pierde —sigue siendo una `raw_event`
                    # consultable— y la próxima pasada volverá a evaluarla junto
                    # a la corroboración que pueda haber llegado entretanto.
                    result.clusters_deferred += 1
                    continue
                incident = await self._open_incident(members, lat=lat, lon=lon)
                result.incidents_created += 1

            links = [
                EventLink(
                    raw_event_id=member.event_id,
                    link_method=LinkMethod.SPATIAL,
                    link_confidence=1.0,
                    distance_m=round(
                        haversine_m(incident.lat, incident.lon, member.lat, member.lon),
                        2,
                    ),
                )
                for member in members
            ]
            result.spatial_links += await self.repo.link_events(
                incident_id=incident.id, links=links
            )
            await self.repo.assign_events_to_incident(
                incident_id=incident.id,
                event_ids=[member.event_id for member in members],
                processed_at=now,
            )
            await self._refresh(incident, now=now)
            result.incidents_updated += 1

    def _should_open_incident(self, members: Sequence[ClusteredEvent]) -> bool:
        """¿Este racimo merece un incidente propio?

        Una fuente confirmatoria abre incidente siempre, aunque venga sola: si
        CONAF dice que hay un incendio, no hace falta que nadie más lo corrobore.
        El resto tiene que alcanzar el mínimo de señales configurado.
        """
        if any(rule_for(member.source).confirming for member in members):
            return True
        return len(members) >= self.min_signals

    async def _open_incident(
        self, members: Sequence[ClusteredEvent], *, lat: float, lon: float
    ) -> Incident:
        timestamps = [member.timestamp for member in members]
        return await self.repo.create_incident(
            lat=lat,
            lon=lon,
            type=self._seed_type(members),
            status=IncidentStatus.ACTIVE,
            first_seen_at=min(timestamps),
            last_seen_at=max(timestamps),
        )

    @staticmethod
    def _seed_type(members: Sequence[ClusteredEvent]) -> IncidentType:
        """Tipo con el que nace el incidente, antes del primer `_refresh`.

        Hasta la capa de accidentes esto era `POSSIBLE_FIRE` fijo, y funcionaba
        porque todo lo que el motor agrupaba era fuego: `_refresh` recalculaba el
        tipo real medio segundo después y el valor sembrado no llegaba a
        significar nada.

        Con más de una familia en juego dejó de ser inocuo. `find_nearest_open_incident`
        filtra por familia, así que un incidente de accidente que naciera rotulado
        `possible_fire` quedaría en la familia equivocada durante esa ventana: las
        señales siguientes del mismo choque no lo encontrarían y abrirían un
        incidente duplicado a metros del primero.

        Se siembra con el tipo mejor sostenido por confianza dentro del racimo.
        `resolve_type` hace el juicio definitivo enseguida, con la política
        completa; esto sólo tiene que caer en la familia correcta.
        """
        weighted: dict[IncidentType, float] = defaultdict(float)
        for member in members:
            incident_type = EVENT_TO_INCIDENT_TYPE.get(member.type)
            if incident_type is not None:
                weighted[incident_type] += max(member.confidence, 0.0)

        if not weighted:
            return IncidentType.POSSIBLE_FIRE
        return max(weighted.items(), key=lambda item: (item[1], item[0].value))[0]

    # -- Fusión ---------------------------------------------------------------

    async def _merge_converged(self, result: CorrelationPass, *, now: datetime) -> None:
        """Funde incidentes abiertos cuyos centroides quedaron dentro del radio.

        Un incendio que avanza produce racimos sucesivos que terminan tocándose.
        Sobrevive el más antiguo: es el que ya tiene folio circulando por radio.
        """
        since = now - timedelta(hours=self.match_window_hours)
        pairs = await self.repo.find_mergeable(radius_m=self.radius_m, since=since)
        if not pairs:
            return

        redirect: dict[int, int] = {}
        for keep_id, drop_id in pairs:
            keep = self._resolve_redirect(redirect, keep_id)
            drop = self._resolve_redirect(redirect, drop_id)
            if keep == drop:
                continue
            if drop < keep:  # sobrevive siempre el id menor, o sea el más antiguo
                keep, drop = drop, keep
            await self.repo.merge(keep_id=keep, drop_id=drop)
            redirect[drop] = keep
            result.incidents_merged += 1

        for survivor in set(redirect.values()):
            incident = await self.repo.get_by_id(survivor)
            if incident is not None:
                await self._refresh(incident, now=now)

    @staticmethod
    def _resolve_redirect(redirect: dict[int, int], incident_id: int) -> int:
        seen: set[int] = set()
        current = incident_id
        while current in redirect and current not in seen:
            seen.add(current)
            current = redirect[current]
        return current

    # -- Paso B ---------------------------------------------------------------

    async def _step_b_commune(self, result: CorrelationPass, *, now: datetime) -> None:
        """Adosa las alertas vigentes sin geometría a los incidentes de su comuna.

        Este paso **no crea incidentes**. Una alerta que no encuentra ningún
        incidente espacial en su comuna queda sin vincular, y así debe ser: la
        alternativa sería pintar un punto en un mapa que ninguna fuente observó.
        """
        active_since = now - timedelta(hours=settings.CORRELATION_ACTIVE_WINDOW_HOURS)
        incidents = list(await self.repo.open_incidents(since=active_since))
        if not incidents:
            return

        result.incidents_without_commune = sum(
            1 for incident in incidents if not incident.commune
        )

        alerts_raw = await self.repo.vigent_alerts(
            updated_since=now - timedelta(hours=self.alert_validity_hours),
            sources=[EventSource.SENAPRED, EventSource.MUNICIPALITY],
        )
        result.alerts_considered = len(alerts_raw)

        by_id = {incident.id: incident for incident in incidents}
        # Los incidentes que HOY llevan alerta también hay que refrescarlos:
        # si la alerta se levantó, su `alert_level` tiene que caerse.
        affected: set[int] = {
            incident.id for incident in incidents if incident.alert_level is not None
        }

        # Reconstrucción completa del Paso B. Ver el docstring del módulo.
        await self.repo.drop_links_by_method(method=LinkMethod.COMMUNE_TEXT)

        alert_views: list[AlertView] = [
            build_alert_view(
                event_id=alert.id, raw_data=alert.raw_data or {}, text=alert.text
            )
            for alert in alerts_raw
        ]
        incident_views = [
            IncidentView(
                incident_id=incident.id,
                commune=incident.commune,
                type=incident.type,
            )
            for incident in incidents
        ]

        matches = match_alerts_to_incidents(
            alert_views, incident_views, attach_regional=self.attach_regional_alerts
        )

        grouped: dict[int, list[EventLink]] = defaultdict(list)
        for match in matches:
            grouped[match.incident_id].append(
                EventLink(
                    raw_event_id=match.alert_event_id,
                    link_method=LinkMethod.COMMUNE_TEXT,
                    link_confidence=match.link_confidence,
                    matched_commune=match.matched_commune,
                    note=match.note,
                )
            )

        for incident_id, links in grouped.items():
            result.alert_links += await self.repo.link_events(
                incident_id=incident_id, links=links
            )
            affected.add(incident_id)

        for incident_id in affected:
            incident = by_id.get(incident_id)
            if incident is not None:
                await self._refresh(incident, now=now)

    # -- Caducidad ------------------------------------------------------------

    async def _expire(self, result: CorrelationPass, *, now: datetime) -> None:
        """Dos caducidades distintas, con criterios distintos.

        `mark_stale` mide **silencio**: un incidente real sobre el que dejaron de
        llegar señales pasa a `stale` tras horas. No afirma que se haya apagado,
        sólo que nadie lo está viendo.

        `expire_uncorroborated_citizen` mide **falta de respaldo**: un reporte
        ciudadano que a los pocos minutos no consiguió que ninguna otra fuente lo
        acompañe se descarta. No es que haya dejado de llegar información — es
        que nunca hubo suficiente.

        El orden importa poco porque operan sobre conjuntos disjuntos (uno exige
        horas de silencio, el otro minutos de soledad), pero el descarte va
        primero: un incidente ya descartado no debería contarse además como
        `stale` en la traza de la pasada.
        """
        result.incidents_dismissed = await self.repo.expire_uncorroborated_citizen(
            older_than=now - timedelta(minutes=self.citizen_ttl_minutes),
            max_confidence=self.citizen_max_confidence,
        )

        threshold = now - timedelta(hours=self.stale_hours)
        result.incidents_stale = await self.repo.mark_stale(threshold=threshold)

    # -- Recálculo de un incidente -------------------------------------------

    async def _refresh(self, incident: Incident, *, now: datetime) -> None:
        """Recalcula confianza, tipo, estado, geometría y metadatos.

        Es el único lugar donde se escribe la confianza de un incidente. Que
        exista una sola ruta importa: si la confianza se pudiera fijar desde dos
        sitios, tarde o temprano uno de los dos se olvidaría de un techo.
        """
        signals = await self.repo.signals_of(incident.id)
        if not signals:
            return

        views = [SignalView.from_orm(event) for event in signals]
        # El tipo se resuelve ANTES de puntuar, y el orden importa: `score`
        # necesita la familia para rotular el tramo de confianza con el
        # sustantivo correcto ("Accidente confirmado" y no "Incendio
        # confirmado"). No influye en el número, sólo en cómo se lo nombra.
        incident_type = resolve_type(views)
        scored = score(views, family=family_of_incident(incident_type))
        commune, province = self._resolve_territory(signals)

        # La ventana temporal la marcan las OBSERVACIONES del fenómeno, no las
        # alertas: una alerta declarada hace tres días adosada hoy no debe
        # retroceder el `first_seen_at` de un incendio que empezó esta mañana.
        observed = [event for event in signals if event.lat is not None] or list(signals)
        timestamps = [event.timestamp for event in observed]

        values: dict[str, object] = {
            "type": incident_type,
            "confidence": scored.confidence,
            "alert_confidence": scored.alert_confidence,
            "alert_level": scored.alert_level,
            "is_official_confirmed": scored.is_official_confirmed,
            "confidence_breakdown": scored.breakdown,
            "event_count": len(signals),
            "source_count": len(scored.sources),
            "sources": [source.value for source in scored.sources],
            "commune": commune,
            "province": province,
            "title": build_title(incident_type, commune),
            "first_seen_at": min(timestamps),
            "last_seen_at": max(timestamps),
            "correlated_at": now,
        }

        geometry = await self.repo.recompute_geometry(incident.id)
        if geometry is not None:
            values["lat"], values["lon"] = geometry

        if incident.status in _MUTABLE_STATUSES:
            status, resolved_at = resolve_status(views)
            values["status"] = status
            values["resolved_at"] = resolved_at

        await self.repo.update_incident(incident.id, **values)

    @staticmethod
    def _resolve_territory(
        signals: Sequence[RawEvent],
    ) -> tuple[str | None, str | None]:
        """Comuna y provincia del incidente.

        Se recorre por confianza descendente —`signals_of` ya ordena así— para
        que mande el dato territorial de la fuente más creíble. CONAF trae
        comuna en su capa; FIRMS y los reportes ciudadanos no, y en ese caso se
        devuelve `None` en vez de inventar una.
        """
        commune: str | None = None
        province: str | None = None
        for event in signals:
            if commune is None:
                commune = extract_commune(
                    commune=event.commune,
                    raw_data=event.raw_data or {},
                    text=event.text,
                )
            if province is None:
                province = extract_province(
                    province=event.province, raw_data=event.raw_data or {}
                )
            if commune and province:
                break
        return (commune, province)
