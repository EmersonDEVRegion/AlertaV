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

Entre el Paso A y la fusión corre además el **vínculo por sector**
(`_step_a_sector`): una nota de prensa que dice "sector de Miraflores Alto" y no
trae calle se une al incidente de su misma familia cuyas señales nombran ese
sector. Y dentro del Paso A, un racimo que no encuentra incidente a menos de
`radius_m` prueba lo mismo antes de abrir uno nuevo. Ver `_incident_by_sector`.
4. **Caducidad.** Dos reglas distintas y con criterios distintos: los
   incidentes sin señales nuevas pasan a `stale` tras horas, y los sostenidos
   sólo por reportes ciudadanos que no juntaron el quórum se descartan tras
   minutos. Ver `_expire`.

Publicación
-----------
`_refresh` escribe además `ciudadanos_independientes` y `publico`. Lo que tenga
una fuente no ciudadana se publica como siempre. Lo sólo ciudadano, cuando lo
reportan `CITIZEN_QUORUM` vecinos independientes (dispositivos Y redes
distintos) y el freno global no está puesto. Los reportes repetidos de un mismo
vecino no suman confianza. Ver `app.services.ciudadanos`.

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

El vínculo por sector agrega una cuarta, `find_open_incident_by_sector`, que
filtra por familia por la misma razón: un choque y un incendio en el mismo
sector la misma tarde no son el mismo hecho.

Dejar una sola abierta anula a las otras dos: bastaría con que el choque se
adhiriera al incendio ya existente para que todo el trabajo de particionar el
Paso A no sirviera de nada.

Lo que NO separa: los grados de certeza sobre un mismo fenómeno. `smoke`,
`thermal_anomaly` y `wildfire` caen todos en la familia `fire` y se corroboran
entre sí. Eso es el sistema funcionando, no una fuga.

Perfiles por familia
--------------------
Con `CORRELATION_PERFILES` (encendido por defecto) cada familia tiene su radio,
su brecha temporal y su edad máxima (`perfiles.py`): el DBSCAN agrupa con el
radio de la familia, el racimo se corta donde se abre el tiempo, una señal sólo
se adhiere a un incidente que estuvo vivo cerca de su hora, el radio de
adhesión crece con la imprecisión del punto (`street`, `sector`), y lo que llega
tarde (por `ingested_at`) todavía se agrupa. En `false`, todo vuelve a lo de
antes: un radio y una ventana por `timestamp`.

Con `CORRELATION_SOLO_REGION` (encendido) sólo se agrupan señales dentro de la V
Región —o a ~2 km de ella—, medido contra `comunas_region`. La caja de ingesta
deja entrar media Región Metropolitana, y eso eran los incidentes «sin comuna».

La comuna tiene además un último recurso: el polígono comunal que contiene al
incidente (`comunas_region`, migración 0015). Ese sí no depende del interruptor.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy import text as sa_text
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
    NearbyIncident,
    SectorSignal,
    SenalUbicada,
)
from app.services.ciudadanos import es_publico, seleccionar_independientes
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
from app.services.correlation.despachos import (
    ESQUINA_RADIO_M,
    compatibles,
    es_despacho,
    partir_por_llamado,
)
from app.services.correlation import relaciones as rel
from app.services.correlation.fuentes_fijas import fuente_conocida, solo_firms
from app.services.correlation.perfiles import (
    PERFILES,
    Perfil,
    holgura,
    partir_por_brecha,
    perfil,
    perfil_para,
)

logger = logging.getLogger(__name__)

#: Estados sobre los que el motor puede escribir. `merged` y `dismissed` son
#: decisiones ya tomadas —por el propio motor o por un operador— y recalcularlas
#: en cada pasada las desharía.
_MUTABLE_STATUSES = frozenset(
    {IncidentStatus.ACTIVE, IncidentStatus.CONTROLLED, IncidentStatus.STALE}
)

_EARTH_RADIUS_M = 6_371_008.8

#: Los días de la regla de recurrencia de FIRMS se cuentan en hora de Chile.
_ZONA_CHILE = ZoneInfo("America/Santiago")

#: Incidentes cercanos que se miran al adherir un racimo: el más cercano puede
#: ser otro llamado (regla de despachos) y el siguiente, el correcto.
_CANDIDATOS_ADHESION = 5

