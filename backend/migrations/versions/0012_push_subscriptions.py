"""Notificaciones push: `push_subscriptions` y `push_deliveries`.

Qué agrega
----------
Dos tablas nuevas y nada más. No toca `raw_events` ni `incidents`: el
notificador sólo lee de ellas. Ver `app/models/push.py` para el porqué de cada
columna y `docs/notificaciones-push.md` para el flujo completo.

* `push_subscriptions` — un navegador suscrito, con su última ubicación
  (redondeada a ~110 m) y su radio de aviso.
* `push_deliveries` — un aviso por suscripción y por asunto. Su índice único es
  lo que impide avisar dos veces del mismo incendio.

Mismo criterio que las anteriores: DDL explícito en vez de `op.create_table`.

Sobre el downgrade
------------------
Borra las dos tablas. Las suscripciones no se pueden reconstruir desde el
servidor —las crea el navegador—, así que después de un downgrade y un nuevo
upgrade cada teléfono vuelve a registrarse la próxima vez que abra la app (la
PWA reenvía su suscripción en cada apertura).

Revision ID: 0012_push_subscriptions
Revises: 0011_sector_text_link
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_push_subscriptions"
down_revision: str | None = "0011_sector_text_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.push_subscriptions (
            id                   BIGSERIAL        PRIMARY KEY,
            public_id            UUID             NOT NULL DEFAULT gen_random_uuid(),
            endpoint             TEXT             NOT NULL,
            p256dh               TEXT             NOT NULL,
            auth                 TEXT             NOT NULL,
            lat                  DOUBLE PRECISION NOT NULL,
            lon                  DOUBLE PRECISION NOT NULL,
            geom                 geometry(Point, 4326)
                                 GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
            accuracy_m           DOUBLE PRECISION,
            location_updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
            radius_m             DOUBLE PRECISION NOT NULL DEFAULT 5000,
            notify_incidents     BOOLEAN          NOT NULL DEFAULT TRUE,
            notify_seismic       BOOLEAN          NOT NULL DEFAULT TRUE,
            last_success_at      TIMESTAMPTZ,
            consecutive_failures INTEGER          NOT NULL DEFAULT 0,
            created_at           TIMESTAMPTZ      NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ      NOT NULL DEFAULT now(),

            CONSTRAINT ck_push_subscriptions_lat CHECK (lat >= -90.0 AND lat <= 90.0),
            CONSTRAINT ck_push_subscriptions_lon CHECK (lon >= -180.0 AND lon <= 180.0),
            CONSTRAINT ck_push_subscriptions_radius_m
                CHECK (radius_m >= 500 AND radius_m <= 20000),
            CONSTRAINT ck_push_subscriptions_failures CHECK (consecutive_failures >= 0)
        );
        """
    )
    # El endpoint es la identidad: el navegador lo reenvía en cada apertura y el
    # registro es un upsert sobre esta columna.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_push_subscriptions_endpoint "
        f"ON {SCHEMA}.push_subscriptions (endpoint)"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_push_subscriptions_public_id "
        f"ON {SCHEMA}.push_subscriptions (public_id)"
    )
    # Sobre `geography` y no `geometry`: el notificador filtra con
    # `ST_DWithin(geom::geography, ..., metros)` y el índice tiene que ser sobre
    # la misma expresión para que el planificador lo use.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_push_subscriptions_geog "
        f"ON {SCHEMA}.push_subscriptions USING gist ((geom::geography))"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_push_subscriptions_updated_at "
        f"ON {SCHEMA}.push_subscriptions"
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_push_subscriptions_updated_at
            BEFORE UPDATE ON {SCHEMA}.push_subscriptions
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at();
        """
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.push_deliveries (
            id              BIGSERIAL        PRIMARY KEY,
            subscription_id BIGINT           NOT NULL
                            REFERENCES {SCHEMA}.push_subscriptions (id) ON DELETE CASCADE,
            kind            VARCHAR(16)      NOT NULL,
            subject_key     VARCHAR(80)      NOT NULL,
            distance_m      DOUBLE PRECISION,
            status          VARCHAR(16)      NOT NULL DEFAULT 'pending',
            http_status     INTEGER,
            created_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),
            sent_at         TIMESTAMPTZ,

            CONSTRAINT ck_push_deliveries_kind CHECK (kind IN ('incident', 'seismic')),
            CONSTRAINT ck_push_deliveries_status
                CHECK (status IN ('pending', 'sent', 'failed', 'gone'))
        );
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_push_deliveries_subject "
        f"ON {SCHEMA}.push_deliveries (subscription_id, kind, subject_key)"
    )
    # «¿A quién ya se le avisó de este incidente?», que es la pregunta del
    # anti-join del notificador visto desde el asunto.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_push_deliveries_kind_subject "
        f"ON {SCHEMA}.push_deliveries (kind, subject_key)"
    )
    # La poda por antigüedad.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_push_deliveries_created_at "
        f"ON {SCHEMA}.push_deliveries (created_at)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.push_deliveries")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.push_subscriptions")
