"""Smoke test end-to-end contra una base PostGIS real.

    python scripts/smoke_test.py

Verifica el camino completo: ingesta → idempotencia → columna generada →
consulta espacio-temporal → collector FIRMS (con la API mockeada) → API HTTP.
No requiere MAP_KEY real.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

from app.collectors.firms.collector import FirmsCollector
from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine
from app.main import app
from app.models.enums import EventSource, EventType
from app.repositories.incident_repository import IncidentRepository
from app.schemas.event import EventCreate
from app.services.correlation.engine import CorrelationEngine
from app.services.ingest_service import IngestService

#: Folio legible que debe generar la base: INC-2026-00142.
CODE_PATTERN = re.compile(r"INC-\d{4}-\d{5,}")

VIIRS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
-33.02500,-71.52000,340.5,0.42,0.38,{date},1832,N,VIIRS,n,2.0NRT,295.1,12.34,D
-32.98100,-71.48700,367.2,0.40,0.36,{date},1832,N,VIIRS,h,2.0NRT,301.8,45.60,D
"""

EMPTY_CSV = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"

ok = 0
fail = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  [FALLA] {label}" + (f" — {detail}" if detail else ""))


async def main() -> int:
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        service = IngestService(session)

        # 1. Ingesta de un lote --------------------------------------------
        print("\n1. Ingesta idempotente")
        batch = [
            EventCreate(
                timestamp=now - timedelta(minutes=8),
                source=EventSource.CITIZEN,
                type=EventType.SMOKE,
                lat=-33.0250,
                lon=-71.5200,
                text="Humo denso en el cerro, sector Forestal",
            ),
            EventCreate(
                timestamp=now - timedelta(minutes=6),
                source=EventSource.BROADCASTIFY,
                type=EventType.DISPATCH,
                lat=-33.0261,
                lon=-71.5188,
                text="Se despacha material mayor, incendio forestal, segunda alarma",
                external_id="bcfy:call:998877",
            ),
            EventCreate(
                timestamp=now - timedelta(minutes=4),
                source=EventSource.NASA_FIRMS,
                type=EventType.THERMAL_ANOMALY,
                lat=-33.0272,
                lon=-71.5205,
                text="Anomalía térmica detectada por VIIRS_SNPP_NRT. Sin confirmar.",
                external_id="firms:deadbeef",
            ),
            EventCreate(
                timestamp=now - timedelta(minutes=2),
                source=EventSource.CONAF,
                type=EventType.WILDFIRE,
                lat=-33.0280,
                lon=-71.5210,
                text=(
                    'Incendio forestal "Cerro Forestal" — estado: En Combate. '
                    "Ubicación: Viña del Mar, Valparaíso. Reporte oficial de CONAF."
                ),
                external_id="conaf:2026-00142",
                # El motor lee la comuna de acá para el Paso B: es el único
                # puente entre un incendio con coordenadas y una alerta que sólo
                # tiene el nombre de una comuna.
                raw_data={
                    "comuna": "Viña del Mar",
                    "provincia": "Valparaíso",
                    "estado": "En Combate",
                },
            ),
        ]
        first = await service.ingest_batch(batch)
        check("primer lote insertado", first.inserted == 4, f"inserted={first.inserted}")

        second = await service.ingest_batch(batch)
        check(
            "reejecución no duplica los que tienen external_id",
            second.inserted == 1 and second.duplicated == 3,
            f"inserted={second.inserted} dup={second.duplicated}",
        )
        check(
            "reporte ciudadano sin external_id sí se reinserta",
            second.inserted == 1,
            "dos vecinos reportando el mismo humo son dos señales",
        )

        # 2. Confianza por línea base --------------------------------------
        print("\n2. Confianza")
        auto = EventCreate(
            timestamp=now, source=EventSource.NASA_FIRMS, lat=-33.0, lon=-71.5
        )
        check("FIRMS recibe línea base 0.55", abs(auto.confidence - 0.55) < 1e-6)
        auto_conaf = EventCreate(
            timestamp=now, source=EventSource.CONAF, lat=-33.0, lon=-71.5
        )
        check("CONAF recibe línea base 1.0", abs(auto_conaf.confidence - 1.0) < 1e-6)

        # 3. Columna generada -----------------------------------------------
        print("\n3. Geometría")
        from sqlalchemy import func, select

        from app.models.event import RawEvent

        row = (
            await session.execute(
                select(
                    func.ST_AsText(RawEvent.geom), func.ST_SRID(RawEvent.geom)
                ).where(RawEvent.external_id == "conaf:2026-00142")
            )
        ).first()
        check("geom generada desde lat/lon", row is not None and "POINT" in row[0], str(row[0]))
        check("SRID 4326", row is not None and row[1] == 4326, str(row[1]))

        # 4. Correlación espacio-temporal ------------------------------------
        print("\n4. Correlación espacio-temporal")
        neighbours = await service.repo.find_spatiotemporal_neighbours(
            lat=-33.0250, lon=-71.5200, timestamp=now, radius_m=2000, window_minutes=60
        )
        check(
            "las 4 fuentes caen en el mismo cluster",
            len(neighbours) >= 4,
            f"{len(neighbours)} señales en 2 km / 60 min",
        )
        fuentes = {n.source.value for n in neighbours}
        check(
            "cluster multi-fuente",
            {"citizen", "broadcastify", "nasa_firms", "conaf"} <= fuentes,
            ", ".join(sorted(fuentes)),
        )

        lejos = await service.repo.find_spatiotemporal_neighbours(
            lat=-33.6000, lon=-71.6000, timestamp=now, radius_m=2000, window_minutes=60
        )
        check("señales lejanas no se agrupan", len(lejos) == 0, f"{len(lejos)} vecinos")

        # 5. Motor de correlación --------------------------------------------
        print("\n5. Motor de correlación")

        # Alerta vigente de SENAPRED: tabular, sin coordenadas. Es el caso que
        # obliga a que exista el Paso B.
        await service.ingest_batch(
            [
                EventCreate(
                    timestamp=now - timedelta(minutes=1),
                    source=EventSource.SENAPRED,
                    type=EventType.ALERT,
                    text=(
                        "Alerta Roja vigente. Región: Valparaíso. "
                        "Comunas: Viña del Mar. Motivo: Incendio Forestal. "
                        "Declarada por SENAPRED."
                    ),
                    external_id="senapred:smoke:1",
                    raw_data={
                        "Region": "Valparaíso",
                        "Alerta": "Alerta Roja",
                        "Comunas": "Viña del Mar",
                        "Ambito": "Comunal",
                        "Evento": "Incendio Forestal",
                        "_alert_level": "roja",
                        "_national": False,
                    },
                )
            ]
        )

        primera = await CorrelationEngine(session).run()
        check(
            "el Paso A crea incidentes desde las señales georreferenciadas",
            primera.incidents_created >= 1,
            f"creados={primera.incidents_created} racimos={primera.clusters}",
        )

        repo = IncidentRepository(session)
        incidentes = list(await repo.open_incidents())
        confirmado = next((i for i in incidentes if i.is_official_confirmed), None)
        check("hay un incidente confirmado por CONAF", confirmado is not None)

        if confirmado is not None:
            check(
                "folio legible",
                bool(CODE_PATTERN.fullmatch(confirmado.code)),
                confirmado.code,
            )
            check(
                "CONAF lleva la confianza a 1.0",
                abs(confirmado.confidence - 1.0) < 1e-6,
                f"confidence={confirmado.confidence}",
            )
            check(
                "el tipo lo fija la fuente confirmatoria",
                confirmado.type.value == "wildfire",
                confirmado.type.value,
            )
            check(
                "el incidente es multi-fuente",
                confirmado.source_count >= 3,
                ", ".join(sorted(confirmado.sources)),
            )
            check(
                "la geometría se derivó del centroide ponderado",
                confirmado.geom is not None,
                f"({confirmado.lat:.5f}, {confirmado.lon:.5f})",
            )
            check(
                "la comuna se extrajo de la capa de CONAF",
                confirmado.commune == "Viña del Mar",
                str(confirmado.commune),
            )

            # -- Paso B ---------------------------------------------------
            check(
                "el Paso B adosó la alerta de SENAPRED por comuna",
                confirmado.alert_level == "roja",
                f"alert_level={confirmado.alert_level}",
            )
            check(
                "el estado de alerta es cierto al 100%",
                abs(confirmado.alert_confidence - 1.0) < 1e-6,
                f"alert_confidence={confirmado.alert_confidence}",
            )

            enlaces = await repo.links_with_events(confirmado.id)
            metodos = {link.link_method.value for link, _ in enlaces}
            check(
                "conviven vínculos espaciales y por texto",
                {"spatial", "commune_text"} <= metodos,
                ", ".join(sorted(metodos)),
            )
            espaciales = [link for link, _ in enlaces if link.link_method.value == "spatial"]
            check(
                "los vínculos espaciales registran su distancia",
                all(link.distance_m is not None for link in espaciales),
                f"{len(espaciales)} vínculos espaciales",
            )
            por_texto = [link for link, _ in enlaces if link.link_method.value == "commune_text"]
            check(
                "un vínculo por comuna vale menos que uno geométrico",
                all(link.link_confidence < 1.0 for link in por_texto),
                ", ".join(f"{link.link_confidence}" for link in por_texto),
            )

        # -- Idempotencia del motor ----------------------------------------
        antes = len(incidentes)
        segunda = await CorrelationEngine(session).run()
        despues = len(await repo.open_incidents())
        check(
            "reejecutar el motor no duplica incidentes",
            despues == antes and segunda.incidents_created == 0,
            f"{antes} → {despues}, creados={segunda.incidents_created}",
        )

        # 6. Collector NASA FIRMS con la API mockeada -------------------------
        print("\n6. Collector NASA FIRMS (API mockeada)")
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*VIIRS_SNPP_NRT.*").mock(
                return_value=httpx.Response(200, text=VIIRS_CSV.format(date=yesterday))
            )
            mock.get(url__regex=r".*(VIIRS_NOAA20_NRT|VIIRS_NOAA21_NRT|MODIS_NRT).*").mock(
                return_value=httpx.Response(200, text=EMPTY_CSV)
            )
            collector = FirmsCollector(session)
            result = await collector.run()

        check(
            "collector completó",
            result.status.value in {"success", "partial"},
            f"status={result.status.value} fetched={result.fetched} inserted={result.inserted}",
        )
        check("detecciones ingeridas", result.inserted == 2, f"inserted={result.inserted}")

        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*VIIRS_SNPP_NRT.*").mock(
                return_value=httpx.Response(200, text=VIIRS_CSV.format(date=yesterday))
            )
            mock.get(url__regex=r".*(VIIRS_NOAA20_NRT|VIIRS_NOAA21_NRT|MODIS_NRT).*").mock(
                return_value=httpx.Response(200, text=EMPTY_CSV)
            )
            rerun = await FirmsCollector(session).run()
        check(
            "reejecución del collector es idempotente",
            rerun.inserted == 0 and rerun.duplicated == 2,
            f"inserted={rerun.inserted} dup={rerun.duplicated}",
        )

        # 7. Error enmascarado de FIRMS ---------------------------------------
        print("\n7. Manejo de errores de FIRMS")
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=r".*").mock(
                return_value=httpx.Response(200, text="Invalid MAP_KEY.")
            )
            bad = await FirmsCollector(session).run()
        check(
            "MAP_KEY inválida se reporta como fallo, no como cero detecciones",
            bad.status.value == "failed",
            f"status={bad.status.value}",
        )

        # 8. Estadísticas ------------------------------------------------------
        print("\n8. Estadísticas")
        stats = await service.repo.stats()
        check("stats agrega por fuente", len(stats["by_source"]) >= 4, str(stats["by_source"]))
        check(
            "todos los eventos georreferenciados",
            stats["georeferenced"] == stats["total"],
            f"{stats['georeferenced']}/{stats['total']}",
        )

    # El engine es global y su pool queda atado al loop que lo estrenó.
    # TestClient levanta su propio loop, así que hay que soltar las conexiones
    # antes de cambiar de loop. En producción esto no ocurre: uvicorn tiene uno solo.
    await dispose_engine()
    return 0


