"""Tránsito del MTT: el tipo `traffic_condition` y la forma de las calles.

Qué agrega
----------
* `event_type += 'traffic_condition'`: congestión, tránsito lento, semáforo
  apagado o normalidad informados por @TTIValparaiso. Como `road_closure`, queda
  fuera del motor: no es un siniestro. El subtipo va en `raw_data._mtt.tipo`.
* `osm_vias`: la forma de cada vía con nombre de la región, una fila por nombre
  y comuna (las piezas de OSM ya unidas y simplificadas). Sirve para dibujar un
  tramo («Viana desde Sucre hasta Von Schroeders») pegado a la calle. La llena
  el servicio del índice de OSM con el release mensual, igual que `osm_cruces`.
* `osm_indice.version_vias`: qué versión de `osm_vias` está cargada (las vías se
  agregaron al release después que las esquinas).

`ALTER TYPE ... ADD VALUE` va fuera de la transacción y el downgrade no puede
quitar valores de un enum: ver la 0003 y la 0014.

Revision ID: 0024_transito
Revises: 0023_incident_relations
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024_transito"
down_revision: str | None = "0023_incident_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'traffic_condition'")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.osm_vias (
            id BIGSERIAL PRIMARY KEY,
            nombre VARCHAR(200) NOT NULL,
            ultima VARCHAR(80) NOT NULL,
            comunas VARCHAR(40)[] NOT NULL,
            geom geometry(MULTILINESTRING, 4326) NOT NULL
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_osm_vias_ultima ON {SCHEMA}.osm_vias (ultima)")
    op.execute(f"ALTER TABLE {SCHEMA}.osm_indice ADD COLUMN IF NOT EXISTS version_vias VARCHAR(80)")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.osm_indice DROP COLUMN IF EXISTS version_vias")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.osm_vias")
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE type = 'traffic_condition'")
