"""Lugares guardados para los avisos: `push_places`.

Qué agrega
----------
Una tabla nueva, `push_places`: hasta tres lugares por suscripción («Casa»,
«Trabajo»), redondeados como la ubicación (~110 m). El notificador avisa de lo
que pase cerca de la última ubicación o de cualquiera de ellos. No toca
ninguna tabla existente.

Sobre el downgrade
------------------
Borra la tabla. Los lugares viven también en el teléfono (`localStorage`), y la
PWA los reenvía con la suscripción la próxima vez que se abra.

Revision ID: 0016_push_lugares
Revises: 0015_indices_y_comunas
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_push_lugares"
down_revision: str | None = "0015_indices_y_comunas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.push_places (
            id              BIGSERIAL        PRIMARY KEY,
            subscription_id BIGINT           NOT NULL
                            REFERENCES {SCHEMA}.push_subscriptions (id) ON DELETE CASCADE,
            name            VARCHAR(40)      NOT NULL,
            lat             DOUBLE PRECISION NOT NULL,
            lon             DOUBLE PRECISION NOT NULL,
            geom            geometry(Point, 4326)
                            GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
            position        INTEGER          NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),

            CONSTRAINT ck_push_places_lat CHECK (lat >= -90.0 AND lat <= 90.0),
            CONSTRAINT ck_push_places_lon CHECK (lon >= -180.0 AND lon <= 180.0),
            CONSTRAINT ck_push_places_name CHECK (length(btrim(name)) > 0)
        );
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_push_places_subscription "
        f"ON {SCHEMA}.push_places (subscription_id)"
    )
    # Mismo criterio que `ix_push_subscriptions_geog`: el notificador filtra con
    # `ST_DWithin(geom::geography, ..., metros)`.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_push_places_geog "
        f"ON {SCHEMA}.push_places USING gist ((geom::geography))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.push_places")