#: `link_confidence` de un vínculo por sector. Por debajo del 1.0 espacial y del
#: 0.70 de una comuna exacta en el Paso B, aunque un sector sea mucho más fino
#: que una comuna: acá no hay un organismo declarando nada, sólo dos textos que
#: dicen el mismo nombre. No entra en la confianza del incidente —esa se calcula
#: sobre las señales—; es la marca con que un operador reconoce el vínculo más
#: débil al auditar.
LINK_CONFIDENCE_SECTOR = 0.60


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
    #: Racimos con punto que no tenían incidente a menos de `radius_m` y se
    #: unieron a uno por nombrar el mismo sector.
    clusters_joined_by_sector: int = 0
    #: Señales sin punto que nombran un sector, evaluadas en la pasada.
    unlocated_sector_signals: int = 0
    #: Vínculos `sector_text` escritos en la pasada, de las dos vías.
    sector_links: int = 0
    alerts_considered: int = 0
    alert_links: int = 0
    incidents_merged: int = 0
    incidents_stale: int = 0
    #: Descartados por no conseguir corroboración a tiempo. Es la métrica que
    #: dirá si el TTL de 5 minutos está bien calibrado o mata reportes válidos.
    incidents_dismissed: int = 0
    #: Incidentes que el Paso B no pudo alcanzar por no tener comuna. Desde la
    #: 0015 debería quedar en cero para todo lo que cae dentro de la región.
    incidents_without_commune: int = 0
    #: Incidentes cuya comuna salió del polígono y no de una señal.
    communes_by_polygon: int = 0
    #: Tramos extra que salieron de cortar racimos por su brecha temporal.
    clusters_split: int = 0
    #: Incidentes abiertos de los que se desvinculó la prensa en esta pasada
    #: (`CORRELATION_PRENSA=false`). En régimen, cero.
    prensa_desvinculada: int = 0
    #: De ésos, los que sólo tenían prensa y se descartaron.
    solo_prensa_descartados: int = 0
    #: Tramos extra que salieron de separar despachos que no son el mismo
    #: llamado (`correlation/despachos.py`).
    llamados_separados: int = 0
    #: Incidentes cercanos a los que un racimo NO se adhirió por ser otro
    #: llamado o por quedar fuera del perfil estructural.
    adhesiones_rechazadas: int = 0
    #: Pares que `find_mergeable` propuso y la regla de despachos rechazó.
    fusiones_rechazadas: int = 0
    #: Racimos sólo de FIRMS en una fuente de calor fija: no abrieron incidente.
    firms_fuente_fija: int = 0
    #: Vínculos entre familias escritos en la pasada (`correlation/relaciones.py`).
    relaciones_nuevas: int = 0
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
            "clusters_joined_by_sector": self.clusters_joined_by_sector,
            "unlocated_sector_signals": self.unlocated_sector_signals,
            "sector_links": self.sector_links,
            "alerts_considered": self.alerts_considered,
            "alert_links": self.alert_links,
            "incidents_merged": self.incidents_merged,
            "incidents_stale": self.incidents_stale,
            "incidents_dismissed": self.incidents_dismissed,
            "incidents_without_commune": self.incidents_without_commune,
            "communes_by_polygon": self.communes_by_polygon,
            "clusters_split": self.clusters_split,
            "prensa_desvinculada": self.prensa_desvinculada,
            "solo_prensa_descartados": self.solo_prensa_descartados,
            "llamados_separados": self.llamados_separados,
            "adhesiones_rechazadas": self.adhesiones_rechazadas,
            "fusiones_rechazadas": self.fusiones_rechazadas,
            "firms_fuente_fija": self.firms_fuente_fija,
            "relaciones_nuevas": self.relaciones_nuevas,
            "warnings": list(self.warnings),
        }


