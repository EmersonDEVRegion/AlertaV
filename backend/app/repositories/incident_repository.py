"""Acceso a datos del motor de correlación.

Todo el SQL espacial del motor vive aquí. El servicio decide *qué* correlacionar;
este módulo sabe *cómo* preguntárselo a PostGIS.

Dos primitivas geométricas, cada una para lo suyo:

* `ST_ClusterDBSCAN` para agrupar señales sueltas entre sí. Es la operación
  correcta porque no depende del orden en que lleguen los eventos: agrupar
  incremental —"cada señal se pega a la primera vecina que encuentre"— produce
  racimos distintos según el orden de lectura, y eso es indefendible en algo
  que decide dónde hay un incendio. `eps` va en metros porque se proyecta a
  UTM 19S antes de agrupar. Va **particionado por familia de fenómeno**: ver
  `cluster_unassigned_events`.
* `ST_DWithin` sobre `geography` para pegar una señal nueva a un incidente que
  ya existe. Aquí sí interesa la distancia en metros reales sobre el elipsoide,
  y el índice GiST sigue haciendo el prefiltrado por caja envolvente.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    Case,
    ColumnElement,
    Select,
    Text,
    and_,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy import (
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.comuna import ComunaRegion
from app.models.enums import (
    CORRELATABLE_EVENT_TYPES,
    DEFAULT_FAMILY,
    INCIDENT_FAMILY,
    OPEN_INCIDENT_STATUSES,
    CollectorStatus,
    EventSource,
    EventType,
    IncidentStatus,
    IncidentType,
    LinkMethod,
    family_of_event,
)
from app.models.event import CollectorRun, RawEvent
from app.models.incident import Incident, IncidentEvent
from app.repositories.confidence_filters import confidence_at_least

#: Distancias en metros reales sobre el elipsoide.
_GEOGRAPHY = Geography(geometry_type="POINT", srid=4326)

#: Piso del peso en el centroide ponderado. Una señal de confianza 0 no debe
#: anular la división, pero tampoco arrastrar el punto.
_MIN_WEIGHT = 0.01

#: Clave del advisory lock que serializa las pasadas del motor. Arbitraria pero
#: fija: sólo tiene que ser la misma en todas las réplicas.
CORRELATION_LOCK_KEY = 0x_A1E2_7A00


def event_family_sql(column: ColumnElement[Any]) -> Case:
    """Traduce en SQL una columna `event_type` a su familia de fenómeno.

    El CASE se **genera** desde `family_of_event`, no se escribe a mano. Es la
    diferencia entre una tabla de traducción y dos: si alguien agrega un tipo de
    señal nuevo a `EVENT_TO_INCIDENT_TYPE` y esta función estuviera hardcodeada,
    el motor agruparía según una tabla que ya nadie mantiene, sin fallar y sin
    avisar. Del `else_` cuelgan sólo los tipos no correlacionables, que la
    consulta ya filtró antes.
    """
    # `sorted` porque `CORRELATABLE_EVENT_TYPES` es un frozenset: sin ordenar, el
    # SQL sale con las ramas en orden distinto en cada arranque del proceso. No
    # cambia el resultado, pero ensucia los diffs de logs y desperdicia la caché
    # de planes de PostgreSQL, que indexa por texto de la consulta.
    return case(
        {
            kind.value: family_of_event(kind)
            for kind in sorted(CORRELATABLE_EVENT_TYPES, key=lambda item: item.value)
        },
        value=func.cast(column, Text),
        else_=DEFAULT_FAMILY,
    )


def incident_family_sql(column: ColumnElement[Any]) -> Case:
    """Lo mismo para una columna `incident_type`. Generado desde INCIDENT_FAMILY."""
    return case(
        {kind.value: family for kind, family in INCIDENT_FAMILY.items()},
        value=func.cast(column, Text),
        else_=DEFAULT_FAMILY,
    )


def por_familia_sql(
    familia: ColumnElement[Any], valores: Mapping[str, Any], *, defecto: Any
) -> Case:
    """`CASE familia WHEN 'fire' THEN … END` con un valor por familia.

    Constante dentro de cada partición del DBSCAN, que es lo que permite darle a
    cada familia su propio `eps` en una sola consulta (probado en PostGIS 3.4).
    Ordenado por familia por la misma razón que `event_family_sql`: el texto de
    la consulta no cambia entre arranques y la caché de planes lo reconoce.
    """
    return case(
        {nombre: literal(valor) for nombre, valor in sorted(valores.items())},
        value=familia,
        else_=literal(defecto),
    )


#: Margen alrededor de las comunas de la región, en grados (~2 km a esta
#: latitud). En grados y no en metros para que el `ST_DWithin` use el índice
#: GIST de `comunas_region` sin castear a `geography`.
MARGEN_REGION_GRADOS = 0.02


def dentro_de_la_region(geom: Any) -> ColumnElement[bool]:
    """¿El punto está en (o a menos de ~2 km de) alguna comuna de la V Región?"""
    return (
        select(literal(1))
        .select_from(ComunaRegion)
        .where(func.ST_DWithin(ComunaRegion.geom, geom, MARGEN_REGION_GRADOS))
        .exists()
    )


def geocoding_precision_sql() -> ColumnElement[Any]:
    """`raw_data._geocoding.precision`: `street`, `sector`… o NULL sin geocodificar."""
    return RawEvent.raw_data["_geocoding"]["precision"].astext


def sector_clave_sql() -> ColumnElement[Any]:
    """`raw_data._extraction.sector_clave` de una señal, como texto.

    La escriben los collectors que leen prosa (prensa, Instagram, X) con
    `lugares.anotar_sector`: "vina del mar|miraflores alto". Vive en
    `_extraction` y no en `_geocoding` a propósito: una nota sin punto también
    la tiene, y es justamente la que más la necesita.
    """
    return RawEvent.raw_data["_extraction"]["sector_clave"].astext


@dataclass(frozen=True, slots=True)
class ClusteredEvent:
    """Una señal con el racimo que le asignó DBSCAN en esta pasada."""

    event_id: int
    cluster_id: int | None
    lat: float
    lon: float
    confidence: float
    timestamp: datetime
    source: EventSource
    type: EventType
    #: Familia de fenómeno: `fire`, `traffic`, `hydro`, `other`. Es la partición
    #: dentro de la cual se calcularon las distancias.
    family: str = DEFAULT_FAMILY
    #: Sector que nombró la fuente, si lo nombró. Ver `sector_clave_sql`.
    sector_clave: str | None = None
    #: Precisión del punto geocodificado (`street`, `sector`…). None = el punto
    #: vino de la fuente (FIRMS, CONAF, GPS) y cuenta como exacto.
    precision: str | None = None

    @property
    def cluster_key(self) -> tuple[str, int | None]:
        """Identidad del racimo **dentro de la pasada**.

        La familia forma parte de la clave y no es un detalle: `ST_ClusterDBSCAN`
        numera desde 0 en CADA partición, así que el racimo 0 de `fire` y el
        racimo 0 de `traffic` son dos cosas distintas que comparten número.
        Agrupar sólo por `cluster_id` volvería a fundir justo lo que la partición
        acaba de separar — y de la forma más silenciosa posible, porque el SQL
        habría hecho su trabajo bien.
        """
        return (self.family, self.cluster_id)


@dataclass(frozen=True, slots=True)
class NearbyIncident:
    incident: Incident
    distance_m: float


@dataclass(frozen=True, slots=True)
class SectorSignal:
    """Una señal SIN punto que nombra un sector. Candidata al vínculo por sector."""

    event_id: int
    type: EventType
    family: str
    sector_clave: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class EventLink:
    """Instrucción de vinculación que el servicio le pasa al repositorio."""

    raw_event_id: int
    link_method: LinkMethod
    link_confidence: float = 1.0
    distance_m: float | None = None
    matched_commune: str | None = None
    note: str | None = None


class IncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Exclusión mutua entre pasadas ---------------------------------------

    async def try_advisory_lock(self, key: int = CORRELATION_LOCK_KEY) -> bool:
        """Toma el lock de la pasada, o devuelve False si ya lo tiene alguien.

        Dos pasadas concurrentes sobre las mismas señales pueden crear dos
        incidentes para el mismo incendio: ambas leen `incident_id IS NULL`
        antes de que la otra escriba. Un advisory lock a nivel de transacción lo
        evita sin tablas de coordinación y se libera solo al commit o al
        rollback —incluso si el proceso muere—, que es exactamente lo que se
        quiere de un lock operativo.

        Con una sola réplica no cambia nada. Con varias, es lo que impide que el
        mapa se llene de incidentes duplicados.
        """
        stmt = select(func.pg_try_advisory_xact_lock(key))
        return bool((await self.session.execute(stmt)).scalar_one())

    # -- Paso A: agrupación espaciotemporal ----------------------------------

    async def cluster_unassigned_events(
        self,
        *,
        since: datetime,
        radius_m: float,
        limit: int,
        utm_srid: int | None = None,
        radios: Mapping[str, float] | None = None,
        edades_desde: Mapping[str, datetime] | None = None,
        solo_region: bool = False,
    ) -> list[ClusteredEvent]:
        """Agrupa con DBSCAN las señales georreferenciadas aún sin incidente.

        `minpoints = 1` hace que toda señal reciba racimo, incluso si está sola:
        decidir si un racimo de uno merece incidente es una regla de negocio del
        servicio (`CORRELATION_MIN_SIGNALS_FOR_INCIDENT`), no de la geometría.

        La ventana temporal es el `WHERE`: DBSCAN agrupa en el espacio, y sólo ve
        señales que ya son contemporáneas por construcción.

        Partición por familia
        ---------------------
        El `PARTITION BY familia` de la ventana es lo que impide que un choque y
        un incendio se fundan en un mismo incidente por ocurrir en la misma
        esquina. La coincidencia espaciotemporal entre fenómenos distintos es
        justamente lo esperable en una ciudad —un accidente en la Ruta 68 y una
        quema agrícola al lado del camino comparten coordenada y minuto sin tener
        absolutamente nada que ver— y sin la partición DBSCAN los agruparía
        porque sólo mira distancias.

        Dentro de una familia el agrupamiento sigue intacto: `smoke`,
        `thermal_anomaly` y `wildfire` caen todos en `fire` y se corroboran entre
        sí, que es de lo que vive este motor. Lo que se separa son fenómenos, no
        grados de certeza sobre el mismo fenómeno.

        **`ST_ClusterDBSCAN` reinicia la numeración en cada partición.** El
        `cluster_id` sólo es único dentro de su familia; quien consuma esto debe
        agrupar por `ClusteredEvent.cluster_key`.

        Perfiles por familia (`CORRELATION_PERFILES`)
        ---------------------------------------------
        Con `radios`, cada familia agrupa con su propio `eps` (el `CASE` es
        constante dentro de la partición). Con `edades_desde`, además de lo que
        OCURRIÓ en la ventana entra lo que LLEGÓ en la ventana (`ingested_at`),
        siempre que no sea más viejo que la edad máxima de su familia: FIRMS
        publica ~3 h tarde, y antes eso quedaba sin incidente para siempre. Sin
        los dos parámetros, la consulta es exactamente la de antes.

        Sólo la región (`solo_region`)
        ------------------------------
        La caja de ingesta (`REGION_*`) cubre media Región Metropolitana: en la
        semana del 19 al 29 de septiembre, 241 de 403 señales con punto caían
        fuera de la V Región —cortes de CGE en Puente Alto y La Florida, focos
        de FIRMS en Colina— y eran los incidentes «sin comuna» del mapa. Con
        `solo_region`, sólo se agrupan señales a menos de
        `MARGEN_REGION_GRADOS` de alguna comuna de `comunas_region`: el margen
        deja pasar un punto geocodificado sobre el muelle o la playa, que el
        polígono de la BCN no siempre cubre. Lo de afuera queda como
        `raw_event` consultable, sin incidente.
        """
        srid = utm_srid or settings.CORRELATION_UTM_SRID
        familia_evento = event_family_sql(RawEvent.type)

        if edades_desde:
            # Lo que llegó tarde también entra, con su edad acotada por familia.
            # Los valores del CASE son instantes (ahora - edad_max), así que la
            # comparación es una sola y el índice de `ingested_at` hace el resto.
            ventana = or_(
                RawEvent.timestamp >= since,
                and_(
                    RawEvent.ingested_at >= since,
                    RawEvent.timestamp
                    >= por_familia_sql(
                        familia_evento, edades_desde, defecto=min(edades_desde.values())
                    ),
                ),
            )
        else:
            ventana = RawEvent.timestamp >= since

        candidates = (
            select(
                RawEvent.id.label("id"),
                RawEvent.lat.label("lat"),
                RawEvent.lon.label("lon"),
                RawEvent.confidence.label("confidence"),
                RawEvent.timestamp.label("timestamp"),
                RawEvent.source.label("source"),
                RawEvent.type.label("type"),
                RawEvent.geom.label("geom"),
                familia_evento.label("family"),
                sector_clave_sql().label("sector_clave"),
                geocoding_precision_sql().label("precision"),
            )
            .where(RawEvent.geom.isnot(None))
            .where(RawEvent.incident_id.is_(None))
            .where(ventana)
            .where(dentro_de_la_region(RawEvent.geom) if solo_region else sa_true())
            .where(RawEvent.type.in_(sorted(CORRELATABLE_EVENT_TYPES, key=lambda t: t.value)))
            # Las señales más creíbles primero: si el tope de la pasada corta la
            # lista, que lo que se quede afuera sea lo menos informativo.
            .order_by(RawEvent.confidence.desc(), RawEvent.timestamp.asc())
            .limit(limit)
            .cte("candidatos")
        )

        eps: Any = (
            por_familia_sql(candidates.c.family, radios, defecto=radius_m)
            if radios
            else radius_m
        )
        cluster_id = func.ST_ClusterDBSCAN(
            func.ST_Transform(candidates.c.geom, srid), eps, 1
        ).over(partition_by=candidates.c.family)

        stmt = select(
            candidates.c.id,
            candidates.c.lat,
            candidates.c.lon,
            candidates.c.confidence,
            candidates.c.timestamp,
            candidates.c.source,
            candidates.c.type,
            candidates.c.family,
            candidates.c.sector_clave,
            candidates.c.precision,
            cluster_id.label("cluster_id"),
        )

        rows = (await self.session.execute(stmt)).all()
        return [
            ClusteredEvent(
                event_id=row.id,
                cluster_id=row.cluster_id,
                lat=row.lat,
                lon=row.lon,
                confidence=float(row.confidence),
                timestamp=row.timestamp,
                source=EventSource(getattr(row.source, "value", row.source)),
                type=EventType(getattr(row.type, "value", row.type)),
                family=str(row.family),
                sector_clave=row.sector_clave or None,
                precision=row.precision or None,
            )
            for row in rows
        ]

    # -- Vínculo por sector ---------------------------------------------------

    def open_incident_by_sector_stmt(
        self, *, sector_clave: str, family: str, since: datetime
    ) -> Select:
        """La consulta de `find_open_incident_by_sector`, separada para testearla.

        `@>` (`contains`) y no `->> =`: es lo que puede usar el índice GIN
        `jsonb_path_ops` de `raw_data`.
        """
        return (
            select(Incident)
            .join(IncidentEvent, IncidentEvent.incident_id == Incident.id)
            .join(RawEvent, RawEvent.id == IncidentEvent.raw_event_id)
            .where(Incident.status.in_(_open_statuses()))
            .where(Incident.last_seen_at >= since)
            .where(incident_family_sql(Incident.type) == family)
            .where(
                RawEvent.raw_data.contains({"_extraction": {"sector_clave": sector_clave}})
            )
            # El más reciente: si hubo dos incendios en el sector el mismo día,
            # el que sigue vivo es el que corresponde. A igualdad, el más
            # antiguo, que es el que sobreviviría a una fusión.
            .order_by(Incident.last_seen_at.desc(), Incident.id.asc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    async def find_open_incident_by_sector(
        self, *, sector_clave: str, family: str, since: datetime
    ) -> Incident | None:
        """Incidente abierto de la familia cuyas señales nombran este sector.

        Es la cuarta puerta del aislamiento entre familias, y por eso filtra por
        familia igual que las otras tres: que un choque y un incendio ocurran en
        el mismo sector la misma tarde no los vuelve el mismo hecho.
        """
        stmt = self.open_incident_by_sector_stmt(
            sector_clave=sector_clave, family=family, since=since
        )
        return (await self.session.execute(stmt)).scalars().first()

    def unlocated_sector_signals_stmt(self, *, since: datetime, limit: int) -> Select:
        """La consulta de `unlocated_sector_signals`, separada para testearla."""
        clave = sector_clave_sql()
        return (
            select(
                RawEvent.id.label("id"),
                RawEvent.type.label("type"),
                RawEvent.timestamp.label("timestamp"),
                event_family_sql(RawEvent.type).label("family"),
                clave.label("sector_clave"),
            )
            .where(RawEvent.geom.is_(None))
            .where(RawEvent.incident_id.is_(None))
            .where(RawEvent.timestamp >= since)
            .where(RawEvent.type.in_(sorted(CORRELATABLE_EVENT_TYPES, key=lambda t: t.value)))
            .where(clave.isnot(None))
            .where(clave != "")
            .order_by(RawEvent.timestamp.asc())
            .limit(limit)
        )

    async def unlocated_sector_signals(
        self, *, since: datetime, limit: int
    ) -> list[SectorSignal]:
        """Señales sin punto, aún sin incidente, que nombran un sector.

        Son las que el Paso A no puede ver —filtra por `geom IS NOT NULL`— y que
        sin esto se quedaban para siempre fuera del mapa aunque otra fuente
        estuviera contando el mismo hecho en el mismo sector.
        """
        stmt = self.unlocated_sector_signals_stmt(since=since, limit=limit)
        rows = (await self.session.execute(stmt)).all()
        return [
            SectorSignal(
                event_id=row.id,
                type=EventType(getattr(row.type, "value", row.type)),
                family=str(row.family),
                sector_clave=str(row.sector_clave),
                timestamp=row.timestamp,
            )
            for row in rows
        ]

    async def find_nearest_open_incident(
        self,
        *,
        lat: float,
        lon: float,
        radius_m: float,
        since: datetime,
        family: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> NearbyIncident | None:
        """Incidente abierto más cercano dentro del radio y aún vivo.

        Con `desde`/`hasta`, además, el incidente tuvo que estar vivo CERCA DEL
        MOMENTO de la señal —`last_seen_at >= desde` y `first_seen_at <= hasta`—
        y no sólo «en las últimas horas». Sin esto, una nota de prensa de las
        15:00 se adhería a un choque de las 09:00 a 900 m (auditoría 2026-09-23).

        `family` es la segunda mitad del aislamiento entre fenómenos. Particionar
        el DBSCAN separa los racimos nuevos entre sí, pero un racimo de
        accidentes todavía podría adherirse a un incendio que ya existe en esa
        esquina —y ahí la contaminación es peor, porque el incidente de incendio
        ya tiene folio, confianza y quizá una alerta de SENAPRED colgando—.
        Se deja opcional para no romper a quien busque el incidente más cercano
        sin importar su tipo.
        """
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        distance = func.ST_Distance(
            func.cast(Incident.geom, _GEOGRAPHY), func.cast(point, _GEOGRAPHY)
        )

        stmt = (
            select(Incident, distance.label("distance_m"))
            .where(Incident.status.in_(_open_statuses()))
            .where(Incident.last_seen_at >= since)
            .where(
                func.ST_DWithin(
                    func.cast(Incident.geom, _GEOGRAPHY),
                    func.cast(point, _GEOGRAPHY),
                    radius_m,
                )
            )
            .order_by(distance.asc())
            .limit(1)
            # El motor actualiza incidentes con sentencias Core dentro de la
            # misma pasada. Sin `populate_existing`, SQLAlchemy devolvería la
            # instancia que ya tiene en el mapa de identidad con sus valores
            # viejos y el motor recentraría el incidente sobre datos rancios.
            .execution_options(populate_existing=True)
        )
        if family is not None:
            stmt = stmt.where(incident_family_sql(Incident.type) == family)
        if desde is not None:
            stmt = stmt.where(Incident.last_seen_at >= desde)
        if hasta is not None:
            stmt = stmt.where(Incident.first_seen_at <= hasta)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return NearbyIncident(incident=row[0], distance_m=float(row.distance_m))

    async def get_by_id(self, incident_id: int) -> Incident | None:
        stmt = (
            select(Incident)
            .where(Incident.id == incident_id)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # -- Territorio -------------------------------------------------------------

    async def comunas_disponibles(self) -> bool:
        """¿Existe `comunas_region`? Sin la migración 0015 el motor sigue igual."""
        tabla = f"{ComunaRegion.__table__.schema}.{ComunaRegion.__tablename__}"
        stmt = select(func.to_regclass(tabla).isnot(None))
        return bool((await self.session.execute(stmt)).scalar_one())

    async def comuna_por_punto(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        """`(comuna, provincia)` del polígono que contiene el punto, o `(None, None)`.

        El último recurso de `CorrelationEngine._refresh`: sólo se consulta
        cuando ninguna señal dijo la comuna. En los bordes la capa tiene solapes
        de decenas de metros (Los Andes); gana el menor CUT, igual que en el
        relleno de la migración 0015, para que el resultado no dependa del plan.
        """
        punto = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = (
            select(ComunaRegion.nombre, ComunaRegion.provincia)
            .where(func.ST_Covers(ComunaRegion.geom, punto))
            .order_by(ComunaRegion.cut)
            .limit(1)
        )
        fila = (await self.session.execute(stmt)).first()
        return (fila.nombre, fila.provincia) if fila else (None, None)

    # -- Escritura de incidentes ---------------------------------------------

    async def create_incident(self, **values: Any) -> Incident:
        """Inserta un incidente. `code` y `geom` los pone la base."""
        incident = Incident(**values)
        self.session.add(incident)
        await self.session.flush()
        await self.session.refresh(incident)
        return incident

    async def link_events(
        self, *, incident_id: int, links: Sequence[EventLink]
    ) -> int:
        """Vincula señales a un incidente de forma idempotente.

        Reejecutar el motor sobre la misma ventana no duplica enlaces: la clave
        primaria es `(incident_id, raw_event_id)` y el conflicto sólo refresca
        los metadatos del vínculo.
        """
        if not links:
            return 0

        rows = [
            {
                "incident_id": incident_id,
                "raw_event_id": link.raw_event_id,
                "link_method": link.link_method,
                "link_confidence": link.link_confidence,
                "distance_m": link.distance_m,
                "matched_commune": link.matched_commune,
                "note": link.note,
            }
            for link in links
        ]

        stmt = pg_insert(IncidentEvent).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[IncidentEvent.incident_id, IncidentEvent.raw_event_id],
            set_={
                "link_method": stmt.excluded.link_method,
                "link_confidence": stmt.excluded.link_confidence,
                "distance_m": stmt.excluded.distance_m,
                "matched_commune": stmt.excluded.matched_commune,
                "note": stmt.excluded.note,
            },
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def assign_events_to_incident(
        self, *, incident_id: int, event_ids: Sequence[int], processed_at: datetime
    ) -> int:
        """Fija el puntero desnormalizado `raw_events.incident_id`.

        Para vínculos espaciales y por sector: los dos son 1:1 —una señal
        pertenece a un solo incidente—. Una alerta comunal puede pertenecer a
        varios incidentes y por eso vive únicamente en la tabla intermedia.
        """
        if not event_ids:
            return 0
        stmt = (
            update(RawEvent)
            .where(RawEvent.id.in_(list(event_ids)))
            .values(incident_id=incident_id, processed_at=processed_at)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def update_incident(self, incident_id: int, **values: Any) -> None:
        if not values:
            return
        await self.session.execute(
            update(Incident).where(Incident.id == incident_id).values(**values)
        )

    async def recompute_geometry(self, incident_id: int) -> tuple[float, float] | None:
        """Centroide ponderado por confianza de las señales espaciales.

        Ponderar no es un adorno: cuando un incidente tiene un punto de CONAF y
        seis píxeles de VIIRS repartidos por la ladera, el centro sin ponderar
        se va cerro arriba y el mapa deja de coincidir con el lugar que el
        organismo reportó.
        """
        weight = func.greatest(RawEvent.confidence, _MIN_WEIGHT)
        stmt = (
            select(
                (func.sum(RawEvent.lat * weight) / func.sum(weight)).label("lat"),
                (func.sum(RawEvent.lon * weight) / func.sum(weight)).label("lon"),
            )
            .select_from(IncidentEvent)
            .join(RawEvent, RawEvent.id == IncidentEvent.raw_event_id)
            .where(IncidentEvent.incident_id == incident_id)
            .where(IncidentEvent.link_method == LinkMethod.SPATIAL)
            .where(RawEvent.geom.isnot(None))
        )
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None or row.lat is None or row.lon is None:
            return None
        return (float(row.lat), float(row.lon))

    # -- Lectura de señales de un incidente ----------------------------------

    async def signals_of(self, incident_id: int) -> Sequence[RawEvent]:
        """Todas las señales del incidente, por cualquier método de vínculo."""
        stmt = (
            select(RawEvent)
            .join(IncidentEvent, IncidentEvent.raw_event_id == RawEvent.id)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(RawEvent.confidence.desc(), RawEvent.timestamp.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()


    # -- Paso B: alertas sin geometría ---------------------------------------

    async def vigent_alerts(
        self, *, updated_since: datetime, sources: Sequence[EventSource] | None = None
    ) -> Sequence[RawEvent]:
        """Alertas vigentes sin coordenadas.

        La vigencia NO se mide con `timestamp`, que es la fecha de declaración y
        puede ser de hace días: se mide con `updated_at`, porque el upsert lo
        refresca cada vez que la capa vuelve a publicar la alerta. Que la fila
        siga tocándose *es* su vigencia. Cuando SENAPRED la levanta, la alerta
        desaparece de la capa, deja de refrescarse y sale sola de esta consulta.
        """
        stmt = (
            select(RawEvent)
            .where(RawEvent.geom.is_(None))
            .where(RawEvent.type.in_([EventType.ALERT, EventType.EVACUATION]))
            .where(RawEvent.updated_at >= updated_since)
            .order_by(RawEvent.updated_at.desc())
        )
        if sources:
            stmt = stmt.where(RawEvent.source.in_(list(sources)))
        return (await self.session.execute(stmt)).scalars().all()

    async def open_incidents(
        self, *, since: datetime | None = None, limit: int = 1000
    ) -> Sequence[Incident]:
        stmt = (
            select(Incident)
            .where(Incident.status.in_(_open_statuses()))
            .order_by(Incident.last_seen_at.desc())
            .limit(limit)
            # El Paso B lee la comuna que el Paso A acaba de escribir con una
            # sentencia Core, en la misma transacción: hay que releer la fila.
            .execution_options(populate_existing=True)
        )
        if since is not None:
            stmt = stmt.where(Incident.last_seen_at >= since)
        return (await self.session.execute(stmt)).scalars().all()

    async def drop_links_by_method(
        self, *, method: LinkMethod, incident_ids: Sequence[int] | None = None
    ) -> int:
        """Borra los vínculos de un método concreto.

        El Paso B se recalcula entero en cada pasada: una alerta levantada tiene
        que dejar de teñir el incidente, y la forma más simple y auditable de
        conseguirlo es reconstruir sus enlaces en vez de intentar caducarlos uno
        a uno. Los vínculos espaciales no se tocan.
        """
        stmt = delete(IncidentEvent).where(IncidentEvent.link_method == method)
        if incident_ids is not None:
            stmt = stmt.where(IncidentEvent.incident_id.in_(list(incident_ids)))
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    # -- Ciclo de vida --------------------------------------------------------

    async def mark_stale(self, *, threshold: datetime) -> int:
        """Incidentes activos sin señales nuevas → `stale`.

        Sólo se degradan los `active`. Un `controlled` lo declaró una fuente
        institucional y ese dato no se pisa por silencio: `stale` significa "no
        llegan señales", no "se apagó".
        """
        stmt = (
            update(Incident)
            .where(Incident.status == IncidentStatus.ACTIVE)
            .where(Incident.last_seen_at < threshold)
            .values(status=IncidentStatus.STALE)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def expire_uncorroborated_citizen(
        self,
        *,
        older_than: datetime,
        citizen_sources: Sequence[EventSource] | None = None,
    ) -> int:
        """Descarta incidentes sólo ciudadanos que no juntaron el quórum a tiempo.

        Es la defensa de fondo contra el reporte falso. Hasta el 2026-09-30 la
        condición era «confianza ≤ 0,40», y tenía un hueco: dos reportes (el
        mismo teléfono con 4G y después con wifi) sumaban 0,58, se salían de la
        regla y el incidente falso quedaba en el mapa hasta 12 horas. Ahora la
        condición es **no publicado**: el motor sólo marca `publico` a lo sólo
        ciudadano que juntó `CITIZEN_QUORUM` vecinos independientes, y eso no
        se consigue repitiendo el reporte.

        Cuatro condiciones, y las cuatro tienen que cumplirse:

        1. **Ninguna fuente ajena al reporte ciudadano.** `sources <@
           ARRAY['citizen']`: basta un píxel de FIRMS, un incendio de CONAF o un
           reporte de Waze para quedar fuera.
        2. **No confirmado en terreno.** Redundante con la anterior por
           construcción, y por eso vale: dos candados independientes.
        3. **No publicado.** Lo que ya juntó quórum sigue su propio plazo
           (`stale_citizen_only`), no éste.
        4. **Edad medida desde `first_seen_at`.** No desde `last_seen_at`: quien
           repite el reporte cada pocos minutos refrescaría `last_seen_at` y su
           incidente no moriría nunca. `first_seen_at` no se puede reiniciar.

        Se marca `DISMISSED` y no `STALE`. `STALE` significa "dejaron de llegar
        señales"; esto es "nunca hubo evidencia suficiente". Confundirlos haría
        que un operador leyera como incendio apagado lo que fue un reporte
        descartado.
        """
        sources = list(citizen_sources or [EventSource.CITIZEN])
        etiquetas = [source.value for source in sources]

        stmt = (
            update(Incident)
            .where(Incident.status.in_(_open_statuses()))
            .where(Incident.first_seen_at < older_than)
            .where(Incident.publico.is_(False))
            # Una fuente institucional que fue al lugar jamás se descarta por
            # tiempo, pase lo que pase con las otras condiciones.
            .where(Incident.is_official_confirmed.is_(False))
            # `sources <@ ARRAY[...]`: "todo lo que hay está contenido en".
            .where(Incident.sources.contained_by(etiquetas))
            .values(status=IncidentStatus.DISMISSED)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def stale_citizen_only(self, *, threshold: datetime) -> int:
        """Lo sólo ciudadano ya publicado, sin reportes nuevos → `stale`.

        Pasa antes que el resto (`CITIZEN_ONLY_STALE_HOURS`, 3 h, contra las 12
        de `mark_stale`) porque ninguna fuente oficial lo sostiene.
        """
        stmt = (
            update(Incident)
            .where(Incident.status == IncidentStatus.ACTIVE)
            .where(Incident.last_seen_at < threshold)
            .where(Incident.is_official_confirmed.is_(False))
            .where(Incident.sources.contained_by([EventSource.CITIZEN.value]))
            .values(status=IncidentStatus.STALE)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def count_recent_citizen_reports(self, *, since: datetime) -> int:
        """Reportes ciudadanos recibidos desde `since`, en toda la región.

        El `timestamp` de un reporte lo fija el servidor al recibirlo, así que
        acá mide llegada. Usa `ix_raw_events_source_timestamp`.
        """
        stmt = (
            select(func.count())
            .select_from(RawEvent)
            .where(RawEvent.source == EventSource.CITIZEN)
            .where(RawEvent.timestamp >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one() or 0)

    async def find_mergeable(
        self,
        *,
        radius_m: float,
        since: datetime,
        radios: Mapping[str, float] | None = None,
        brechas: Mapping[str, timedelta] | None = None,
    ) -> list[tuple[int, int]]:
        """Pares de incidentes abiertos que convergieron.

        Devuelve `(superviviente, absorbido)`. Sobrevive el más antiguo: es el
        que ya tiene folio circulando por radio.

        Sólo funde incidentes de la **misma familia**. Es la tercera y última
        puerta del aislamiento: sin esta condición, un incendio y un accidente
        que nacieron separados —porque la partición del Paso A hizo su trabajo—
        se reunirían igual acá al crecer uno hacia el otro, y el resultado sería
        idéntico a no haber particionado nada.

        Con `radios`, el radio es el de la familia; con `brechas`, además, los
        dos incidentes tienen que haberse solapado en el tiempo (con esa
        tolerancia): dos choques en la misma esquina con cinco horas de
        diferencia son dos choques, aunque los dos sigan «abiertos».
        """
        left = Incident.__table__.alias("a")
        right = Incident.__table__.alias("b")
        familia = incident_family_sql(left.c.type)
        radio: Any = (
            por_familia_sql(familia, radios, defecto=radius_m) if radios else radius_m
        )

        # `keep_id`/`drop_id` y no `keep`/`drop`: `drop` como alias desnudo es
        # pedirle problemas al parser por una comodidad de dos caracteres.
        stmt = (
            select(left.c.id.label("keep_id"), right.c.id.label("drop_id"))
            .select_from(
                left.join(
                    right,
                    func.ST_DWithin(
                        func.cast(left.c.geom, _GEOGRAPHY),
                        func.cast(right.c.geom, _GEOGRAPHY),
                        radio,
                    ),
                )
            )
            .where(left.c.id < right.c.id)
            .where(left.c.status.in_(_open_statuses()))
            .where(right.c.status.in_(_open_statuses()))
            .where(left.c.last_seen_at >= since)
            .where(right.c.last_seen_at >= since)
            .where(familia == incident_family_sql(right.c.type))
            .order_by(left.c.id.asc(), right.c.id.asc())
        )
        if brechas:
            brecha = por_familia_sql(familia, brechas, defecto=max(brechas.values()))
            stmt = stmt.where(left.c.last_seen_at >= right.c.first_seen_at - brecha)
            stmt = stmt.where(right.c.last_seen_at >= left.c.first_seen_at - brecha)
        rows = (await self.session.execute(stmt)).all()
        return [(row.keep_id, row.drop_id) for row in rows]

    async def merge(self, *, keep_id: int, drop_id: int) -> None:
        """Absorbe `drop_id` dentro de `keep_id` sin perder trazabilidad.

        El incidente absorbido no se borra: queda en `merged` apuntando a su
        sucesor, de modo que un folio ya comunicado sigue resolviendo a algo.
        """
        already_linked = select(IncidentEvent.raw_event_id).where(
            IncidentEvent.incident_id == keep_id
        )
        # UPDATE en vez de INSERT+DELETE: mover la fila no puede chocar con el
        # índice único parcial de vínculos espaciales, porque no la duplica.
        await self.session.execute(
            update(IncidentEvent)
            .where(IncidentEvent.incident_id == drop_id)
            .where(IncidentEvent.raw_event_id.notin_(already_linked))
            .values(incident_id=keep_id)
        )
        await self.session.execute(
            delete(IncidentEvent).where(IncidentEvent.incident_id == drop_id)
        )
        await self.session.execute(
            update(RawEvent)
            .where(RawEvent.incident_id == drop_id)
            .values(incident_id=keep_id)
        )
        await self.session.execute(
            update(Incident)
            .where(Incident.id == drop_id)
            .values(status=IncidentStatus.MERGED, merged_into_id=keep_id)
        )

    # -- Lectura para la API --------------------------------------------------

    def _apply_filters(
        self,
        stmt: Select,
        *,
        since: datetime | None,
        statuses: Sequence[IncidentStatus] | None,
        types: Sequence[IncidentType] | None,
        min_confidence: float | None,
        commune: str | None,
        bbox: tuple[float, float, float, float] | None,
        confirmed_only: bool,
        with_alert_only: bool,
    ) -> Select:
        if since is not None:
            stmt = stmt.where(Incident.last_seen_at >= since)
        stmt = stmt.where(
            Incident.status.in_(list(statuses) if statuses else _open_statuses())
        )
        # Lo sólo ciudadano sin quórum no sale por la API pública. Ver
        # `app.services.ciudadanos.es_publico`.
        stmt = stmt.where(Incident.publico.is_(True))
        if types:
            stmt = stmt.where(Incident.type.in_(list(types)))
        if min_confidence is not None:
            # Mismo defecto que en `expire_uncorroborated_citizen`, en la otra
            # dirección: `>= 0.35` esconde los incidentes que valen exactamente
            # 0.35, porque float4 los guarda como 0.3499999940395355.
            stmt = stmt.where(confidence_at_least(Incident.confidence, min_confidence))
        if commune:
            stmt = stmt.where(Incident.commune.ilike(f"%{commune}%"))
        if confirmed_only:
            stmt = stmt.where(Incident.is_official_confirmed.is_(True))
        if with_alert_only:
            stmt = stmt.where(Incident.alert_level.isnot(None))
        if bbox is not None:
            west, south, east, north = bbox
            stmt = stmt.where(
                func.ST_Intersects(
                    Incident.geom, func.ST_MakeEnvelope(west, south, east, north, 4326)
                )
            )
        return stmt

    async def list_incidents(
        self,
        *,
        since: datetime | None = None,
        statuses: Sequence[IncidentStatus] | None = None,
        types: Sequence[IncidentType] | None = None,
        min_confidence: float | None = None,
        commune: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        confirmed_only: bool = False,
        with_alert_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[Incident]:
        stmt = self._apply_filters(
            select(Incident),
            since=since,
            statuses=statuses,
            types=types,
            min_confidence=min_confidence,
            commune=commune,
            bbox=bbox,
            confirmed_only=confirmed_only,
            with_alert_only=with_alert_only,
        )
        # Orden pensado para el mapa: primero lo confirmado, luego lo más
        # creíble, y a igualdad de confianza lo más reciente.
        stmt = (
            stmt.order_by(
                Incident.is_official_confirmed.desc(),
                Incident.confidence.desc(),
                Incident.last_seen_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()


    async def get_by_code(self, code: str) -> Incident | None:
        stmt = select(Incident).where(Incident.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_public_id(self, public_id: UUID) -> Incident | None:
        stmt = select(Incident).where(Incident.public_id == public_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


    async def links_with_events(
        self, incident_id: int
    ) -> list[tuple[IncidentEvent, RawEvent]]:
        """Señales del incidente junto al motivo de cada vínculo.

        Una sola consulta: la vista de detalle muestra ambas cosas siempre, y
        pedirlas por separado sería un N+1 disfrazado de prolijidad.
        """
        stmt = (
            select(IncidentEvent, RawEvent)
            .join(RawEvent, RawEvent.id == IncidentEvent.raw_event_id)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(RawEvent.confidence.desc(), RawEvent.timestamp.asc())
        )
        return [(link, event) for link, event in (await self.session.execute(stmt)).all()]

    async def outage_details(
        self, incident_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """Metadatos de corte por incidente, en UNA consulta.

        Un `IN (...)` sobre los incidentes ya cargados en vez de una consulta
        por ficha: la lista de `/incidents/active` puede traer decenas de cortes
        y el N+1 se notaría en la primera tormenta.

        Los campos salen de `raw_events.raw_data`, donde los deja
        `collectors/power/base_worker.py`. Se leen las claves planas
        (`affected_clients`, `restoration_at`, `company`) y no el objeto anidado
        `_outage`, porque el worker las escribe planas exactamente para esto.

        Criterios de agregación, que no son obvios:

        * **clientes: se suman.** Cada `raw_event` es un corte distinto —el
          upsert por `external_id` impide que un mismo corte entre dos veces—,
          así que sumar es contar clientes, no contarlos doble.
        * **reposición: la más tardía.** Un incidente con varios frentes está
          resuelto cuando vuelve el último, no el primero.
        * **proveedor: el de la señal más reciente.** Si Chilquinta y CGE
          reportan el mismo corte, gana quien lo actualizó al final.
        * **visto: el más reciente.** `raw_events.updated_at` lo mueve el
          trigger en cada upsert, y el collector vuelve a escribir cada corte
          que la empresa sigue listando. Así que el `updated_at` más nuevo del
          grupo dice cuándo la empresa lo publicó por última vez. Lo compara
          con la última lectura del collector `IncidentService` (vigencia).
        """
        if not incident_ids:
            return {}

        rows = (
            await self.session.execute(
                select(
                    IncidentEvent.incident_id,
                    RawEvent.source,
                    RawEvent.timestamp,
                    RawEvent.raw_data,
                    RawEvent.updated_at,
                )
                .join(RawEvent, RawEvent.id == IncidentEvent.raw_event_id)
                .where(
                    IncidentEvent.incident_id.in_(list(incident_ids)),
                    RawEvent.type == EventType.POWER_OUTAGE,
                )
                .order_by(IncidentEvent.incident_id, RawEvent.timestamp.asc())
            )
        ).all()

        details: dict[int, dict[str, Any]] = {}

        for incident_id, source, _timestamp, raw_data, updated_at in rows:
            data = raw_data or {}
            entry = details.setdefault(
                incident_id,
                {
                    "provider": source.value if hasattr(source, "value") else str(source),
                    "affected_clients": None,
                    "estimated_restoration": None,
                    "sector": None,
                    "outage_count": 0,
                    "visto_en": None,
                    "collector": None,
                },
            )
            if isinstance(updated_at, datetime) and (
                entry["visto_en"] is None or updated_at > entry["visto_en"]
            ):
                entry["visto_en"] = updated_at
                collector = data.get("_collector")
                entry["collector"] = collector if isinstance(collector, str) else None
            # Las filas vienen ordenadas por timestamp ascendente, así que la
            # última en sobrescribir es la más reciente.
            entry["provider"] = (
                data.get("company")
                or (source.value if hasattr(source, "value") else str(source))
            )
            entry["outage_count"] += 1

            clients = data.get("affected_clients")
            if isinstance(clients, int):
                entry["affected_clients"] = (entry["affected_clients"] or 0) + clients

            restoration = data.get("restoration_at")
            if isinstance(restoration, str) and restoration:
                current = entry["estimated_restoration"]
                if current is None or restoration > current:
                    entry["estimated_restoration"] = restoration

            outage = data.get("_outage")
            if isinstance(outage, dict) and outage.get("sector"):
                entry["sector"] = outage["sector"]

        return details

    async def ultimas_lecturas(self, collectors: Sequence[str]) -> dict[str, datetime]:
        """Inicio de la última corrida que LEYÓ la fuente, por collector.

        `success` y `partial` leyeron; `failed` no. Es la referencia de la
        vigencia de los cortes de luz: vigente = la empresa lo volvió a listar
        en esa corrida. Si el collector cae, la referencia no avanza y el mapa
        sigue mostrando lo último que se supo, igual que el agua.
        """
        wanted = sorted({c for c in collectors if c})
        if not wanted:
            return {}
        stmt = (
            select(CollectorRun.collector, func.max(CollectorRun.started_at))
            .where(
                CollectorRun.collector.in_(wanted),
                CollectorRun.status.in_(
                    [CollectorStatus.SUCCESS.value, CollectorStatus.PARTIAL.value]
                ),
            )
            .group_by(CollectorRun.collector)
        )
        return {
            collector: started_at
            for collector, started_at in (await self.session.execute(stmt)).all()
            if started_at is not None
        }

    async def stats(self, *, since: datetime | None = None) -> dict[str, Any]:
        base = select(Incident)
        if since is not None:
            base = base.where(Incident.last_seen_at >= since)
        subq = base.subquery()

        totals = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(subq.c.is_official_confirmed.is_(True))
                    .label("confirmed"),
                    func.count().filter(subq.c.alert_level.isnot(None)).label("alerted"),
                    func.avg(subq.c.confidence).label("avg_confidence"),
                    func.max(subq.c.last_seen_at).label("last_seen_at"),
                ).select_from(subq)
            )
        ).one()

        by_status = (
            await self.session.execute(
                select(subq.c.status, func.count()).group_by(subq.c.status)
            )
        ).all()
        by_type = (
            await self.session.execute(
                select(subq.c.type, func.count()).group_by(subq.c.type)
            )
        ).all()

        def _key(value: Any) -> str:
            return value.value if hasattr(value, "value") else str(value)

        return {
            "total": totals.total or 0,
            "confirmed": totals.confirmed or 0,
            "with_official_alert": totals.alerted or 0,
            "avg_confidence": (
                round(float(totals.avg_confidence), 4)
                if totals.avg_confidence is not None
                else None
            ),
            "last_seen_at": totals.last_seen_at,
            "by_status": {_key(status): count for status, count in by_status},
            "by_type": {_key(kind): count for kind, count in by_type},
        }


def _open_statuses() -> list[IncidentStatus]:
    return sorted(OPEN_INCIDENT_STATUSES, key=lambda item: item.value)


__all__ = [
    "ClusteredEvent",
    "EventLink",
    "IncidentRepository",
    "NearbyIncident",
    "SectorSignal",
    "event_family_sql",
    "incident_family_sql",
    "sector_clave_sql",
]
