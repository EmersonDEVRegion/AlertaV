"""Lo que sólo se puede probar contra Postgres de verdad.

Saltados por defecto: la suite corre sin base. Para ejercitarlos, contra una
base desechable con `alembic upgrade head` aplicado:

    ALERTAV_PG_INTEGRACION=1 POSTGRES_PORT=5433 POSTGRES_DB=alertav_test \\
        pytest tests/test_integracion_pg.py

Cubren el SQL crudo que los dobles de los demás tests no pueden validar: el
reclamo del inbox con `FOR UPDATE SKIP LOCKED`, el abandono tras
`INBOX_MAX_INTENTOS` y el upsert que no borra coordenadas.

**Nunca contra producción**: cada test borra las filas que toca.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("ALERTAV_PG_INTEGRACION") != "1",
    reason="integración con Postgres: ALERTAV_PG_INTEGRACION=1 y una base desechable",
)

DATASET = "datasetDeIntegracion"


def correr(corrutina_factory):
    """Una base de eventos por test, y el pool cerrado dentro del mismo loop."""
    from app.core.database import dispose_engine

    async def envoltura():
        try:
            return await corrutina_factory()
        finally:
            await dispose_engine()

    return asyncio.run(envoltura())


async def _limpiar() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource
    from app.models.event import CollectorRun, RawEvent
    from app.services import apify_webhook_service as svc

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(CollectorRun).where(CollectorRun.collector == svc.COLLECTOR_NAME)
        )
        await session.execute(
            delete(RawEvent).where(
                RawEvent.source == EventSource.TRANSPORTE_INFORMA,
                RawEvent.external_id.like("integracion:%"),
            )
        )
        await session.commit()


async def _fila(run_id: int):
    from app.core.database import AsyncSessionLocal
    from app.models.event import CollectorRun

    async with AsyncSessionLocal() as session:
        return await session.get(CollectorRun, run_id)


async def _envejecer(run_id: int, *, minutos: int, intentos: int | None = None) -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.event import CollectorRun

    tabla = CollectorRun.__table__.fullname
    extra = f", 'intentos', {int(intentos)}" if intentos is not None else ""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                f"UPDATE {tabla} SET params = params || jsonb_build_object("
                f"'reclamado_en', now() - make_interval(mins => {int(minutos)}){extra}) "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )
        await session.commit()


# --- El inbox -----------------------------------------------------------------


def test_encolar_es_idempotente_por_dataset():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        primera = await svc.encolar_dataset(DATASET, {"resource": {"id": "r1"}})
        segunda = await svc.encolar_dataset(DATASET, {"resource": {"id": "r1"}})
        return primera, segunda

    assert correr(caso) == (True, False)


def test_dos_reclamos_simultaneos_no_toman_la_misma_entrega():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        return await asyncio.gather(svc.reclamar_siguiente(), svc.reclamar_siguiente())

    a, b = correr(caso)
    tomados = [r for r in (a, b) if r is not None]
    assert len(tomados) == 1, "SKIP LOCKED: uno la toma, el otro sigue de largo"
    run_id, dataset, _traza, intento, _canario = tomados[0]
    assert dataset == DATASET
    assert intento == 1

    fila = correr(lambda: _fila(run_id))
    assert fila.params["inbox"] == svc.INBOX_EN_PROCESO
    assert fila.status == "running"


def test_una_entrega_reclamada_y_viva_no_se_vuelve_a_reclamar():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        await svc.reclamar_siguiente()
        return await svc.reclamar_siguiente()

    assert correr(caso) is None


def test_una_entrega_huerfana_se_reclama_de_nuevo():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        await _envejecer(run_id, minutos=20)
        return await svc.reclamar_siguiente()

    reclamo = correr(caso)
    assert reclamo is not None
    assert reclamo[3] == 2, "segundo intento"


def test_tras_agotar_los_intentos_la_entrega_se_cierra_failed():
    from app.services import apify_webhook_service as svc

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        await _envejecer(run_id, minutos=20, intentos=svc.INBOX_MAX_INTENTOS)
        otro = await svc.reclamar_siguiente()
        return run_id, otro

    run_id, otro = correr(caso)
    assert otro is None
    fila = correr(lambda: _fila(run_id))
    assert fila.status == "failed"
    assert fila.params["inbox"] == svc.INBOX_ABANDONADO
    assert fila.finished_at is not None
    assert "se abandona" in (fila.error or "")


def test_retomar_la_corrida_conserva_los_params_del_inbox():
    from app.core.database import AsyncSessionLocal
    from app.services import apify_webhook_service as svc
    from app.services.ingest_service import IngestService

    async def caso():
        await _limpiar()
        await svc.encolar_dataset(DATASET, {})
        run_id, *_ = await svc.reclamar_siguiente()
        async with AsyncSessionLocal() as session:
            run = await IngestService(session).resume_run(run_id, {"keys": ["5-1"]})
            return dict(run.params)

    params = correr(caso)
    assert params["keys"] == ["5-1"]
    assert params["inbox"] == svc.INBOX_EN_PROCESO
    assert params["dataset_id"] == DATASET


# --- El upsert no borra coordenadas -------------------------------------------


def test_reingerir_sin_punto_conserva_el_punto_anterior():
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.models.event import RawEvent
    from app.repositories.event_repository import EventRepository
    from app.schemas.event import EventCreate

    def evento(lat, lon, raw):
        return EventCreate(
            timestamp=datetime.now(UTC),
            source=EventSource.TRANSPORTE_INFORMA,
            type=EventType.ACCIDENT,
            lat=lat,
            lon=lon,
            text="Accidente en Av. España con Uno Norte",
            external_id="integracion:1",
            confidence=0.6,
            raw_data=raw,
        )

    async def caso():
        await _limpiar()
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            await repo.upsert_many(
                [evento(-33.02, -71.55, {"_geocoding": {"comuna": "Viña del Mar"}})]
            )
            await session.commit()
            await repo.upsert_many([evento(None, None, {"otra": 1})])
            await session.commit()
            fila = await session.scalar(
                select(RawEvent).where(RawEvent.external_id == "integracion:1")
            )
            conocidos = await repo.puntos_conocidos(
                EventSource.TRANSPORTE_INFORMA, ["integracion:1", "integracion:no"]
            )
            return fila.lat, fila.lon, dict(fila.raw_data), conocidos

    lat, lon, raw, conocidos = correr(caso)
    assert (lat, lon) == (-33.02, -71.55)
    assert raw["_punto_heredado"] is True
    assert raw["_geocoding"] == {"comuna": "Viña del Mar"}
    assert raw["otra"] == 1, "el resto del raw_data nuevo sí entra"
    assert set(conocidos) == {"integracion:1"}
    assert conocidos["integracion:1"].lat == -33.02


# --- El motor calibrado (perfiles, comuna por polígono) ------------------------

#: Dos puntos de Viña del Mar a ~1 km: Av. Libertad con 5 Norte y con 15 Norte.
LIBERTAD_5_NORTE = (-33.01700, -71.55320)
LIBERTAD_15_NORTE = (-33.00800, -71.55120)
#: Centro de Quilpué, lejos de cualquier borde comunal.
QUILPUE = (-33.04720, -71.44250)


async def _vaciar_motor() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.event import RawEvent
    from app.models.incident import Incident

    tabla_ev = RawEvent.__table__.fullname
    tabla_inc = Incident.__table__.fullname
    async with AsyncSessionLocal() as session:
        await session.execute(text(f"TRUNCATE {tabla_inc}, {tabla_ev} RESTART IDENTITY CASCADE"))
        await session.commit()


async def _ingerir(*eventos) -> None:
    from app.core.database import AsyncSessionLocal
    from app.repositories.event_repository import EventRepository

    async with AsyncSessionLocal() as session:
        await EventRepository(session).upsert_many(list(eventos))
        await session.commit()


def _evento(
    external_id: str,
    punto: tuple[float, float] | None,
    *,
    hace: timedelta,
    fuente: str = "transporte_informa",
    tipo: str = "accident",
    raw: dict | None = None,
):
    from app.models.enums import EventSource, EventType
    from app.schemas.event import EventCreate

    lat, lon = punto if punto else (None, None)
    return EventCreate(
        timestamp=datetime.now(UTC) - hace,
        source=EventSource(fuente),
        type=EventType(tipo),
        lat=lat,
        lon=lon,
        text=f"señal de integración {external_id}",
        external_id=f"integracion:{external_id}",
        raw_data=raw or {},
    )


async def _pasada(*, perfiles: bool, window_hours: int = 4):
    from app.core.database import AsyncSessionLocal
    from app.models.incident import Incident
    from app.services.correlation.engine import CorrelationEngine

    async with AsyncSessionLocal() as session:
        resultado = await CorrelationEngine(
            session, perfiles=perfiles, window_hours=window_hours
        ).run()
    async with AsyncSessionLocal() as session:
        incidentes = (await session.execute(select(Incident).order_by(Incident.id))).scalars().all()
    return resultado, incidentes


@pytest.mark.parametrize(("perfiles", "esperados"), [(True, 2), (False, 1)])
def test_dos_choques_a_un_kilometro_son_dos_incidentes(perfiles, esperados):
    """C1: 1500 m para todo juntaba dos choques de la misma avenida."""
    from datetime import timedelta as td

    async def caso():
        await _vaciar_motor()
        await _ingerir(
            _evento("a", LIBERTAD_5_NORTE, hace=td(minutes=30)),
            _evento("b", LIBERTAD_15_NORTE, hace=td(minutes=20)),
        )
        return await _pasada(perfiles=perfiles)

    _, incidentes = correr(caso)
    assert len(incidentes) == esperados


@pytest.mark.parametrize(("perfiles", "incidentes_esperados"), [(True, 2), (False, 1)])
def test_una_senal_horas_despues_no_se_pega_al_choque_de_la_manana(perfiles, incidentes_esperados):
    """C3: el incidente tuvo que estar vivo cerca de la hora de la señal."""
    from datetime import timedelta as td

    async def caso():
        await _vaciar_motor()
        await _ingerir(_evento("choque", LIBERTAD_5_NORTE, hace=td(hours=5, minutes=30)))
        await _pasada(perfiles=perfiles, window_hours=8)
        await _ingerir(_evento("nota", (-33.01650, -71.55250), hace=td(minutes=30), fuente="media"))
        return await _pasada(perfiles=perfiles, window_hours=8)

    _, incidentes = correr(caso)
    assert len(incidentes) == incidentes_esperados


@pytest.mark.parametrize(("perfiles", "agrupada"), [(True, True), (False, False)])
def test_lo_que_llega_tarde_todavia_se_agrupa(perfiles, agrupada):
    """C4: FIRMS publica horas después de la pasada; la ventana era por `timestamp`."""
    from datetime import timedelta as td

    async def caso():
        await _vaciar_motor()
        await _ingerir(
            _evento(
                "firms", QUILPUE, hace=td(hours=10), fuente="nasa_firms", tipo="thermal_anomaly"
            )
        )
        return await _pasada(perfiles=perfiles)

    resultado, incidentes = correr(caso)
    assert (resultado.events_considered == 1) is agrupada
    assert (len(incidentes) == 1) is agrupada


def test_la_comuna_sale_del_poligono_cuando_ninguna_senal_la_dice():
    """C5: 211 incidentes de CGE sin comuna en 30 días (consulta del 2026-09-23)."""
    from datetime import timedelta as td

    async def caso():
        await _vaciar_motor()
        await _ingerir(
            _evento("cge", QUILPUE, hace=td(minutes=10), fuente="cge", tipo="power_outage")
        )
        return await _pasada(perfiles=True)

    resultado, (incidente,) = correr(caso)
    assert incidente.commune == "Quilpué"
    assert incidente.province == "Marga Marga"
    assert incidente.title.endswith("— Quilpué")
    assert resultado.communes_by_polygon == 1
    assert resultado.incidents_without_commune == 0


def test_la_comuna_geocodificada_gana_al_poligono():
    """C5: `_geocoding.comuna` ya estaba guardada y no se leía."""
    from datetime import timedelta as td

    async def caso():
        await _vaciar_motor()
        await _ingerir(
            _evento(
                "mtt",
                LIBERTAD_5_NORTE,
                hace=td(minutes=10),
                raw={"_geocoding": {"comuna": "Vina del Mar", "precision": "street"}},
            )
        )
        return await _pasada(perfiles=True)

    resultado, (incidente,) = correr(caso)
    assert incidente.commune == "Viña del Mar", "nombre canónico, con tilde"
    assert resultado.communes_by_polygon == 0


def test_el_indice_geography_sirve_a_la_consulta_del_motor():
    """La expresión del índice tiene que ser idéntica a la del `ST_DWithin`."""
    from sqlalchemy import func
    from sqlalchemy.dialects import postgresql

    from app.core.database import AsyncSessionLocal
    from app.models.incident import Incident
    from app.repositories.incident_repository import _GEOGRAPHY

    punto = func.ST_SetSRID(func.ST_MakePoint(-71.55, -33.02), 4326)
    consulta = (
        select(Incident.id)
        .where(Incident.status.in_(["active", "controlled"]))
        .where(
            func.ST_DWithin(func.cast(Incident.geom, _GEOGRAPHY), func.cast(punto, _GEOGRAPHY), 700)
        )
    )
    sql = str(
        consulta.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    async def caso():
        async with AsyncSessionLocal() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = (await session.execute(text(f"EXPLAIN {sql}"))).scalars().all()
            await session.rollback()
            return "\n".join(plan)

    plan = correr(caso)
    assert "ix_incidents_open_geog" in plan, plan


#: Puente Alto, Región Metropolitana: dentro de la caja `REGION_*`, fuera de la V.
PUENTE_ALTO = (-33.6117, -70.5758)
#: Frente al muelle Prat de Valparaíso, sobre el agua: fuera del polígono, a menos
#: de 2 km de la costa.
MUELLE_PRAT = (-33.0355, -71.6270)


@pytest.mark.parametrize(
    ("punto", "solo_region", "incidentes"),
    [
        (PUENTE_ALTO, True, 0),
        (PUENTE_ALTO, False, 1),
        (MUELLE_PRAT, True, 1),
        (QUILPUE, True, 1),
    ],
    ids=["santiago-filtrado", "santiago-sin-filtro", "muelle-dentro-del-margen", "quilpue"],
)
def test_solo_se_agrupan_senales_de_la_v_region(punto, solo_region, incidentes):
    """241 de 403 señales con punto de la semana del 19-09 eran de Santiago."""
    from datetime import timedelta as td

    from app.core.database import AsyncSessionLocal
    from app.models.incident import Incident
    from app.services.correlation.engine import CorrelationEngine

    async def caso():
        await _vaciar_motor()
        await _ingerir(
            _evento("cge", punto, hace=td(minutes=10), fuente="cge", tipo="power_outage")
        )
        async with AsyncSessionLocal() as session:
            await CorrelationEngine(session, solo_region=solo_region).run()
        async with AsyncSessionLocal() as session:
            return (await session.execute(select(Incident))).scalars().all()

    assert len(correr(caso)) == incidentes


# --- Cortes de agua: del collector a la capa ------------------------------------------


async def _limpiar_esval() -> None:
    from app.collectors.water.esval_worker import EsvalCollector
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource
    from app.models.event import CollectorRun, RawEvent

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(CollectorRun).where(CollectorRun.collector == EsvalCollector.name)
        )
        await session.execute(delete(RawEvent).where(RawEvent.source == EventSource.ESVAL))
        await session.commit()


def test_la_capa_de_agua_ve_lo_que_el_collector_escribio_y_suelta_lo_que_salio(monkeypatch):
    """Dos corridas reales por `BaseCollector.run()` contra Postgres.

    La primera ve los cortes de la captura; la segunda, sólo Viña. Quilpué
    sigue en la base (Esval no avisa cuándo termina un corte), pero ya no es
    vigente: `visto_en` quedó atrás de la última lectura. Valida el `CAST` de
    `visto_en` y la ventana de la vigencia, que los dobles no pueden probar.
    """
    import json

    import httpx
    import respx

    from app.collectors.water.esval_worker import EsvalCollector
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.services import water_cut_service
    from app.services.water_cut_service import WaterCutService
    from tests.test_esval_water import API_CORTES, KML_ZONAS

    # Las dos corridas pasan en milisegundos: sin margen, la segunda ya deja
    # atrás lo que la primera vio.
    monkeypatch.setattr(water_cut_service, "MARGEN_VIGENCIA", timedelta(0))
    solo_vina = {"data": [r for r in json.loads(API_CORTES)["data"] if r["sisda"] == "2916567"]}

    async def corrida(api: str) -> str:
        with respx.mock:
            respx.get(settings.ESVAL_CORTES_URL).mock(return_value=httpx.Response(200, text=api))
            respx.get(settings.ESVAL_ZONAS_KML_URL).mock(
                return_value=httpx.Response(200, text=KML_ZONAS)
            )
            async with AsyncSessionLocal() as session:
                resultado = await EsvalCollector(session).run()
        return resultado.status.value

    async def capa():
        async with AsyncSessionLocal() as session:
            return await WaterCutService(session).vigentes()

    async def caso():
        await _limpiar_esval()
        try:
            antes = await capa()
            primera = await corrida(API_CORTES)
            despues_de_la_primera = await capa()
            segunda = await corrida(json.dumps(solo_vina))
            despues_de_la_segunda = await capa()
            return antes, primera, despues_de_la_primera, segunda, despues_de_la_segunda
        finally:
            await _limpiar_esval()

    antes, primera, uno, segunda, dos = correr(caso)

    assert antes.total == 0 and antes.fuente.ultima_lectura is None
    assert primera in {"success", "partial"} and segunda in {"success", "partial"}

    sisdas_uno = {f.properties["sisda"] for f in uno.features}
    assert {"2916567", "2912217"} <= sisdas_uno
    assert "6261154" not in sisdas_uno, "Aguas del Valle (IV Región) no es de Esval"
    assert uno.fuente.estado == "ok"
    assert all(f.geometry is not None for f in uno.features), "el KML ubicó los cortes"

    assert {f.properties["sisda"] for f in dos.features} == {"2916567"}
    assert dos.fuente.ultima_lectura is not None
    assert dos.fuente.ultima_lectura > uno.fuente.ultima_lectura


def test_los_lugares_guardados_cuentan_para_el_radio_y_se_avisa_una_vez():
    """Ubicación en Valparaíso y Casa en Quilpué: un incendio en Quilpué avisa
    una sola vez, medido desde Casa, aunque Trabajo también quede cerca."""
    from app.core.database import AsyncSessionLocal
    from app.models.push import PushSubscription
    from app.repositories.push_repository import PushRepository

    endpoint = "https://fcm.googleapis.com/fcm/send/integracion-lugares"

    async def caso():
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            repo = PushRepository(session)
            hace_tres_dias = datetime.now(UTC) - timedelta(days=3)
            sub = await repo.upsert_subscription(
                endpoint=endpoint,
                p256dh="B" * 87,
                auth="A" * 22,
                lat=-33.045,
                lon=-71.620,
                accuracy_m=None,
                radius_m=5000.0,
                notify_incidents=True,
                notify_seismic=False,
                located_at=hace_tres_dias,
            )
            await repo.replace_places(
                sub.id, [("Casa", -33.047, -71.442), ("Trabajo", -33.060, -71.460)]
            )
            await session.flush()
            recipients = await repo.recipients_for_incident(
                incident_id=-1, code="INC-INTEGRACION-LUGARES", lat=-33.055, lon=-71.440
            )
            await session.execute(
                delete(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            await session.commit()
            return recipients, hace_tres_dias

    recipients, hace_tres_dias = correr(caso)
    mine = [r for r in recipients if r.endpoint == endpoint]
    assert len(mine) == 1
    assert mine[0].place == "Casa"
    assert mine[0].distance_m < 1500
    assert mine[0].located_at is not None
    assert abs((mine[0].located_at - hace_tres_dias).total_seconds()) < 5


# --- Reportes ciudadanos: quórum, cupo y moderación (§C) -----------------------

MARCA_CIUDADANA = "integracion-ciudadana"


async def _limpiar_ciudadanos() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource
    from app.models.event import RawEvent
    from app.models.incident import Incident, IncidentEvent

    async with AsyncSessionLocal() as session:
        ids = (
            (
                await session.execute(
                    select(RawEvent.id).where(
                        RawEvent.source == EventSource.CITIZEN,
                        RawEvent.text.like(f"{MARCA_CIUDADANA}%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            incidentes = (
                (
                    await session.execute(
                        select(IncidentEvent.incident_id).where(IncidentEvent.raw_event_id.in_(ids))
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(delete(RawEvent).where(RawEvent.id.in_(ids)))
            if incidentes:
                await session.execute(delete(Incident).where(Incident.id.in_(incidentes)))
        await session.commit()


async def _reportar(lat: float, lon: float, *, dispositivo: str, red: str, texto: str = "humo"):
    from app.core.database import AsyncSessionLocal
    from app.schemas.event import CitizenReportCreate, ReportCategory
    from app.services.ciudadanos import moderacion_inicial
    from app.services.ingest_service import IngestService

    reporte = CitizenReportCreate(
        lat=lat,
        lon=lon,
        accuracy_m=20,
        text=f"{MARCA_CIUDADANA} {texto}",
        category=ReportCategory.FIRE,
    )
    async with AsyncSessionLocal() as session:
        evento = await IngestService(session).ingest_citizen_report(
            reporte,
            huellas={"dispositivo": dispositivo, "red": red},
            moderacion=moderacion_inicial(reporte.text),
        )
        return evento.id


async def _correlacionar(ahora: datetime | None = None):
    from app.core.database import AsyncSessionLocal
    from app.services.correlation.engine import CorrelationEngine

    async with AsyncSessionLocal() as session:
        return await CorrelationEngine(session).run(now=ahora)


async def _incidente_de(evento_id: int):
    from app.core.database import AsyncSessionLocal
    from app.models.event import RawEvent
    from app.models.incident import Incident

    async with AsyncSessionLocal() as session:
        evento = await session.get(RawEvent, evento_id)
        if evento is None or evento.incident_id is None:
            return None
        return await session.get(Incident, evento.incident_id)


def test_tres_vecinos_publican_y_dos_de_la_misma_persona_no():
    punto_a = (-33.0245, -71.5518)  # Viña del Mar
    punto_b = (-33.0472, -71.6127)  # Valparaíso, a ~6 km

    async def caso():
        await _limpiar_ciudadanos()
        vecinos = [await _reportar(*punto_a, dispositivo=f"d{i}", red=f"r{i}") for i in range(3)]
        misma = [
            await _reportar(*punto_b, dispositivo="yo", red="4g"),
            await _reportar(*punto_b, dispositivo="yo", red="wifi"),
        ]
        await _correlacionar()
        publicado = await _incidente_de(vecinos[0])
        oculto = await _incidente_de(misma[0])

        from app.core.database import AsyncSessionLocal
        from app.repositories.incident_repository import IncidentRepository

        async with AsyncSessionLocal() as session:
            visibles = await IncidentRepository(session).list_incidents(limit=500)
        ids_visibles = {i.id for i in visibles}

        # 16 minutos después, lo que no juntó quórum se descarta.
        await _correlacionar(datetime.now(UTC) + timedelta(minutes=16))
        despues = await _incidente_de(misma[0])
        sigue = await _incidente_de(vecinos[0])
        await _limpiar_ciudadanos()
        return publicado, oculto, ids_visibles, despues, sigue

    publicado, oculto, visibles, despues, sigue = correr(caso)
    assert publicado is not None and oculto is not None
    assert (publicado.ciudadanos_independientes, publicado.publico) == (3, True)
    assert (oculto.ciudadanos_independientes, oculto.publico) == (1, False)
    assert publicado.id in visibles and oculto.id not in visibles
    assert despues.status.value == "dismissed"
    assert sigue.status.value == "active"


def test_el_cupo_y_la_moderacion_leen_la_base():
    from app.core.database import AsyncSessionLocal
    from app.repositories.event_repository import EventRepository
    from app.services.ingest_service import IngestService

    async def caso():
        await _limpiar_ciudadanos()
        await _reportar(-33.0245, -71.5518, dispositivo="cupo-d", red="cupo-r")
        await _reportar(-33.0245, -71.5518, dispositivo="otro", red="x", texto="9 8765 4321")
        async with AsyncSessionLocal() as session:
            servicio = IngestService(session)
            mismo = await servicio.citizen_retry_after(
                huellas={"dispositivo": "cupo-d", "red": "otra-red"}
            )
            nuevo = await servicio.citizen_retry_after(
                huellas={"dispositivo": "nuevo", "red": "otra-red"}
            )
            pendientes = await EventRepository(session).pending_moderation(
                since=datetime.now(UTC) - timedelta(hours=1), limit=50
            )
            rechazados = await EventRepository(session).pending_moderation(
                since=datetime.now(UTC) - timedelta(hours=1), limit=50, estados=("rechazado",)
            )
        textos_p = [e.text for e in pendientes if e.text and e.text.startswith(MARCA_CIUDADANA)]
        textos_r = [e.text for e in rechazados if e.text and e.text.startswith(MARCA_CIUDADANA)]
        await _limpiar_ciudadanos()
        return mismo, nuevo, textos_p, textos_r

    mismo, nuevo, pendientes, rechazados = correr(caso)
    assert mismo is not None and 590 <= mismo <= 601
    assert nuevo is None
    assert pendientes == [f"{MARCA_CIUDADANA} humo"]
    assert rechazados == [f"{MARCA_CIUDADANA} 9 8765 4321"]


# --- §K: radios por categoría, cortes de agua y punto del incidente ------------

ENDPOINT_RADIOS = "https://fcm.googleapis.com/fcm/send/integracion-radios"
MARCA_K = "integracion-k"


async def _limpiar_k() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.event import RawEvent
    from app.models.incident import Incident, IncidentEvent
    from app.models.push import PushSubscription

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(PushSubscription).where(PushSubscription.endpoint == ENDPOINT_RADIOS)
        )
        ids = (
            (
                await session.execute(
                    select(RawEvent.id).where(RawEvent.external_id.like(f"{MARCA_K}%"))
                )
            )
            .scalars()
            .all()
        )
        if ids:
            incidentes = (
                (
                    await session.execute(
                        select(IncidentEvent.incident_id).where(IncidentEvent.raw_event_id.in_(ids))
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(delete(RawEvent).where(RawEvent.id.in_(ids)))
            if incidentes:
                await session.execute(delete(Incident).where(Incident.id.in_(incidentes)))
        await session.commit()


def test_cada_categoria_usa_su_radio_y_los_cortes_de_agua_se_avisan():
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.repositories.event_repository import EventRepository
    from app.repositories.push_repository import PushRepository
    from app.schemas.event import EventCreate

    async def caso():
        await _limpiar_k()
        async with AsyncSessionLocal() as session:
            repo = PushRepository(session)
            await repo.upsert_subscription(
                endpoint=ENDPOINT_RADIOS,
                p256dh="B" * 87,
                auth="A" * 22,
                lat=-33.045,
                lon=-71.620,
                accuracy_m=None,
                radius_m=5000.0,
                notify_incidents=True,
                notify_seismic=False,
                radios={"fire": 8000.0, "power": 0.0},
            )
            # Una resincronización sin radios (una PWA vieja) no los borra.
            await repo.upsert_subscription(
                endpoint=ENDPOINT_RADIOS,
                p256dh="B" * 87,
                auth="A" * 22,
                lat=-33.045,
                lon=-71.620,
                accuracy_m=None,
                radius_m=5000.0,
                notify_incidents=True,
                notify_seismic=False,
            )
            await session.commit()

            a_3_km = (-33.072, -71.620)
            mios = {}
            for categoria in ("fire", "power", "traffic"):
                encontrados = await repo.recipients_for_incident(
                    incident_id=-1,
                    code=f"INC-K-{categoria}",
                    lat=a_3_km[0],
                    lon=a_3_km[1],
                    categoria=categoria,
                )
                mios[categoria] = [r for r in encontrados if r.endpoint == ENDPOINT_RADIOS]

            ahora = datetime.now(UTC)
            await EventRepository(session).add(
                EventCreate(
                    timestamp=ahora,
                    source=EventSource.ESVAL,
                    type=EventType.WATER_CUT,
                    lat=-33.049,
                    lon=-71.620,
                    text="corte",
                    external_id=f"{MARCA_K}:agua",
                    confidence=1.0,
                    raw_data={"_esval": {"visto_en": ahora.isoformat(), "comuna": "Valparaíso"}},
                )
            )
            await session.commit()
            cortes = await repo.recent_water_cuts(
                seen_since=ahora - timedelta(hours=1), first_seen_since=ahora - timedelta(hours=12)
            )
            nuestro = [c for c in cortes if c.key == f"{MARCA_K}:agua"]
            avisados = await repo.recipients_for_water_cut(
                subject_key=f"{MARCA_K}:agua", lat=-33.049, lon=-71.620
            )
        await _limpiar_k()
        return mios, nuestro, [r for r in avisados if r.endpoint == ENDPOINT_RADIOS]

    mios, nuestro, avisados_agua = correr(caso)
    assert len(mios["fire"]) == 1, "8 km de radio elegido para incendios"
    assert mios["power"] == [], "cortes de luz apagados"
    assert mios["traffic"] == [], "accidentes: 2 km por defecto, el incidente está a 3 km"
    assert len(nuestro) == 1 and nuestro[0].comuna == "Valparaíso"
    assert len(avisados_agua) == 1 and avisados_agua[0].distance_m < 1000


def test_el_incidente_queda_en_el_cruce_y_no_en_el_promedio():
    from app.core.database import AsyncSessionLocal
    from app.models.enums import EventSource, EventType
    from app.models.event import RawEvent
    from app.models.incident import Incident
    from app.repositories.event_repository import EventRepository
    from app.schemas.event import EventCreate
    from app.services.correlation.engine import CorrelationEngine

    async def caso():
        await _limpiar_k()
        ahora = datetime.now(UTC)
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            despacho = await repo.add(
                EventCreate(
                    timestamp=ahora,
                    source=EventSource.BOMBEROS,
                    type=EventType.STRUCTURAL_FIRE,
                    lat=-33.0500,
                    lon=-71.6150,
                    text="LAS MONJAS / ANDRES BELLO",
                    external_id=f"{MARCA_K}:despacho",
                    confidence=1.0,
                    raw_data={"_geocoding": {"precision": "intersection", "provider": "overpass"}},
                )
            )
            await repo.add(
                EventCreate(
                    timestamp=ahora,
                    source=EventSource.MEDIA,
                    type=EventType.STRUCTURAL_FIRE,
                    lat=-33.0480,
                    lon=-71.6160,
                    text="Incendio en Las Monjas",
                    external_id=f"{MARCA_K}:nota",
                    confidence=0.7,
                    raw_data={"_geocoding": {"precision": "street"}},
                )
            )
            await session.commit()
            await CorrelationEngine(session).run()
            fila = await session.get(RawEvent, despacho.id)
            incidente = await session.get(Incident, fila.incident_id) if fila else None
            resultado = (
                (incidente.lat, incidente.lon, incidente.ubicacion_precision, incidente.event_count)
                if incidente
                else None
            )
        await _limpiar_k()
        return resultado

    resultado = correr(caso)
    assert resultado is not None
    lat, lon, precision, eventos = resultado
    assert eventos == 2, "la nota y el despacho son el mismo incendio"
    assert (round(lat, 4), round(lon, 4)) == (-33.05, -71.615)
    assert precision == "intersection"
