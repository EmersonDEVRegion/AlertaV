"""Índice de cruces de calles de OpenStreetMap.

Qué agrega
----------
* `osm_cruces`: un nodo de OSM por fila donde se tocan dos vías de nombre
  distinto, con los dos nombres, su última palabra significativa (la clave de
  búsqueda) y las comunas cercanas. Permite ubicar «ARLEGUI / TRASLAVIÑA» en la
  esquina sin depender de Overpass, que desde Render falla casi siempre.
* `osm_indice`: qué versión del índice está cargada (una fila).

Las tablas nacen vacías: las llena el servicio de los workers con el release
mensual que publica GitHub Actions (`.github/workflows/osm-cruces.yml`).

Sobre el downgrade
------------------
Borra las dos tablas. El índice se recarga solo con el siguiente release.

Revision ID: 0021_osm_cruces
Revises: 0020_poda
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_osm_cruces"
down_revision: str | None = "0020_poda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.osm_cruces (
            id BIGSERIAL PRIMARY KEY,
            nodo BIGINT NOT NULL,
            nombre_a VARCHAR(200) NOT NULL,
            nombre_b VARCHAR(200) NOT NULL,
            ultima_a VARCHAR(80) NOT NULL,
            ultima_b VARCHAR(80) NOT NULL,
            comunas VARCHAR(40)[] NOT NULL,
            geom geometry(POINT, 4326) NOT NULL
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_osm_cruces_ultima_a ON {SCHEMA}.osm_cruces (ultima_a)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_osm_cruces_ultima_b ON {SCHEMA}.osm_cruces (ultima_b)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.osm_indice (
            id INTEGER PRIMARY KEY,
            version VARCHAR(80) NOT NULL,
            datos_de TIMESTAMPTZ,
            cargado_en TIMESTAMPTZ NOT NULL,
            filas INTEGER NOT NULL,
            CONSTRAINT ck_osm_indice_una_fila CHECK (id = 1)
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.osm_indice")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.osm_cruces")
