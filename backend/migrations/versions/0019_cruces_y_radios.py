"""Precisión del punto del incidente y radios de aviso por categoría (§K).

Qué agrega
----------
* `incidents.ubicacion_precision`: qué tan fino es el punto del incidente
  (`intersection`, `exacta`, `street` o `sector`). Lo escribe el motor en
  `_refresh`; la ficha lo usa para decir «en el cruce de…» o «aproximado».
* `push_subscriptions.radios`: radio de aviso por categoría, en metros
  (`{"fire": 5000, "power": 1000, …}`). `0` = no avisar esa categoría. Una
  clave ausente usa el valor por defecto del servidor (`PUSH_RADIO_*_M`).
* `push_deliveries.kind` admite `water_cut`: los cortes de agua se avisan.

Sobre el downgrade
------------------
Borra las dos columnas y vuelve a restringir `kind`, borrando antes los envíos
de cortes de agua (sin eso la restricción vieja no se podría crear).

Revision ID: 0019_cruces_y_radios
Revises: 0018_quorum_ciudadano
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_cruces_y_radios"
down_revision: str | None = "0018_quorum_ciudadano"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.incidents "
        "ADD COLUMN IF NOT EXISTS ubicacion_precision VARCHAR(16)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_subscriptions "
        "ADD COLUMN IF NOT EXISTS radios JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_push_subscriptions_radios'
            ) THEN
                ALTER TABLE {SCHEMA}.push_subscriptions
                    ADD CONSTRAINT ck_push_subscriptions_radios
                    CHECK (jsonb_typeof(radios) = 'object');
            END IF;
        END $$;
        """
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries DROP CONSTRAINT IF EXISTS ck_push_deliveries_kind"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries ADD CONSTRAINT ck_push_deliveries_kind "
        "CHECK (kind IN ('incident', 'seismic', 'water_cut'))"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.push_deliveries WHERE kind = 'water_cut'")
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries DROP CONSTRAINT IF EXISTS ck_push_deliveries_kind"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries ADD CONSTRAINT ck_push_deliveries_kind "
        "CHECK (kind IN ('incident', 'seismic'))"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.push_subscriptions
            DROP CONSTRAINT IF EXISTS ck_push_subscriptions_radios,
            DROP COLUMN IF EXISTS radios
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.incidents DROP COLUMN IF EXISTS ubicacion_precision")