def http_checks() -> None:
    print("\n9. API HTTP")
    with TestClient(app) as client:
        health = client.get(f"{settings.API_V1_PREFIX}/health/ready")
        check("readiness detecta PostGIS", health.status_code == 200 and health.json().get("postgis"),
              str(health.json().get("postgis")))

        listing = client.get(f"{settings.API_V1_PREFIX}/events", params={"limit": 5})
        check("GET /events", listing.status_code == 200, f"{len(listing.json())} eventos")

        geojson = client.get(f"{settings.API_V1_PREFIX}/events/geojson", params={"hours": 48})
        body = geojson.json()
        check(
            "GET /events/geojson devuelve FeatureCollection",
            geojson.status_code == 200 and body["type"] == "FeatureCollection",
            f"{len(body['features'])} features",
        )
        check(
            "GeoJSON marca que no son incidentes confirmados",
            all(f["properties"]["is_confirmed_incident"] is False for f in body["features"]),
        )

        report = client.post(
            f"{settings.API_V1_PREFIX}/events/citizen-report",
            json={"lat": -33.0255, "lon": -71.5195, "text": "Se ve fuego desde la costanera", "type": "smoke"},
        )
        check("POST /events/citizen-report", report.status_code == 201, report.text[:120])
        if report.status_code == 201:
            data = report.json()
            check("servidor fuerza source=citizen", data["source"] == "citizen")
            check("servidor asigna confianza base", abs(data["confidence"] - 0.5) < 1e-6)

        spoof = client.post(
            f"{settings.API_V1_PREFIX}/events/citizen-report",
            json={"lat": -33.0, "lon": -71.5, "text": "alerta roja regional", "type": "alert"},
        )
        check(
            "un ciudadano no puede publicar una alerta oficial",
            spoof.status_code == 422,
            f"HTTP {spoof.status_code}",
        )

        bad_bbox = client.get(f"{settings.API_V1_PREFIX}/events", params={"bbox": "1,2,3"})
        check("bbox malformado rechazado", bad_bbox.status_code == 422)

        stats_resp = client.get(f"{settings.API_V1_PREFIX}/events/stats")
        check("GET /events/stats", stats_resp.status_code == 200, str(stats_resp.json()["total"]))

        runs = client.get(f"{settings.API_V1_PREFIX}/collectors/runs")
        check("GET /collectors/runs traza las ejecuciones", runs.status_code == 200,
              f"{len(runs.json())} corridas registradas")

        # -- Incidentes consolidados -----------------------------------------
        print("\n10. API HTTP — incidentes")
        activos = client.get(f"{settings.API_V1_PREFIX}/incidents/active")
        check("GET /incidents/active", activos.status_code == 200,
              f"{len(activos.json())} incidentes")

        incidentes = activos.json() if activos.status_code == 200 else []
        confirmado = next((i for i in incidentes if i["is_official_confirmed"]), None)
        check("el mapa recibe al menos un incidente confirmado", confirmado is not None)

        if confirmado is not None:
            check("la etiqueta legible acompaña al número",
                  confirmado["confidence_label"] == "confirmado",
                  confirmado["confidence_label"])
            check("los dos ejes viajan separados",
                  "alert_level" in confirmado and "confidence" in confirmado,
                  f"conf={confirmado['confidence']} alerta={confirmado['alert_level']}")
            check("la derivación de la confianza es auditable desde la API",
                  bool(confirmado["confidence_breakdown"].get("by_source")),
                  ", ".join(sorted(confirmado["confidence_breakdown"].get("by_source", {}))))

            detalle = client.get(f"{settings.API_V1_PREFIX}/incidents/{confirmado['code']}")
            check("GET /incidents/{code}", detalle.status_code == 200, confirmado["code"])
            if detalle.status_code == 200:
                eventos = detalle.json()["events"]
                check("el detalle explica cada vínculo",
                      all(e["link_method"] for e in eventos),
                      f"{len(eventos)} señales vinculadas")

        geo_inc = client.get(f"{settings.API_V1_PREFIX}/incidents/geojson")
        cuerpo = geo_inc.json()
        check("GET /incidents/geojson devuelve FeatureCollection",
              geo_inc.status_code == 200 and cuerpo["type"] == "FeatureCollection",
              f"{len(cuerpo['features'])} features")
        check("aquí is_confirmed_incident sí es un dato real del motor",
              any(f["properties"]["is_confirmed_incident"] for f in cuerpo["features"]))

        solo_confirmados = client.get(
            f"{settings.API_V1_PREFIX}/incidents/active", params={"confirmed_only": True}
        )
        check("filtro confirmed_only",
              solo_confirmados.status_code == 200
              and all(i["is_official_confirmed"] for i in solo_confirmados.json()),
              f"{len(solo_confirmados.json())} confirmados")

        inexistente = client.get(f"{settings.API_V1_PREFIX}/incidents/INC-1999-00001")
        check("folio inexistente devuelve 404", inexistente.status_code == 404)

        stats_inc = client.get(f"{settings.API_V1_PREFIX}/incidents/stats")
        check("GET /incidents/stats", stats_inc.status_code == 200,
              str(stats_inc.json()["total"]))

        schema = client.get(f"{settings.API_V1_PREFIX}/openapi.json")
        check("OpenAPI se genera", schema.status_code == 200,
              f"{len(schema.json()['paths'])} rutas documentadas")


if __name__ == "__main__":
    asyncio.run(main())
    http_checks()
    print(f"\n{'=' * 60}\n  {ok} verificaciones OK, {fail} fallas\n{'=' * 60}")
    sys.exit(1 if fail else 0)
