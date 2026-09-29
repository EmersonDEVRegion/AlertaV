"""Polígonos comunales, comunas faltantes e índices del motor.

Qué agrega
----------
1. **`comunas_region`**: los límites de las 36 comunas continentales de la
   Región de Valparaíso, cargados desde `migrations/data/comunas_v_region.geojson`
   (BCN, vía `caracena/chile-geojson`). El motor la usa como último recurso para
   la comuna de un incidente (`IncidentRepository.comuna_por_punto`).
2. **Relleno de `incidents.commune` y `province`** donde están vacías, por
   polígono. Sólo donde están vacías: una comuna que ya dijo una fuente no se
   pisa. En la consulta del 2026-09-23 eran 211 de CGE, 26 de FIRMS y un puñado
   de prensa y redes en 30 días.
3. **Dos índices**, con `CREATE INDEX CONCURRENTLY` para no bloquear las
   escrituras de los collectors mientras se construyen:

   * `ix_incidents_open_geog`, sobre `geom::geography(Point,4326)`. El motor
     filtra con `ST_DWithin(CAST(geom AS geography(POINT,4326)), …)` y el índice
     `ix_incidents_open_geom` es sobre `geometry`: no sirve para esa expresión, y
     el plan recorría el índice parcial entero. La expresión del índice tiene que
     ser **idéntica** a la de la consulta, con el tipo y el SRID, o PostgreSQL no
     la reconoce. Mismo arreglo que la 0012 hizo con `push_subscriptions`.
   * `ix_raw_events_pendientes_ingesta`, sobre `ingested_at` de las señales con
     punto y sin incidente: la ventana por hora de ingesta del motor (lo que
     llega tarde también se agrupa).

`CONCURRENTLY` no puede correr dentro de una transacción: va en un
`autocommit_block`, igual que los `ALTER TYPE` de la 0014. Si la migración se
corta a mitad de un índice concurrente, puede quedar uno `INVALID`: el
`DROP INDEX IF EXISTS` previo lo limpia al reintentar.

Sobre el downgrade
------------------
Borra los índices y la tabla. **No** deshace el relleno de comunas: no hay
forma de distinguir después una comuna rellenada de una que escribió el motor,
y una comuna de más no rompe nada.

Revision ID: 0015_indices_y_comunas
Revises: 0014_esval_cortes_agua
Create Date: 2026-09-24
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0015_indices_y_comunas"
down_revision: str | None = "0014_esval_cortes_agua"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"

DATOS = Path(__file__).resolve().parents[1] / "data" / "comunas_v_region.geojson"


def _comunas() -> list[dict[str, object]]:
    coleccion = json.loads(DATOS.read_text(encoding="utf-8"))
    filas = [
        {
            "cut": int(feature["properties"]["cut"]),
            "nombre": str(feature["properties"]["nombre"]),
            "provincia": feature["properties"].get("provincia"),
            "geom": json.dumps(feature["geometry"]),
        }
        for feature in coleccion["features"]
    ]
    if len(filas) != 36:
        raise RuntimeError(f"se esperaban 36 comunas en {DATOS.name}, hay {len(filas)}")
    return filas


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.comunas_region (
            cut        INTEGER     PRIMARY KEY,
            nombre     VARCHAR(80) NOT NULL UNIQUE,
            provincia  VARCHAR(80),
            geom       geometry(MultiPolygon, 4326) NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_comunas_region_geom "
        f"ON {SCHEMA}.comunas_region USING gist (geom)"
    )

    insertar = sa.text(
        f"""
        INSERT INTO {SCHEMA}.comunas_region (cut, nombre, provincia, geom)
        VALUES (:cut, :nombre, :provincia,
                ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)))
        ON CONFLICT (cut) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                provincia = EXCLUDED.provincia,
                geom = EXCLUDED.geom
        """
    )
    # `op.execute` con parámetros ligados y no `get_bind()`: así la migración
    # también se puede revisar en modo offline (`alembic upgrade --sql`).
    for fila in _comunas():
        op.execute(insertar.bindparams(**fila))

    # Relleno: sólo lo vacío. Con dos polígonos que se tocan en el borde (la
    # capa tiene solapes de decenas de metros en Los Andes), gana el de menor
    # CUT, igual que en `comuna_por_punto`. La provincia sólo se completa si la
    # comuna también sale del polígono o coincide con él: una provincia de otro
    # polígono al lado de una comuna que dijo la fuente sería una contradicción.
    # El título se completa igual que `build_title`: «Corte de luz — Quilpué».
    op.execute(
        f"""
        WITH poligono AS (
            SELECT DISTINCT ON (i.id) i.id, c.nombre, c.provincia
            FROM {SCHEMA}.incidents AS i
            JOIN {SCHEMA}.comunas_region AS c ON ST_Covers(c.geom, i.geom)
            WHERE (i.commune IS NULL OR i.province IS NULL)
              AND i.geom IS NOT NULL
            ORDER BY i.id, c.cut
        )
        UPDATE {SCHEMA}.incidents AS i
        SET commune  = coalesce(i.commune, p.nombre),
            province = CASE
                WHEN i.commune IS NULL OR i.commune = p.nombre
                    THEN coalesce(i.province, p.provincia)
                ELSE i.province
            END,
            title = CASE
                WHEN i.commune IS NULL AND position(' — ' IN i.title) = 0
                    THEN i.title || ' — ' || p.nombre
                ELSE i.title
            END
        FROM poligono AS p
        WHERE p.id = i.id
        """
    )

    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.ix_incidents_open_geog")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_open_geog "
            f"ON {SCHEMA}.incidents USING gist ((geom::geography(Point,4326))) "
            f"WHERE status IN ('active', 'controlled')"
        )
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.ix_raw_events_pendientes_ingesta"
        )
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_events_pendientes_ingesta "
            f"ON {SCHEMA}.raw_events (ingested_at DESC) "
            f"WHERE incident_id IS NULL AND geom IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.ix_raw_events_pendientes_ingesta"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SCHEMA}.ix_incidents_open_geog")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.comunas_region")
