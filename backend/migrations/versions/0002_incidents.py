"""Motor de correlación: incidents, incident_events y folio legible.

Mismo criterio que la 0001: DDL explícito en vez de `op.create_table`. El
esquema usa columnas GENERATED ... STORED, índices parciales, GiST combinados y
una función de folio por año — nada de eso lo reproduce fielmente el
autogenerate de Alembic. El SQL es idéntico a `sql/002_incidents.sql`.

Revision ID: 0002_incidents
Revises: 0001_initial_schema
Create Date: 2026-08-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_incidents"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Tipos ---------------------------------------------------------------
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type t
                           JOIN pg_namespace n ON n.oid = t.typnamespace
                           WHERE t.typname = 'incident_type' AND n.nspname = '{SCHEMA}') THEN
                CREATE TYPE {SCHEMA}.incident_type AS ENUM (
                    'possible_fire', 'wildfire', 'structural_fire', 'flood',
                    'landslide', 'accident', 'rescue', 'other'
                );
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_type t
                           JOIN pg_namespace n ON n.oid = t.typnamespace
                           WHERE t.typname = 'incident_status' AND n.nspname = '{SCHEMA}') THEN
                CREATE TYPE {SCHEMA}.incident_status AS ENUM (
                    'active', 'controlled', 'extinguished', 'stale', 'merged', 'dismissed'
                );
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_type t
                           JOIN pg_namespace n ON n.oid = t.typnamespace
                           WHERE t.typname = 'link_method' AND n.nspname = '{SCHEMA}') THEN
                CREATE TYPE {SCHEMA}.link_method AS ENUM (
                    'spatial', 'commune_text', 'manual'
                );
            END IF;
        END
        $$;
        """
    )

    # -- Folio legible por año -----------------------------------------------
    # Una SEQUENCE de Postgres no se reinicia por año. Esta tabla sí, y el
    # incremento es atómico: el ON CONFLICT DO UPDATE serializa por bloqueo de
    # fila, así que dos workers concurrentes nunca obtienen el mismo número.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.incident_counters (
            year       INTEGER PRIMARY KEY,
            last_value BIGINT NOT NULL DEFAULT 0
        );
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.next_incident_code()
        RETURNS TEXT LANGUAGE plpgsql AS $$
        DECLARE
            y INTEGER := EXTRACT(YEAR FROM (now() AT TIME ZONE 'UTC'))::INTEGER;
            n BIGINT;
        BEGIN
            INSERT INTO {SCHEMA}.incident_counters (year, last_value)
                 VALUES (y, 1)
            ON CONFLICT (year)
                DO UPDATE SET last_value = {SCHEMA}.incident_counters.last_value + 1
              RETURNING last_value INTO n;

            RETURN 'INC-' || y::TEXT || '-' || lpad(n::TEXT, 5, '0');
        END;
        $$;
        """
    )

    # -- Incidentes ----------------------------------------------------------
    # lat/lon son NOT NULL a propósito: un incidente nace del Paso A, que sólo
    # agrupa señales georreferenciadas. Una alerta de SENAPRED sin coordenadas
    # se adjunta a un incidente existente o queda sin vincular, pero nunca
    # inventa un punto en el mapa.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.incidents (
            id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id             UUID    NOT NULL DEFAULT gen_random_uuid(),
            code                  TEXT    NOT NULL DEFAULT {SCHEMA}.next_incident_code(),
            "type"                {SCHEMA}.incident_type   NOT NULL DEFAULT 'possible_fire',
            status                {SCHEMA}.incident_status NOT NULL DEFAULT 'active',
            lat                   DOUBLE PRECISION NOT NULL,
            lon                   DOUBLE PRECISION NOT NULL,
            geom                  geometry(Point, 4326)
                                  GENERATED ALWAYS AS (
                                      ST_SetSRID(ST_MakePoint(lon, lat), 4326)
                                  ) STORED,
            confidence            REAL    NOT NULL DEFAULT 0.0,
            alert_confidence      REAL    NOT NULL DEFAULT 0.0,
            alert_level           TEXT,
            is_official_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            confidence_breakdown  JSONB   NOT NULL DEFAULT '{{}}'::jsonb,
            event_count           INTEGER NOT NULL DEFAULT 0,
            source_count          INTEGER NOT NULL DEFAULT 0,
            sources               TEXT[]  NOT NULL DEFAULT '{{}}'::text[],
            title                 TEXT,
            commune               TEXT,
            province              TEXT,
            first_seen_at         TIMESTAMPTZ NOT NULL,
            last_seen_at          TIMESTAMPTZ NOT NULL,
            resolved_at           TIMESTAMPTZ,
            correlated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            merged_into_id        BIGINT REFERENCES {SCHEMA}.incidents(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_incidents_confidence
                CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CONSTRAINT ck_incidents_alert_confidence
                CHECK (alert_confidence >= 0.0 AND alert_confidence <= 1.0),
            CONSTRAINT ck_incidents_lat CHECK (lat >= -90.0  AND lat <= 90.0),
            CONSTRAINT ck_incidents_lon CHECK (lon >= -180.0 AND lon <= 180.0),
            CONSTRAINT ck_incidents_window CHECK (last_seen_at >= first_seen_at),
            CONSTRAINT ck_incidents_counts CHECK (event_count >= 0 AND source_count >= 0),
            CONSTRAINT ck_incidents_merged_pair
                CHECK ((status = 'merged') = (merged_into_id IS NOT NULL)),
            CONSTRAINT ck_incidents_no_self_merge
                CHECK (merged_into_id IS NULL OR merged_into_id <> id),
            CONSTRAINT ck_incidents_breakdown_is_object
                CHECK (jsonb_typeof(confidence_breakdown) = 'object')
        );
        """
    )

    # -- Vínculo señal ↔ incidente -------------------------------------------
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.incident_events (
            incident_id     BIGINT NOT NULL REFERENCES {SCHEMA}.incidents(id)  ON DELETE CASCADE,
            raw_event_id    BIGINT NOT NULL REFERENCES {SCHEMA}.raw_events(id) ON DELETE CASCADE,
            link_method     {SCHEMA}.link_method NOT NULL,
            link_confidence REAL   NOT NULL DEFAULT 1.0,
            distance_m      DOUBLE PRECISION,
            matched_commune TEXT,
            note            TEXT,
            linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (incident_id, raw_event_id),
            CONSTRAINT ck_incident_events_link_confidence
                CHECK (link_confidence >= 0.0 AND link_confidence <= 1.0),
            CONSTRAINT ck_incident_events_distance
                CHECK (distance_m IS NULL OR distance_m >= 0),
            CONSTRAINT ck_incident_events_spatial_needs_distance
                CHECK (link_method <> 'spatial' OR distance_m IS NOT NULL)
        );
        """
    )

    # -- Índices -------------------------------------------------------------
    statements = [
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_code ON {SCHEMA}.incidents (code)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_public_id ON {SCHEMA}.incidents (public_id)",
        f"CREATE INDEX IF NOT EXISTS ix_incidents_geom ON {SCHEMA}.incidents USING GIST (geom)",
        f"CREATE INDEX IF NOT EXISTS ix_incidents_geom_last_seen ON {SCHEMA}.incidents USING GIST (geom, last_seen_at)",
        # El índice que atiende tanto al motor como al mapa: casi todas las
        # consultas filtran por incidentes abiertos, y los cerrados se acumulan
        # sin parar. Un parcial mantiene el índice del tamaño del problema real.
        f"CREATE INDEX IF NOT EXISTS ix_incidents_open_geom ON {SCHEMA}.incidents USING GIST (geom) WHERE status IN ('active', 'controlled')",
        f"CREATE INDEX IF NOT EXISTS ix_incidents_status_last_seen ON {SCHEMA}.incidents (status, last_seen_at DESC)",
        f"CREATE INDEX IF NOT EXISTS ix_incidents_commune ON {SCHEMA}.incidents (commune)",
        f"CREATE INDEX IF NOT EXISTS ix_incidents_merged_into_id ON {SCHEMA}.incidents (merged_into_id)",
        # Asimetría de cardinalidad del Paso A vs el Paso B:
        # una señal georreferenciada pertenece a lo sumo a UN incidente…
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_events_spatial ON {SCHEMA}.incident_events (raw_event_id) WHERE link_method = 'spatial'",
        # …pero una alerta comunal puede cubrir varios, y de hecho los cubre.
        f"CREATE INDEX IF NOT EXISTS ix_incident_events_raw_event_id ON {SCHEMA}.incident_events (raw_event_id)",
        f"CREATE INDEX IF NOT EXISTS ix_incident_events_incident_method ON {SCHEMA}.incident_events (incident_id, link_method)",
    ]
    for statement in statements:
        op.execute(statement)

    # -- Trigger de updated_at (la función ya existe desde la 0001) ----------
    op.execute(f"DROP TRIGGER IF EXISTS trg_incidents_updated_at ON {SCHEMA}.incidents")
    op.execute(
        f"""
        CREATE TRIGGER trg_incidents_updated_at
            BEFORE UPDATE ON {SCHEMA}.incidents
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.set_updated_at();
        """
    )

    # -- Cerrar el puntero desnormalizado de raw_events ----------------------
    # `raw_events.incident_id` existía desde la 0001 sin FK porque la tabla
    # destino aún no existía. Ahora sí: se convierte en una referencia real.
    # Mantiene el vínculo ESPACIAL únicamente —es el que es 1:1— y sirve para
    # filtrar señales sin pasar por la tabla intermedia.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_raw_events_incident_id_incidents'
            ) THEN
                ALTER TABLE {SCHEMA}.raw_events
                    ADD CONSTRAINT fk_raw_events_incident_id_incidents
                    FOREIGN KEY (incident_id)
                    REFERENCES {SCHEMA}.incidents(id) ON DELETE SET NULL;
            END IF;
        END
        $$;
        """
    )

    # -- Vista de apoyo ------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_active_incidents AS
        SELECT
            i.code,
            i."type",
            i.status,
            i.lat,
            i.lon,
            i.confidence,
            i.alert_level,
            i.alert_confidence,
            i.is_official_confirmed,
            i.commune,
            i.event_count,
            i.source_count,
            i.sources,
            i.first_seen_at,
            i.last_seen_at,
            now() - i.last_seen_at AS silence
        FROM {SCHEMA}.incidents i
        WHERE i.status IN ('active', 'controlled')
        ORDER BY i.confidence DESC, i.last_seen_at DESC;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.v_active_incidents")
    op.execute(
        f"ALTER TABLE {SCHEMA}.raw_events "
        f"DROP CONSTRAINT IF EXISTS fk_raw_events_incident_id_incidents"
    )
    op.execute(f"DROP TRIGGER IF EXISTS trg_incidents_updated_at ON {SCHEMA}.incidents")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.incident_events")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.incidents")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.next_incident_code()")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.incident_counters")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.link_method")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.incident_status")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.incident_type")
