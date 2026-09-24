"""Reproduce días de señales reales con el motor, para calibrarlo antes de confiar.

Dos pasos, en dos bases distintas:

    # 1. Exportar las señales de producción (sólo lectura). Pide la URL sin
    #    mostrarla: no queda en el historial de la terminal.
    python scripts/replay_correlacion.py exportar --dias 14 --salida senales.jsonl

    # 2. Reproducirlas en una base DESECHABLE (la del .env local, con
    #    `alembic upgrade head` aplicado). Borra incidentes y señales de esa base.
    python scripts/replay_correlacion.py reproducir senales.jsonl --modo comparar \\
        --confirmo-base-desechable

`reproducir` inserta las señales en el orden en que llegaron (`ingested_at`) y
corre una pasada del motor cada `--paso-min` minutos con el reloj simulado
(`CorrelationEngine.run(now=…)`). Con `--modo comparar` lo hace dos veces —motor
anterior (`CORRELATION_PERFILES=false`) y calibrado— y compara:

* incidentes por familia, sin comuna, fundidos, descartados;
* pares de señales que un modo junta y el otro separa, con ejemplos de prensa
  y redes sociales para revisar a mano (la calibración que pidió la auditoría).

Limitaciones que conviene tener presentes al leer los números:

* **El Paso B es aproximado.** La vigencia de una alerta se mide con
  `updated_at`, y la exportación sólo trae el último; acá se usa `ingested_at`,
  así que una alerta «vive» `CORRELATION_ALERT_VALIDITY_HOURS` desde que llegó.
* Las señales que un collector reescribió (upsert) aparecen con su última
  versión, no con la que vio el motor en su momento.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Ejecutable como `python scripts/replay_correlacion.py` desde `backend/`, con o
# sin el paquete instalado.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Tipos que el motor lee: los correlacionables (Paso A) y las alertas (Paso B).
_TIPOS_ALERTA = ("alert", "evacuation")

_COLUMNAS = (
    "id",
    "source",
    "type",
    "lat",
    "lon",
    "text",
    "external_id",
    "confidence",
    "raw_data",
    "timestamp",
    "ingested_at",
    "commune",
    "province",
)


# -- Exportar ------------------------------------------------------------------


async def exportar(dias: int, salida: Path) -> int:
    import asyncpg

    from app.core.config import settings
    from app.models.enums import CORRELATABLE_EVENT_TYPES

    url = getpass.getpass("DATABASE_URL de ORIGEN (no se muestra): ").strip()
    if not url:
        print("sin URL, nada que hacer", file=sys.stderr)
        return 2
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    tipos = sorted({t.value for t in CORRELATABLE_EVENT_TYPES} | set(_TIPOS_ALERTA))
    consulta = f"""
        SELECT {", ".join(f'"{c}"' for c in _COLUMNAS)}
        FROM {settings.DB_SCHEMA}.raw_events
        WHERE ingested_at >= now() - make_interval(days => $1)
          AND type::text = ANY($2::text[])
        ORDER BY ingested_at, id
    """
    conexion = await asyncpg.connect(url, statement_cache_size=0)
    try:
        # Sólo lectura también para la base: un error de este script no puede
        # escribir en producción.
        async with conexion.transaction(readonly=True):
            filas = await conexion.fetch(consulta, dias, tipos)
    finally:
        await conexion.close()

    with salida.open("w", encoding="utf-8") as archivo:
        for fila in filas:
            registro = dict(fila)
            if isinstance(registro["raw_data"], str):
                registro["raw_data"] = json.loads(registro["raw_data"])
            archivo.write(json.dumps(registro, default=_iso, ensure_ascii=False) + "\n")
    print(f"{len(filas)} señales de {dias} días → {salida}")
    return 0


def _iso(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return str(valor)


# -- Reproducir ------------------------------------------------------------------


def _leer(ruta: Path) -> list[dict[str, Any]]:
    senales = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        fila = json.loads(linea)
        for campo in ("timestamp", "ingested_at"):
            fila[campo] = datetime.fromisoformat(fila[campo])
        senales.append(fila)
    senales.sort(key=lambda f: (f["ingested_at"], f["id"]))
    return senales


def _base_desechable() -> str:
    """La base de destino, o SystemExit si parece producción."""
    from app.core.config import settings

    host = urlsplit(settings.database_url).hostname or ""
    if settings.ENVIRONMENT == "production" or "supabase" in host or "pooler" in host:
        raise SystemExit(
            f"la base de destino ({host}) parece producción: reproducir BORRA incidentes "
            "y señales. Apuntar DATABASE_URL/POSTGRES_* a una base local desechable."
        )
    return host


async def _vaciar() -> None:
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.models.event import RawEvent
    from app.models.incident import Incident

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                f"TRUNCATE {Incident.__table__.fullname}, "  # type: ignore[attr-defined]
                f"{RawEvent.__table__.fullname} RESTART IDENTITY CASCADE"  # type: ignore[attr-defined]
            )
        )
        await session.commit()


async def _insertar(lote: Iterable[dict[str, Any]]) -> None:
    from sqlalchemy import text

    from app.core.database import AsyncSessionLocal
    from app.models.event import RawEvent

    tabla = RawEvent.__table__.fullname  # type: ignore[attr-defined]
    sentencia = text(
        f"""
        INSERT INTO {tabla} (id, source, type, lat, lon, text, external_id, confidence,
                             raw_data, timestamp, ingested_at, updated_at, commune, province)
        OVERRIDING SYSTEM VALUE
        VALUES (:id, CAST(:source AS {RawEvent.__table__.schema}.event_source),
                CAST(:type AS {RawEvent.__table__.schema}.event_type), :lat, :lon, :text,
                :external_id, :confidence, CAST(:raw_data AS jsonb), :timestamp,
                :ingested_at, :ingested_at, :commune, :province)
        """
    )
    filas = [{**fila, "raw_data": json.dumps(fila.get("raw_data") or {})} for fila in lote]
    if not filas:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(sentencia, filas)
        # Los ids vienen de producción (OVERRIDING SYSTEM VALUE): la secuencia
        # de identidad no se enteró, y la próxima inserción normal chocaría.
        await session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), "
                f"(SELECT max(id) FROM {tabla}))"
            )
        )
        await session.commit()


async def _una_corrida(
    senales: list[dict[str, Any]], *, perfiles: bool, paso: timedelta
) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.enums import family_of_incident
    from app.models.event import RawEvent
    from app.models.incident import Incident
    from app.services.correlation.engine import CorrelationEngine

    await _vaciar()
    reloj = senales[0]["ingested_at"]
    fin = senales[-1]["ingested_at"] + paso
    siguiente = 0
    pasadas = 0
    totales: Counter[str] = Counter()

    while reloj <= fin:
        lote = []
        while siguiente < len(senales) and senales[siguiente]["ingested_at"] <= reloj:
            lote.append(senales[siguiente])
            siguiente += 1
        await _insertar(lote)
        async with AsyncSessionLocal() as session:
            pasada = await CorrelationEngine(session, perfiles=perfiles).run(now=reloj)
        for clave in ("incidents_created", "incidents_merged", "incidents_dismissed",
                      "clusters_split", "communes_by_polygon"):
            totales[clave] += int(getattr(pasada, clave))
        pasadas += 1
        reloj += paso

    async with AsyncSessionLocal() as session:
        incidentes = (await session.execute(select(Incident))).scalars().all()
        asignadas = (
            await session.execute(
                select(RawEvent.id, RawEvent.incident_id, RawEvent.source)
                .where(RawEvent.incident_id.isnot(None))
            )
        ).all()

    vivos = [
        i for i in incidentes
        if getattr(i.status, "value", i.status) not in ("merged", "dismissed")
    ]
    return {
        "pasadas": pasadas,
        "incidentes": len(vivos),
        "por_familia": Counter(family_of_incident(i.type) for i in vivos),
        "sin_comuna": sum(1 for i in vivos if not i.commune),
        "fundidos": totales["incidents_merged"],
        "descartados": totales["incidents_dismissed"],
        "racimos_cortados": totales["clusters_split"],
        "comuna_por_poligono": totales["communes_by_polygon"],
        "incidente_de": {fila.id: fila.incident_id for fila in asignadas},
        "fuente_de": {fila.id: getattr(fila.source, "value", fila.source) for fila in asignadas},
    }


def _pares(incidente_de: dict[int, int]) -> set[tuple[int, int]]:
    grupos: dict[int, list[int]] = defaultdict(list)
    for evento, incidente in incidente_de.items():
        grupos[incidente].append(evento)
    return {
        (a, b)
        for miembros in grupos.values()
        for i, a in enumerate(sorted(miembros))
        for b in sorted(miembros)[i + 1 :]
    }


def _informe(nombre: str, r: dict[str, Any]) -> None:
    print(f"\n== {nombre} ({r['pasadas']} pasadas)")
    print(f"  incidentes vivos     {r['incidentes']}")
    for familia, n in sorted(r["por_familia"].items()):
        print(f"    {familia:<10} {n}")
    print(f"  sin comuna           {r['sin_comuna']}")
    print(f"  comuna por polígono  {r['comuna_por_poligono']}")
    print(f"  fundidos             {r['fundidos']}")
    print(f"  descartados          {r['descartados']}")
    print(f"  racimos cortados     {r['racimos_cortados']}")


def _comparar(a: dict[str, Any], b: dict[str, Any], senales: list[dict[str, Any]]) -> None:
    pares_a, pares_b = _pares(a["incidente_de"]), _pares(b["incidente_de"])
    solo_a, solo_b = pares_a - pares_b, pares_b - pares_a
    print("\n== Diferencias")
    print(f"  pares que sólo junta el motor anterior  {len(solo_a)}")
    print(f"  pares que sólo junta el calibrado        {len(solo_b)}")

    texto = {
        s["id"]: (s["source"], s["type"], f"{s['timestamp']:%d-%m %H:%M}", (s.get("text") or "")[:80])
        for s in senales
    }
    prensa = {"media", "social_media"}
    casos = [
        par for par in sorted(solo_a | solo_b)
        if texto.get(par[0], ("",))[0] in prensa or texto.get(par[1], ("",))[0] in prensa
    ][:20]
    if casos:
        print("\n  Para revisar a mano (prensa y redes; «A» = sólo el anterior los junta):")
    for x, y in casos:
        quien = "A" if (x, y) in solo_a else "B"
        print(f"  [{quien}] {x}: {texto.get(x)}\n       {y}: {texto.get(y)}")


async def reproducir(ruta: Path, modo: str, paso_min: int) -> int:
    from app.core.database import dispose_engine

    host = _base_desechable()
    senales = _leer(ruta)
    if not senales:
        print("el archivo no tiene señales", file=sys.stderr)
        return 2
    paso = timedelta(minutes=paso_min)
    print(f"{len(senales)} señales, {senales[0]['ingested_at']:%d-%m %H:%M} → "
          f"{senales[-1]['ingested_at']:%d-%m %H:%M}, base {host}, paso {paso_min} min")
    try:
        resultados = {}
        if modo in ("anterior", "comparar"):
            resultados["anterior"] = await _una_corrida(senales, perfiles=False, paso=paso)
            _informe("motor anterior", resultados["anterior"])
        if modo in ("calibrado", "comparar"):
            resultados["calibrado"] = await _una_corrida(senales, perfiles=True, paso=paso)
            _informe("motor calibrado", resultados["calibrado"])
        if modo == "comparar":
            _comparar(resultados["anterior"], resultados["calibrado"], senales)
    finally:
        await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="accion", required=True)

    exp = sub.add_parser("exportar", help="señales de producción → JSONL (sólo lectura)")
    exp.add_argument("--dias", type=int, default=14)
    exp.add_argument("--salida", type=Path, default=Path("senales.jsonl"))

    rep = sub.add_parser("reproducir", help="JSONL → motor en una base desechable")
    rep.add_argument("archivo", type=Path)
    rep.add_argument("--modo", choices=("anterior", "calibrado", "comparar"), default="comparar")
    rep.add_argument("--paso-min", type=int, default=10)
    rep.add_argument("--confirmo-base-desechable", action="store_true")

    args = parser.parse_args(argv)
    if args.accion == "exportar":
        return asyncio.run(exportar(args.dias, args.salida))
    if not args.confirmo_base_desechable:
        parser.error("reproducir BORRA incidentes y señales de la base: agregar --confirmo-base-desechable")
    return asyncio.run(reproducir(args.archivo, args.modo, args.paso_min))


if __name__ == "__main__":
    sys.exit(main())
