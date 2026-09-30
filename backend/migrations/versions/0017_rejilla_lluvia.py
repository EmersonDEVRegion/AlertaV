"""Grilla de lluvia para el mapa de calor: `weather_grids`.

Qué agrega
----------
Una tabla nueva con una fila por grilla (hoy sólo `lluvia`): la última foto del
pronóstico de precipitación sobre una grilla regular de la región, que pinta la
capa de lluvia. No toca ninguna tabla existente.

Sobre el downgrade
------------------
Borra la tabla. La foto se vuelve a generar en la siguiente corrida.

Revision ID: 0017_rejilla_lluvia
Revises: 0016_push_lugares
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_rejilla_lluvia"
down_revision: str | None = "0016_push_lugares"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.weather_grids (
            key          VARCHAR(40)      PRIMARY KEY,
            generated_at TIMESTAMPTZ,
            model        VARCHAR(60),
            step         DOUBLE PRECISION,
            west         DOUBLE PRECISION,
            north        DOUBLE PRECISION,
            nx           INTEGER,
            ny           INTEGER,
            hours        INTEGER,
            "values"     JSONB,
            attempted_at TIMESTAMPTZ,
            error        TEXT,
            updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.weather_grids")
