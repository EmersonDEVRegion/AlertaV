"""Sismos del USGS: valores de enum nuevos y tabla satélite `seismic_details`.

Mismo criterio que la 0001 y la 0002: DDL explícito en vez de `op.create_table`.
El SQL es idéntico a `sql/003_seismic.sql`.

Dos cosas de esta migración merecen atención antes de ejecutarla:

**1. `ALTER TYPE ... ADD VALUE` va en un bloque de autocommit.** Desde PostgreSQL
12 la sentencia puede ejecutarse dentro de una transacción, pero el valor nuevo
**no puede usarse** hasta que esa transacción confirme. Como más abajo esta misma
migración inserta la fila `('usgs', ...)` en `source_confidence`, correrlo todo
en la transacción de Alembic fallaría con `unsafe use of new value of enum type`.
`autocommit_block()` cierra la transacción, aplica los ALTER y la reabre.

**2. El downgrade no quita los valores del enum.** PostgreSQL no implementa
`ALTER TYPE ... DROP VALUE`; quitarlos exige recrear el tipo y reescribir todas
las columnas que lo usan. Se deja constancia en vez de simularlo: un `downgrade`
que dice haber revertido algo que no revirtió es peor que uno que declara su
límite. Los valores huérfanos son inertes mientras nadie los use.

Revision ID: 0003_seismic
Revises: 0002_incidents
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_seismic"
down_revision: str | None = "0002_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Valores nuevos de los enums existentes -------------------------------
    # Fuera de la transacción de Alembic; ver el docstring del módulo.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'usgs'"
        )
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'earthquake'"
        )

    # -- Confianza base de la fuente -----------------------------------------
    # 1.0 e `is_official`: una red sismológica mide el fenómeno con instrumentos.
    # La nota deja escrito el matiz que el número solo no transmite.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'usgs', 1.00, TRUE,
            'Red sismológica global. El sismo es un hecho medido, no una hipótesis. '
            'NO implica que haya un siniestro en el epicentro: es contexto, causa '
            'posible de incendios, derrumbes o tsunami.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )

    # -- Detalle sismológico --------------------------------------------------
    # Satélite 1:1 de `raw_events`. El sismo entra al sistema como una señal más
    # —con su idempotencia por (source, external_id) y su traza en
    # `collector_runs`—; esto sólo cuelga de esa fila los campos que ninguna otra
    # fuente tiene. ON DELETE CASCADE porque sin su señal el detalle no significa
    # nada.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.seismic_details (
            raw_event_id      BIGINT      PRIMARY KEY
                              REFERENCES {SCHEMA}.raw_events (id) ON DELETE CASCADE,
            usgs_id           VARCHAR(64) NOT NULL,
            magnitude         DOUBLE PRECISION,
            mag_type          VARCHAR(16),
            depth_km          DOUBLE PRECISION,
            place             TEXT,
            felt_reports      INTEGER,
            tsunami           BOOLEAN     NOT NULL DEFAULT FALSE,
            pager_alert       VARCHAR(16),
            significance      INTEGER,
            review_status     VARCHAR(16),
            usgs_url          TEXT,
            source_updated_at TIMESTAMPTZ,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT ck_seismic_details_magnitude
                CHECK (magnitude IS NULL OR (magnitude >= -2.0 AND magnitude <= 10.5)),
            CONSTRAINT ck_seismic_details_depth_km
                CHECK (depth_km IS NULL OR (depth_km >= -15.0 AND depth_km <= 800.0)),
            CONSTRAINT ck_seismic_details_felt_reports
                CHECK (felt_reports IS NULL OR felt_reports >= 0),
            CONSTRAINT ck_seismic_details_significance
                CHECK (significance IS NULL OR (significance >= 0 AND significance <= 5000)),
            CONSTRAINT ck_seismic_details_pager_alert
                CHECK (pager_alert IS NULL
                       OR pager_alert IN ('green', 'yellow', 'orange', 'red')),
            CONSTRAINT ck_seismic_details_review_status
                CHECK (review_status IS NULL
                       OR review_status IN ('automatic', 'reviewed'))
        );
        """
    )

    # El id del USGS ya es único en `raw_events.external_id` como 'usgs:<id>';
    # repetirlo aquí desnudo permite cruzar con el catálogo del USGS sin parsear
    # el prefijo, y garantiza el 1:1 aunque alguien inserte a mano.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_seismic_details_usgs_id "
        f"ON {SCHEMA}.seismic_details (usgs_id)"
    )
    # La consulta que de verdad se va a hacer: los sismos fuertes primero.
    # NULLS LAST porque una solución sin magnitud no encabeza ninguna lista.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_seismic_details_magnitude "
        f"ON {SCHEMA}.seismic_details (magnitude DESC NULLS LAST)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_seismic_details_depth_km "
        f"ON {SCHEMA}.seismic_details (depth_km)"
    )

    op.execute(
        f"DROP TRIGGER IF EXISTS trg_seismic_details_updated_at "
        f"ON {SCHEMA}.seismic_details"
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_seismic_details_updated_at
            BEFORE UPDATE ON {SCHEMA}.seismic_details
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at();
        """
    )

    # -- Vista de apoyo -------------------------------------------------------
    # Ahorra el JOIN en la consulta que se va a repetir en cada revisión manual.
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_recent_seismic_events AS
        SELECT
            s.usgs_id,
            s.magnitude,
            s.mag_type,
            s.depth_km,
            s.place,
            s.tsunami,
            s.pager_alert,
            s.review_status,
            s.usgs_url,
            e.public_id,
            e."timestamp",
            e.lat,
            e.lon,
            e.commune,
            e.ingested_at
        FROM {SCHEMA}.seismic_details s
        JOIN {SCHEMA}.raw_events e ON e.id = s.raw_event_id
        ORDER BY e."timestamp" DESC;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.v_recent_seismic_events")
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_seismic_details_updated_at "
        f"ON {SCHEMA}.seismic_details"
    )
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.seismic_details")
    op.execute(f"DELETE FROM {SCHEMA}.source_confidence WHERE source = 'usgs'")
    # Las señales de sismos quedarían con un `source` que ya nadie produce. Se
    # borran junto con la tabla que las explicaba: dejarlas sería dejar filas
    # tipadas 'earthquake' sin magnitud ni profundidad recuperables.
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'usgs'")
    # 'usgs' y 'earthquake' siguen en sus enums: PostgreSQL no sabe quitarlos.
    # Ver el docstring del módulo.
