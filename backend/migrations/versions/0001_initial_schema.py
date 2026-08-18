"""Esquema inicial del Fire Data Collector (PostGIS).

Se ejecuta el DDL explícito en vez de `op.create_table` porque el esquema usa
tres cosas que el autogenerate de Alembic no reproduce fielmente: columnas
GENERATED ... STORED, índices parciales y expresiones GiST combinadas. El SQL
de esta migración es idéntico a `sql/001_schema.sql`, que queda como
documentación legible del modelo.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Extensiones ---------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # -- Tipos ---------------------------------------------------------------
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type t
                           JOIN pg_namespace n ON n.oid = t.typnamespace
                           WHERE t.typname = 'event_source' AND n.nspname = '{SCHEMA}') THEN
                CREATE TYPE {SCHEMA}.event_source AS ENUM (
                    'citizen', 'broadcastify', 'nasa_firms', 'conaf', 'senapred',
                    'bomberos', 'municipality', 'media', 'social_media', 'weather',
                    'camera', 'other'
                );
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_type t
                           JOIN pg_namespace n ON n.oid = t.typnamespace
                           WHERE t.typname = 'event_type' AND n.nspname = '{SCHEMA}') THEN
                CREATE TYPE {SCHEMA}.event_type AS ENUM (
                    'wildfire', 'structural_fire', 'smoke', 'thermal_anomaly',
                    'dispatch', 'alert', 'evacuation', 'rescue', 'accident',
                    'flood', 'landslide', 'weather_observation', 'other', 'unknown'
                );
            END IF;
        END
        $$;
        """
    )

    # -- Tabla principal -----------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.raw_events (
            id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id    UUID                    NOT NULL DEFAULT gen_random_uuid(),
            "timestamp"  TIMESTAMPTZ             NOT NULL,
            source       {SCHEMA}.event_source   NOT NULL,
            "type"       {SCHEMA}.event_type     NOT NULL DEFAULT 'unknown',
            lat          DOUBLE PRECISION,
            lon          DOUBLE PRECISION,
            "text"       TEXT,
            external_id  TEXT,
            confidence   REAL                    NOT NULL DEFAULT 0.5,
            raw_data     JSONB                   NOT NULL DEFAULT '{{}}'::jsonb,
            geom         geometry(Point, 4326)
                         GENERATED ALWAYS AS (
                             CASE WHEN lat IS NOT NULL AND lon IS NOT NULL
                                  THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326)
                             END
                         ) STORED,
            commune      TEXT,
            province     TEXT,
            ingested_at  TIMESTAMPTZ             NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ,
            incident_id  BIGINT,
            created_at   TIMESTAMPTZ             NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ             NOT NULL DEFAULT now(),
            CONSTRAINT ck_raw_events_confidence
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CONSTRAINT ck_raw_events_lat
                CHECK (lat IS NULL OR (lat >= -90.0 AND lat <= 90.0)),
            CONSTRAINT ck_raw_events_lon
                CHECK (lon IS NULL OR (lon >= -180.0 AND lon <= 180.0)),
            CONSTRAINT ck_raw_events_latlon_pair
                CHECK ((lat IS NULL) = (lon IS NULL)),
            CONSTRAINT ck_raw_events_has_signal
                CHECK (lat IS NOT NULL OR "text" IS NOT NULL),
            CONSTRAINT ck_raw_events_raw_data_is_object
                CHECK (jsonb_typeof(raw_data) = 'object')
        );
        """
    )

    # -- Índices -------------------------------------------------------------
    statements = [
        f'CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_source_external_id ON {SCHEMA}.raw_events (source, external_id) WHERE external_id IS NOT NULL',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_geom ON {SCHEMA}.raw_events USING GIST (geom)',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_geom_timestamp ON {SCHEMA}.raw_events USING GIST (geom, "timestamp")',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_timestamp ON {SCHEMA}.raw_events ("timestamp" DESC)',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_source_timestamp ON {SCHEMA}.raw_events (source, "timestamp" DESC)',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_type_timestamp ON {SCHEMA}.raw_events ("type", "timestamp" DESC)',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_unprocessed ON {SCHEMA}.raw_events ("timestamp") WHERE processed_at IS NULL',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_incident_id ON {SCHEMA}.raw_events (incident_id) WHERE incident_id IS NOT NULL',
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_raw_data ON {SCHEMA}.raw_events USING GIN (raw_data jsonb_path_ops)',
        f"""CREATE INDEX IF NOT EXISTS ix_raw_events_text_fts ON {SCHEMA}.raw_events USING GIN (to_tsvector('spanish', COALESCE("text", '')))""",
    ]
    for statement in statements:
        op.execute(statement)

    # -- Trigger de updated_at -----------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.set_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS trg_raw_events_updated_at ON {SCHEMA}.raw_events")
    op.execute(
        f"""
        CREATE TRIGGER trg_raw_events_updated_at
            BEFORE UPDATE ON {SCHEMA}.raw_events
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at();
        """
    )

    # -- Confianza base por fuente -------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.source_confidence (
            source          {SCHEMA}.event_source PRIMARY KEY,
            base_confidence REAL        NOT NULL CHECK (base_confidence >= 0.0 AND base_confidence <= 1.0),
            is_official     BOOLEAN     NOT NULL DEFAULT FALSE,
            notes           TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes) VALUES
            ('bomberos',     1.00, TRUE,  'Fuente institucional operativa'),
            ('senapred',     1.00, TRUE,  'Alertas oficiales'),
            ('conaf',        1.00, TRUE,  'Incendios forestales confirmados'),
            ('municipality', 0.90, TRUE,  'Municipios de la región'),
            ('media',        0.70, FALSE, 'Medios de comunicación'),
            ('broadcastify', 0.65, FALSE, 'Transcripción automática: depende de la calidad del STT'),
            ('citizen',      0.50, FALSE, 'Un solo reporte. Sube al agregarse reportes cercanos'),
            ('social_media', 0.45, FALSE, 'Detección automática en RRSS'),
            ('nasa_firms',   0.55, FALSE, 'SEÑAL DE CORROBORACIÓN: una anomalía térmica no es un incendio confirmado'),
            ('camera',       0.50, FALSE, 'Detección de humo por visión computacional'),
            ('weather',      0.10, FALSE, 'Sólo contexto, no evidencia de emergencia'),
            ('other',        0.30, FALSE, NULL)
        ON CONFLICT (source) DO NOTHING;
        """
    )

    # -- Trazabilidad de collectors ------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.collector_runs (
            id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source           {SCHEMA}.event_source NOT NULL,
            collector        TEXT        NOT NULL,
            started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at      TIMESTAMPTZ,
            status           TEXT        NOT NULL DEFAULT 'running'
                             CHECK (status IN ('running', 'success', 'partial', 'failed')),
            events_fetched   INTEGER     NOT NULL DEFAULT 0,
            events_inserted  INTEGER     NOT NULL DEFAULT 0,
            events_duplicate INTEGER     NOT NULL DEFAULT 0,
            error            TEXT,
            params           JSONB       NOT NULL DEFAULT '{{}}'::jsonb
        );
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_collector_runs_source_started "
        f"ON {SCHEMA}.collector_runs (source, started_at DESC)"
    )

    # -- Vista de apoyo ------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_events_by_source_day AS
        SELECT
            date_trunc('day', "timestamp")           AS day,
            source,
            "type",
            count(*)                                 AS events,
            count(*) FILTER (WHERE geom IS NOT NULL) AS georeferenced,
            round(avg(confidence)::numeric, 3)       AS avg_confidence
        FROM {SCHEMA}.raw_events
        GROUP BY 1, 2, 3;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.v_events_by_source_day")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.collector_runs")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.source_confidence")
    op.execute(f"DROP TRIGGER IF EXISTS trg_raw_events_updated_at ON {SCHEMA}.raw_events")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.raw_events")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.set_updated_at()")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.event_type")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.event_source")
