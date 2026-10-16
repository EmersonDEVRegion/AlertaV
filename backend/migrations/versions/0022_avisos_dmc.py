"""Avisos meteorológicos de la DMC: la fuente `dmc` y el tipo `weather_warning`.

Qué agrega
----------
* `event_source += 'dmc'`: la Dirección Meteorológica de Chile.
* `event_type += 'weather_warning'`: un aviso, alerta o alarma del pronóstico
  oficial.
* Una fila en `source_confidence` (1.00, oficial): la DMC es la autoridad del
  pronóstico. El 1.0 dice que el aviso existe, no que el fenómeno ocurra.

Sin tabla nueva. `weather_warning` queda fuera de `CORRELATABLE_EVENT_TYPES`,
como `water_cut`: el detalle del aviso viaja en `raw_data["_dmc"]`, que ya
tiene índice GIN, y la vigencia la decide `_dmc.visto_en`.

`ALTER TYPE ... ADD VALUE` va fuera de la transacción y el downgrade no puede
quitar valores de un enum: ver la 0003 y la 0014.

Revision ID: 0022_avisos_dmc
Revises: 0021_osm_cruces
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_avisos_dmc"
down_revision: str | None = "0021_osm_cruces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'dmc'")
        op.execute(f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'weather_warning'")

    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'dmc', 1.00, TRUE,
            'Direccion Meteorologica de Chile. Autoridad del pronostico oficial: '
            'confirma que el AVISO existe, no que el fenomeno ocurra. '
            'weather_warning es capa de contexto y no correlaciona.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.source_confidence WHERE source = 'dmc'")
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'dmc'")
    op.execute(f"DELETE FROM {SCHEMA}.collector_runs WHERE source = 'dmc'")
