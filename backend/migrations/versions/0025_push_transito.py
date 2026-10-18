"""Avisos push de tránsito: `push_deliveries.kind` admite `transito`.

Qué agrega
----------
* `push_deliveries.kind += 'transito'`: congestión con tramo, semáforos
  apagados, pistas ocupadas y marchas que informa el MTT, a quien encienda la
  categoría «Tránsito» (apagada por defecto, `PUSH_RADIO_TRANSITO_M = 0`).

Las preferencias no cambian de forma: el radio de la categoría va en el mismo
JSON `push_subscriptions.radios` que las demás.

Revision ID: 0025_push_transito
Revises: 0024_transito
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_push_transito"
down_revision: str | None = "0024_transito"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries DROP CONSTRAINT IF EXISTS ck_push_deliveries_kind"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries ADD CONSTRAINT ck_push_deliveries_kind "
        "CHECK (kind IN ('incident', 'seismic', 'water_cut', 'transito'))"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.push_deliveries WHERE kind = 'transito'")
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries DROP CONSTRAINT IF EXISTS ck_push_deliveries_kind"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.push_deliveries ADD CONSTRAINT ck_push_deliveries_kind "
        "CHECK (kind IN ('incident', 'seismic', 'water_cut'))"
    )