def _aware(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


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
        sector_window_hours: int | None = None,
        stale_hours: int | None = None,
        citizen_ttl_minutes: int | None = None,
        alert_validity_hours: int | None = None,
        min_signals: int | None = None,
        attach_regional_alerts: bool | None = None,
        max_events: int | None = None,
        perfiles: bool | None = None,
        solo_region: bool | None = None,
        prensa: bool | None = None,
        despachos_por_calle: bool | None = None,
        fuentes_fijas: bool | None = None,
        relaciones: str | None = None,
    ) -> None:
        self.session = session
        self.repo = IncidentRepository(session)
        self.radius_m = radius_m or settings.CORRELATION_RADIUS_M
        self.window_hours = window_hours or settings.CORRELATION_WINDOW_HOURS
        self.match_window_hours = (
            match_window_hours or settings.CORRELATION_MATCH_WINDOW_HOURS
        )
        self.sector_window_hours = (
            sector_window_hours or settings.CORRELATION_SECTOR_WINDOW_HOURS
        )
        self.stale_hours = stale_hours or settings.CORRELATION_STALE_HOURS
        self.citizen_ttl_minutes = (
            citizen_ttl_minutes or settings.CITIZEN_UNCORROBORATED_TTL_MINUTES
        )
        self.citizen_only_stale_hours = settings.CITIZEN_ONLY_STALE_HOURS
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
        self.perfiles = settings.CORRELATION_PERFILES if perfiles is None else perfiles
        self.solo_region = (
            settings.CORRELATION_SOLO_REGION if solo_region is None else solo_region
        )
        #: ¿La prensa entra al motor? Ver `CORRELATION_PRENSA`.
        self.prensa = settings.CORRELATION_PRENSA if prensa is None else prensa
        #: Regla de despachos. Ver `CORRELATION_DESPACHOS_POR_CALLE`.
        self.despachos_por_calle = (
            settings.CORRELATION_DESPACHOS_POR_CALLE
            if despachos_por_calle is None
            else despachos_por_calle
        )
        self.despacho_radio_m = settings.CORRELATION_DESPACHO_RADIO_M
        self.despacho_brecha_max = timedelta(hours=settings.CORRELATION_DESPACHO_BRECHA_HORAS)
        #: Fuentes de calor fijas. Ver `FIRMS_FUENTES_FIJAS`.
        self.fuentes_fijas = (
            settings.FIRMS_FUENTES_FIJAS if fuentes_fijas is None else fuentes_fijas
        )
        #: Vínculos entre familias: `off`, `sombra` o `visible`. Ver
        #: `CORRELATION_RELACIONES`; el motor calcula igual en `sombra` y `visible`.
        self.relaciones = relaciones or settings.CORRELATION_RELACIONES
        #: ¿Existe `comunas_region`? Se pregunta una vez por pasada.
        self._hay_comunas: bool | None = None
        self._comunas_por_poligono = 0
        #: Freno global de reportes ciudadanos (`CITIZEN_GLOBAL_BRAKE_PER_10MIN`).
        #: Se mide una vez por pasada, al comienzo.
        self._freno_ciudadano = False

    # -- Orquestación ---------------------------------------------------------

    async def run(self, now: datetime | None = None) -> CorrelationPass:
        """Una pasada completa. Commit único al final.

        La pasada es atómica a propósito: un fallo a mitad del Paso B no puede
        dejar el mapa con incidentes creados pero sin sus alertas, ni con los
        enlaces del Paso B borrados y no reconstruidos.

        `now` existe para `scripts/replay_correlacion.py`, que reproduce días
        de señales con un reloj simulado. En producción se omite.
        """
        now = now or datetime.now(UTC)
        result = CorrelationPass(started_at=now)
        self._hay_comunas = None
        self._comunas_por_poligono = 0

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
            self._freno_ciudadano = await self._freno_global(result, now=now)
            await self._retirar_prensa(result, now=now)
            await self._step_a_spatial(result, now=now)
            await self._step_a_sector(result, now=now)
            await self._merge_converged(result, now=now)
            await self._step_b_commune(result, now=now)
            await self._relacionar(result, now=now)
            await self._expire(result, now=now)
            result.communes_by_polygon = self._comunas_por_poligono
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            logger.exception("la pasada de correlación falló; se revirtió entera")
            raise

        result.finished_at = datetime.now(UTC)
        logger.info("pasada de correlación", extra=result.as_dict())
        return result

    # -- Vínculos entre familias ---------------------------------------------

    async def _relacionar(self, result: CorrelationPass, *, now: datetime) -> None:
        """Deja anotado qué hechos de familias distintas ocurrieron cerca.

        No toca incidentes ni señales: sólo escribe en `incident_relations`.
        Mira los incidentes de origen de las últimas
        `CORRELATION_RELACIONES_VENTANA_HORAS`, y como el destino puede llegar
        más tarde (el corte de luz aparece después del choque), cada pasada
        vuelve a mirarlos hasta que salen de la ventana. El índice único hace
        que repetir un par no escriba nada.
        """
        if self.relaciones == "off":
            return
        await self.session.flush()
        desde = now - timedelta(hours=settings.CORRELATION_RELACIONES_VENTANA_HORAS)
        margen = rel.margen()
        tipos_origen = rel.tipos_de_origen()
        tipos_incidente, tipos_evento = rel.tipos_de_destino()
        vivos = [
            s for s in IncidentStatus if s not in (IncidentStatus.MERGED, IncidentStatus.DISMISSED)
        ]
        filas = (
            await self.session.execute(
                select(
                    Incident.id, Incident.type, Incident.lat, Incident.lon, Incident.first_seen_at
                )
                .where(Incident.type.in_(tipos_origen | tipos_incidente))
                .where(Incident.status.in_(vivos))
                .where(Incident.publico.is_(True))
                .where(Incident.first_seen_at >= desde - margen)
                .where(Incident.first_seen_at <= now + margen)
            )
        ).all()
        incidentes = [
            rel.Punto(f.id, f.type.value, f.lat, f.lon, _aware(f.first_seen_at)) for f in filas
        ]
        valores_origen = {t.value for t in tipos_origen}
        origenes = [p for p in incidentes if p.tipo in valores_origen and desde <= p.hora <= now]
        if not origenes:
            return
        eventos: list[rel.Punto] = []
        if tipos_evento:
            filas_eventos = (
                await self.session.execute(
                    select(RawEvent.id, RawEvent.type, RawEvent.lat, RawEvent.lon, RawEvent.timestamp)
                    .where(RawEvent.type.in_(tipos_evento))
                    .where(RawEvent.lat.isnot(None))
                    .where(RawEvent.lon.isnot(None))
                    .where(RawEvent.timestamp >= desde - margen)
                    .where(RawEvent.timestamp <= now + margen)
                )
            ).all()
            eventos = [
                rel.Punto(e.id, e.type.value, e.lat, e.lon, _aware(e.timestamp))
                for e in filas_eventos
            ]
        pares = rel.emparejar(origenes, incidentes, eventos, distancia=haversine_m)
        insertar = sa_text(
            f"INSERT INTO {settings.DB_SCHEMA}.incident_relations "
            "(incident_id, otro_incident_id, otro_event_id, regla, distancia_m, desfase_s) "
            "VALUES (:origen, :otro_incidente, :otro_evento, :regla, :distancia, :desfase) "
            "ON CONFLICT (incident_id, regla, (COALESCE(otro_incident_id, 0)), "
            "(COALESCE(otro_event_id, 0))) DO NOTHING"
        )
        for par in pares:
            escrito = await self.session.execute(
                insertar,
                {
                    "origen": par.origen_id,
                    "otro_incidente": par.otro_id if par.otro_es_incidente else None,
                    "otro_evento": None if par.otro_es_incidente else par.otro_id,
                    "regla": par.regla,
                    "distancia": par.distancia_m,
                    "desfase": par.desfase_s,
                },
            )
            result.relaciones_nuevas += max(escrito.rowcount or 0, 0)

    # -- Fuentes fuera del motor ---------------------------------------------

    def fuentes_excluidas(self) -> tuple[EventSource, ...]:
        """Fuentes cuyas señales no se agrupan ni se pegan a un incidente.

        Hoy sólo la prensa, y sólo con `CORRELATION_PRENSA=false` (el valor por
        defecto). Una nota llega con horas de atraso: abriría pines que ya no
        describen el presente y tiraría del punto de un despacho exacto. Se lee en `/feed/noticias`.
        """
        return () if getattr(self, "prensa", True) else (EventSource.MEDIA,)

    async def _retirar_prensa(self, result: CorrelationPass, *, now: datetime) -> None:
        """Saca la prensa de los incidentes abiertos que ya la tenían.

        Cubre lo que quedó de antes del cambio: sin esto, un incidente abierto
        por una nota seguiría en el mapa hasta caducar, y uno mixto seguiría
        con la confianza y el punto que la nota le movió. Un incidente que
        sólo tenía prensa se descarta (`dismissed`: nunca hubo evidencia fuera
        de la prensa); el resto se recalcula con lo que le queda.

        En régimen no hay nada que retirar y cuesta una consulta.
        """
        if self.prensa:
            return
        ids = await self.repo.retirar_fuente_de_abiertos(EventSource.MEDIA)
        result.prensa_desvinculada = len(ids)
        for incident_id in ids:
            if not await self.repo.signals_of(incident_id):
                await self.repo.update_incident(incident_id, status=IncidentStatus.DISMISSED)
                result.solo_prensa_descartados += 1
                continue
            incident = await self.repo.get_by_id(incident_id)
            if incident is not None:
                await self._refresh(incident, now=now)
        if ids:
            logger.info(
                "prensa retirada de incidentes abiertos",
                extra={
                    "incidentes": len(ids),
                    "descartados": result.solo_prensa_descartados,
                },
            )

    # -- Paso A ---------------------------------------------------------------

    async def _step_a_spatial(self, result: CorrelationPass, *, now: datetime) -> None:
        """Agrupa señales georreferenciadas y las vuelca en incidentes."""
        since = now - timedelta(hours=self.window_hours)
        match_since = now - timedelta(hours=self.match_window_hours)

        # Sin la tabla de comunas (antes de la migración 0015) no hay con qué
        # filtrar: se agrupa como antes.
        solo_region = self.solo_region and await self._comunas_disponibles()
        if self.perfiles:
            clustered = await self.repo.cluster_unassigned_events(
                since=since,
                radius_m=self.radius_m,
                limit=self.max_events,
                radios={familia: p.radio_m for familia, p in PERFILES.items()},
                edades_desde={familia: now - p.edad_max for familia, p in PERFILES.items()},
                solo_region=solo_region,
                excluir_fuentes=self.fuentes_excluidas(),
            )
        else:
            clustered = await self.repo.cluster_unassigned_events(
                since=since,
                radius_m=self.radius_m,
                limit=self.max_events,
                solo_region=solo_region,
                excluir_fuentes=self.fuentes_excluidas(),
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

        for (family, _), racimo in clusters.items():
            # DBSCAN con `minpoints=1` encadena: A–B–C a 1,4 km cada uno forman
            # un racimo aunque A y C estén a 4 km y a tres horas. Se corta donde
            # se abre el tiempo; sin perfiles, el racimo va entero como antes.
            tramos = (
                partir_por_brecha(racimo, self._perfil(family, racimo).brecha)
                if self.perfiles
                else [racimo]
            )
            result.clusters_split += len(tramos) - 1
            if self.despachos_por_calle:
                # Dos despachos cercanos que nombran otras calles son dos
                # llamados, aunque DBSCAN los haya puesto en el mismo racimo.
                por_llamado: list[list[ClusteredEvent]] = []
                for tramo in tramos:
                    partes = partir_por_llamado(
                        tramo,
                        radio_m=self.despacho_radio_m,
                        brecha=self._brecha_despachos(family),
                        radio_ajenas_m=self._perfil(family, tramo).radio_m,
                    )
                    result.llamados_separados += len(partes) - 1
                    por_llamado.extend(partes)
                tramos = por_llamado
            # Del más antiguo al más nuevo: el refuerzo encuentra el incidente
            # que abrió su primer despacho aunque DBSCAN los separara.
            for members in sorted(tramos, key=lambda t: min(m.timestamp for m in t)):
                await self._absorb_cluster(
                    members, family=family, result=result, now=now, match_since=match_since
                )

    async def _absorb_cluster(
        self,
        members: Sequence[ClusteredEvent],
        *,
        family: str,
        result: CorrelationPass,
        now: datetime,
        match_since: datetime,
    ) -> None:
        """Un racimo (o tramo) → al incidente cercano, al de su sector, o a uno nuevo."""
        lat, lon = weighted_centroid(members)

        # Con despachos se busca más lejos: la misma esquina repetida puede
        # caer a dos kilómetros por error de geocodificación, y la que decide
        # ahí es la dirección (`_primer_admisible`), no el radio.
        con_despachos = self.despachos_por_calle and any(es_despacho(m) for m in members)
        if self.perfiles:
            p = perfil(family)
            horas = [member.timestamp for member in members]
            brecha = p.brecha
            # Un punto `street` puede estar en cualquier parte de una avenida
            # de dos kilómetros: el radio crece con la imprecisión del peor.
            radio = p.radio_m + holgura(member.precision for member in members)
            candidatos = await self.repo.find_open_incidents_near(
                lat=lat,
                lon=lon,
                radius_m=max(radio, ESQUINA_RADIO_M) if con_despachos else radio,
                since=match_since,
                family=family,
                # El incidente tuvo que estar vivo cerca de la hora de la señal.
                desde=min(horas) - brecha,
                hasta=max(horas) + brecha,
                limit=_CANDIDATOS_ADHESION,
            )
        else:
            candidatos = await self.repo.find_open_incidents_near(
                lat=lat,
                lon=lon,
                radius_m=max(self.radius_m, ESQUINA_RADIO_M) if con_despachos else self.radius_m,
                since=match_since,
                family=family,
                limit=_CANDIDATOS_ADHESION,
            )
        nearby = await self._primer_admisible(candidatos, members, family=family, result=result)

        method = LinkMethod.SPATIAL
        by_sector: Incident | None = None
        if nearby is None:
            by_sector = await self._incident_by_sector(
                [member.sector_clave for member in members],
                family=family,
                now=now,
                members=members,
            )

        if nearby is not None:
            incident = nearby.incident
        elif by_sector is not None:
            # Nada a menos del radio, pero un incidente de la misma familia
            # nombra el mismo sector. Uno de los dos puntos está mal —la prensa
            # no da esquinas y OSM tiene calles homónimas— y lo que las dos
            # fuentes sí dicen igual es el sector.
            incident = by_sector
            method = LinkMethod.SECTOR_TEXT
            result.clusters_joined_by_sector += 1
        else:
            if self.fuentes_fijas and await self._en_fuente_fija(members):
                # Una fundición o una refinería, no un incendio. Las señales
                # quedan sin incidente: si otra fuente abre uno ahí, se suman.
                result.firms_fuente_fija += 1
                return
            if not self._should_open_incident(members):
                # Señal aislada de una fuente no confirmatoria: se deja sin
                # incidente. No se pierde —sigue siendo una `raw_event`
                # consultable— y la próxima pasada volverá a evaluarla junto a
                # la corroboración que pueda haber llegado entretanto.
                result.clusters_deferred += 1
                return
            incident = await self._open_incident(members, lat=lat, lon=lon)
            result.incidents_created += 1

        links = [
            EventLink(
                raw_event_id=member.event_id,
                link_method=method,
                link_confidence=(
                    1.0 if method is LinkMethod.SPATIAL else LINK_CONFIDENCE_SECTOR
                ),
                # En el vínculo por sector la distancia también se guarda: es lo
                # que muestra, al auditar, cuánto discrepaban los dos puntos que
                # el sector unió.
                distance_m=round(
                    haversine_m(incident.lat, incident.lon, member.lat, member.lon),
                    2,
                ),
                note=(
                    f"sector: {member.sector_clave}"
                    if method is LinkMethod.SECTOR_TEXT
                    else None
                ),
            )
            for member in members
        ]
        written = await self.repo.link_events(incident_id=incident.id, links=links)
        if method is LinkMethod.SPATIAL:
            result.spatial_links += written
        else:
            result.sector_links += written
        await self.repo.assign_events_to_incident(
            incident_id=incident.id,
            event_ids=[member.event_id for member in members],
            processed_at=now,
        )
        await self._refresh(incident, now=now)
        result.incidents_updated += 1

    def _brecha_despachos(self, family: str) -> timedelta:
        """Brecha de la regla de despachos: nunca más larga que la de la familia.

        La regla separa llamados; no puede juntar lo que el perfil ya separaba
        por tiempo (dos choques a 2 h y media en la misma ruta son dos choques).
        """
        if not self.perfiles:
            return self.despacho_brecha_max
        return min(self.despacho_brecha_max, perfil(family).brecha)

    @staticmethod
    def _perfil(family: str, senales: Sequence[ClusteredEvent | SenalUbicada]) -> Perfil:
        """Perfil de la familia, o el estructural si el grupo es sólo estructural."""
        return perfil_para(family, [senal.type for senal in senales])

    async def _primer_admisible(
        self,
        candidatos: Sequence[NearbyIncident],
        members: Sequence[ClusteredEvent],
        *,
        family: str,
        result: CorrelationPass,
    ) -> NearbyIncident | None:
        """El incidente más cercano al que este racimo se puede adherir.

        * **Si los dos lados tienen despachos, decide la dirección**: uno de cada
          lado tiene que ser el mismo llamado (`correlation/despachos.py`), y
          con eso basta aunque los puntos estén lejos.
        * **Si no, decide la geometría** con el perfil que corresponde: un
          incendio estructural acepta señales a 500 m y 3 h, no a 1500 m y 8 h
          como uno forestal.
        """
        if not candidatos:
            return None
        senales = await self.repo.senales_de_incidentes([c.incident.id for c in candidatos])
        horas = [member.timestamp for member in members]
        margen = holgura(member.precision for member in members)
        hay_despachos = any(es_despacho(m) for m in members)
        for candidato in candidatos:
            existentes = senales.get(candidato.incident.id, [])
            if (
                self.despachos_por_calle
                and hay_despachos
                and any(es_despacho(e) for e in existentes)
            ):
                if compatibles(
                    members,
                    existentes,
                    radio_m=self.despacho_radio_m,
                    brecha=self._brecha_despachos(family),
                ):
                    return candidato
                result.adhesiones_rechazadas += 1
                continue
            if self.perfiles:
                p = perfil_para(
                    family,
                    [
                        candidato.incident.type,
                        *(m.type for m in members),
                        *(e.type for e in existentes),
                    ],
                )
                fuera = (
                    candidato.distance_m > p.radio_m + margen
                    or candidato.incident.last_seen_at < min(horas) - p.brecha
                    or candidato.incident.first_seen_at > max(horas) + p.brecha
                )
            else:
                fuera = candidato.distance_m > self.radius_m
            if fuera:
                result.adhesiones_rechazadas += 1
                continue
            return candidato
        return None

    async def _en_fuente_fija(self, members: Sequence[ClusteredEvent]) -> bool:
        """¿Todo el racimo es FIRMS sobre una fuente de calor fija?

        Basta una señal de otra fuente, o un píxel fuera de los focos, para que
        el racimo se trate como siempre. Ver `correlation/fuentes_fijas.py`.
        """
        if not solo_firms([member.source for member in members]):
            return False
        ventana = timedelta(days=settings.FIRMS_FIJA_VENTANA_DIAS)
        for member in members:
            if fuente_conocida(member.lat, member.lon) is not None:
                continue
            local = member.timestamp.astimezone(_ZONA_CHILE)
            inicio_del_dia = local.replace(hour=0, minute=0, second=0, microsecond=0)
            dias = await self.repo.dias_con_anomalia(
                lat=member.lat,
                lon=member.lon,
                radio_m=settings.FIRMS_FIJA_RADIO_M,
                desde=inicio_del_dia - ventana,
                antes_de=inicio_del_dia,
            )
            if dias < settings.FIRMS_FIJA_DIAS:
                return False
        return True

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

    # -- Vínculo por sector ---------------------------------------------------

    async def _incident_by_sector(
        self,
        claves: Sequence[str | None],
        *,
        family: str,
        now: datetime,
        members: Sequence[ClusteredEvent] = (),
    ) -> Incident | None:
        """Incidente abierto de `family` que nombra alguno de estos sectores.

        Existe por el incendio de Miraflores Alto del 2026-09-03: la nota de
        Pura Noticia y el tuit contaban la misma casa quemada, cayeron a 2,5 km
        uno del otro, y el radio de 1500 m no podía unirlos. Lo que las dos
        fuentes sí decían con las mismas palabras era el sector.

        Tres condiciones, y las tres cuentan:

        * **Misma familia** — la cuarta puerta del aislamiento (ver el
          docstring del módulo).
        * **Misma comuna y mismo sector**, que es lo que codifica la clave
          ("vina del mar|miraflores alto"). Sin comuna no hay clave, y
          "Miraflores Alto" y "Miraflores Bajo" son claves distintas.
        * **Ventana corta** (`CORRELATION_SECTOR_WINDOW_HOURS`). Un sector mide
          un par de kilómetros: dos incendios en él con medio día de diferencia
          son dos incendios.
        """
        since = now - timedelta(hours=self.sector_window_hours)
        for clave in sorted({clave for clave in claves if clave}):
            incident = await self.repo.find_open_incident_by_sector(
                sector_clave=clave, family=family, since=since
            )
            if incident is None:
                continue
            if members and self.despachos_por_calle:
                # Mismo sector no hace a dos despachos el mismo llamado.
                senales = await self.repo.senales_de_incidentes([incident.id])
                if not compatibles(
                    members,
                    senales.get(incident.id, []),
                    radio_m=self.despacho_radio_m,
                    brecha=self._brecha_despachos(family),
                ):
                    continue
            return incident
        return None

    async def _step_a_sector(self, result: CorrelationPass, *, now: datetime) -> None:
        """Une las señales SIN punto que nombran un sector a su incidente.

        El Paso A no las ve —filtra por `geom IS NOT NULL`— y antes de esto una
        nota sin calle se quedaba fuera del mapa para siempre, aunque otra
        fuente estuviera contando el mismo incendio en el mismo sector.

        Como el Paso B, **no crea incidentes**: una nota que nombra un sector y
        no encuentra a nadie ahí espera. Sigue sin incidente y la próxima pasada
        la vuelve a mirar, dentro de `window_hours`, por si llegó la señal con
        punto que la ubique. Pintar un punto que ninguna fuente dio sería peor.

        A diferencia del Paso B, el vínculo **es historia**: no se reconstruye
        en cada pasada. La señal queda asignada (`raw_events.incident_id`), igual
        que una espacial, porque pertenece a un solo incidente.
        """
        since = now - timedelta(hours=self.window_hours)
        signals = await self.repo.unlocated_sector_signals(
            since=since, limit=self.max_events, excluir_fuentes=self.fuentes_excluidas()
        )
        result.unlocated_sector_signals = len(signals)
        if not signals:
            return

        grouped: dict[tuple[str, str], list[SectorSignal]] = defaultdict(list)
        for signal in signals:
            grouped[(signal.family, signal.sector_clave)].append(signal)

        for (family, clave), members in grouped.items():
            incident = await self._incident_by_sector([clave], family=family, now=now)
            if incident is None:
                continue

            links = [
                EventLink(
                    raw_event_id=member.event_id,
                    link_method=LinkMethod.SECTOR_TEXT,
                    link_confidence=LINK_CONFIDENCE_SECTOR,
                    note=f"sector: {clave}",
                )
                for member in members
            ]
            result.sector_links += await self.repo.link_events(
                incident_id=incident.id, links=links
            )
            await self.repo.assign_events_to_incident(
                incident_id=incident.id,
                event_ids=[member.event_id for member in members],
                processed_at=now,
            )
            await self._refresh(incident, now=now)
            result.incidents_updated += 1

    # -- Fusión ---------------------------------------------------------------

    async def _merge_converged(self, result: CorrelationPass, *, now: datetime) -> None:
        """Funde incidentes abiertos cuyos centroides quedaron dentro del radio.

        Un incendio que avanza produce racimos sucesivos que terminan tocándose.
        Sobrevive el más antiguo: es el que ya tiene folio circulando por radio.
        """
        since = now - timedelta(hours=self.match_window_hours)
        if self.perfiles:
            pairs = await self.repo.find_mergeable(
                radius_m=self.radius_m,
                since=since,
                radios={familia: p.radio_m for familia, p in PERFILES.items()},
                brechas={familia: p.brecha for familia, p in PERFILES.items()},
            )
        else:
            pairs = await self.repo.find_mergeable(radius_m=self.radius_m, since=since)
        if not pairs:
            return
        if self.despachos_por_calle or self.perfiles:
            pairs = await self._fusiones_admisibles(pairs, result)

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

    async def _fusiones_admisibles(
        self, pairs: Sequence[tuple[int, int]], result: CorrelationPass
    ) -> list[tuple[int, int]]:
        """Los pares que pueden fundirse: misma regla que al adherir un racimo."""
        ids = sorted({i for pair in pairs for i in pair})
        senales = await self.repo.senales_de_incidentes(ids)
        incidentes = {i: await self.repo.get_by_id(i) for i in ids}
        admisibles: list[tuple[int, int]] = []
        for keep_id, drop_id in pairs:
            a, b = incidentes.get(keep_id), incidentes.get(drop_id)
            if a is None or b is None:
                continue
            sa, sb = senales.get(keep_id, []), senales.get(drop_id, [])
            if (
                self.despachos_por_calle
                and any(es_despacho(x) for x in sa)
                and any(es_despacho(x) for x in sb)
            ):
                brecha = self._brecha_despachos(family_of_incident(a.type))
                if compatibles(sa, sb, radio_m=self.despacho_radio_m, brecha=brecha):
                    admisibles.append((keep_id, drop_id))
                else:
                    result.fusiones_rechazadas += 1
                continue
            if self.perfiles:
                p = perfil_para(
                    family_of_incident(a.type),
                    [a.type, b.type, *(s.type for s in sa), *(s.type for s in sb)],
                )
                if haversine_m(a.lat, a.lon, b.lat, b.lon) > p.radio_m or (
                    a.last_seen_at < b.first_seen_at - p.brecha
                    or b.last_seen_at < a.first_seen_at - p.brecha
                ):
                    result.fusiones_rechazadas += 1
                    continue
            admisibles.append((keep_id, drop_id))
        return admisibles

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
        )

        # Lo sólo ciudadano que sí llegó al mapa se apaga antes que el resto:
        # nadie oficial lo sostiene.
        stale_ciudadano = await self.repo.stale_citizen_only(
            threshold=now - timedelta(hours=self.citizen_only_stale_hours)
        )
        threshold = now - timedelta(hours=self.stale_hours)
        result.incidents_stale = stale_ciudadano + await self.repo.mark_stale(
            threshold=threshold
        )

    async def _freno_global(self, result: CorrelationPass, *, now: datetime) -> bool:
        """¿Entraron demasiados reportes ciudadanos en los últimos 10 minutos?

        Con el freno puesto, ningún incidente sólo ciudadano se publica en esta
        pasada. Lo que tenga otra fuente se publica igual.
        """
        tope = settings.CITIZEN_GLOBAL_BRAKE_PER_10MIN
        if tope <= 0:
            return False
        recientes = await self.repo.count_recent_citizen_reports(
            since=now - timedelta(minutes=10)
        )
        if recientes > tope:
            result.warnings.append(
                f"freno ciudadano: {recientes} reportes en 10 min (tope {tope}); "
                "no se publica nada sólo ciudadano"
            )
            logger.warning(
                "freno global de reportes ciudadanos",
                extra={"recientes": recientes, "tope": tope},
            )
            return True
        return False

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

        # Un vecino cuenta una vez: los reportes ciudadanos repetidos (mismo
        # dispositivo o misma red) no suman confianza ni cuentan para el quórum.
        # Las demás fuentes pasan enteras, con su propio descuento por redundancia.
        independientes = seleccionar_independientes(signals)
        elegidos = {event.id for event in independientes}
        puntuables = [
            event
            for event in signals
            if event.source is not EventSource.CITIZEN or event.id in elegidos
        ]

        views = [SignalView.from_orm(event) for event in puntuables]
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
            "ciudadanos_independientes": len(independientes),
            "publico": es_publico(
                fuentes=scored.sources,
                independientes=len(independientes),
                freno=self._freno_ciudadano,
            ),
            "first_seen_at": min(timestamps),
            "last_seen_at": max(timestamps),
            "correlated_at": now,
        }

        ubicacion = await self.repo.recompute_geometry(incident.id)
        if ubicacion is not None:
            values["lat"], values["lon"] = ubicacion.lat, ubicacion.lon
            values["ubicacion_precision"] = ubicacion.precision

        if commune is None:
            # Último recurso: el polígono que contiene al incidente. Ninguna
            # señal dijo la comuna —FIRMS, CGE sin localidad, un reporte con
            # GPS— pero el punto sí sabe dónde está.
            por_poligono, provincia = await self._commune_by_polygon(
                values.get("lat", incident.lat), values.get("lon", incident.lon)
            )
            if por_poligono is not None:
                commune = por_poligono
                province = province or provincia
                self._comunas_por_poligono += 1

        values["commune"] = commune
        values["province"] = province
        values["title"] = build_title(incident_type, commune)

        if incident.status in _MUTABLE_STATUSES:
            status, resolved_at = resolve_status(views)
            values["status"] = status
            values["resolved_at"] = resolved_at

        await self.repo.update_incident(incident.id, **values)

    async def _commune_by_polygon(
        self, lat: object, lon: object
    ) -> tuple[str | None, str | None]:
        """`comuna_por_punto`, si la tabla existe (migración 0015) y hay punto."""
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            return (None, None)
        if not await self._comunas_disponibles():
            return (None, None)
        return await self.repo.comuna_por_punto(float(lat), float(lon))

    async def _comunas_disponibles(self) -> bool:
        """¿Existe `comunas_region`? Se pregunta una vez por pasada."""
        if self._hay_comunas is None:
            self._hay_comunas = await self.repo.comunas_disponibles()
        return self._hay_comunas

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
