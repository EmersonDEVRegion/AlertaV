-- AlertaV — Motor de correlación (Fase 3)
--
-- Mismo DDL que la migración 0002_incidents.py. Este archivo existe para poder
-- leer el modelo de una sentada; el que se ejecuta en cualquier entorno es
-- Alembic.
--
-- La idea que ordena el esquema:
--
--     un incidente describe el FENÓMENO;
--     una alerta de SENAPRED describe la RESPUESTA DEL ESTADO al fenómeno.
--
-- Por eso `incidents.lat/lon` son NOT NULL —el incidente nace del Paso A, que
-- sólo agrupa señales georreferenciadas— y por eso existe `alert_confidence`
-- como eje separado de `confidence`.

-- ---------------------------------------------------------------------------
-- Tipos
-- ---------------------------------------------------------------------------

CREATE TYPE alertav.incident_type AS ENUM (
    'possible_fire',    -- indicios de fuego sin confirmación institucional
    'wildfire',
    'structural_fire',
    'flood',
    'landslide',
    'accident',
    'rescue',
    'other'
);

CREATE TYPE alertav.incident_status AS ENUM (
    'active',
    'controlled',
    'extinguished',
    'stale',        -- dejaron de llegar señales; NO es "se apagó"
    'merged',       -- absorbido por otro incidente
    'dismissed'     -- descartado manualmente
);

CREATE TYPE alertav.link_method AS ENUM (
    'spatial',        -- Paso A: coincidencia geométrica medible
    'commune_text',   -- Paso B: heurística sobre el nombre de la comuna
    'manual'          -- intervención de un operador
);

-- ---------------------------------------------------------------------------
-- Folio legible por año: INC-2026-00142
-- ---------------------------------------------------------------------------
-- Una SEQUENCE de Postgres no se reinicia por año. Esta tabla sí, y el
-- incremento es atómico: el ON CONFLICT DO UPDATE serializa por bloqueo de fila,
-- así que dos workers concurrentes nunca reciben el mismo número.
--
-- Puede tener huecos si una transacción hace rollback. Es aceptable: el código
-- es una etiqueta para decir "el INC-2026-00142" por radio, no un folio contable.

CREATE TABLE alertav.incident_counters (
    year       INTEGER PRIMARY KEY,
    last_value BIGINT NOT NULL DEFAULT 0
);

CREATE OR REPLACE FUNCTION alertav.next_incident_code()
RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE
    y INTEGER := EXTRACT(YEAR FROM (now() AT TIME ZONE 'UTC'))::INTEGER;
    n BIGINT;
BEGIN
    INSERT INTO alertav.incident_counters (year, last_value)
         VALUES (y, 1)
    ON CONFLICT (year)
        DO UPDATE SET last_value = alertav.incident_counters.last_value + 1
      RETURNING last_value INTO n;

    RETURN 'INC-' || y::TEXT || '-' || lpad(n::TEXT, 5, '0');
END;
$$;

-- ---------------------------------------------------------------------------
-- Incidentes
-- ---------------------------------------------------------------------------

CREATE TABLE alertav.incidents (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id             UUID    NOT NULL DEFAULT gen_random_uuid(),
    code                  TEXT    NOT NULL DEFAULT alertav.next_incident_code(),
    "type"                alertav.incident_type   NOT NULL DEFAULT 'possible_fire',
    status                alertav.incident_status NOT NULL DEFAULT 'active',

    lat                   DOUBLE PRECISION NOT NULL,
    lon                   DOUBLE PRECISION NOT NULL,
    geom                  geometry(Point, 4326)
                          GENERATED ALWAYS AS (
                              ST_SetSRID(ST_MakePoint(lon, lat), 4326)
                          ) STORED,

    -- Confianza en que el FENÓMENO es real.
    confidence            REAL    NOT NULL DEFAULT 0.0,
    -- Confianza en el ESTADO DE ALERTA. 1.0 con una alerta vigente de SENAPRED:
    -- el acto administrativo es cierto por definición. Eje distinto del
    -- anterior, y por eso no se promedian.
    alert_confidence      REAL    NOT NULL DEFAULT 0.0,
    alert_level           TEXT,   -- roja | amarilla | temprana_preventiva | verde
    is_official_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    -- Aporte de cada fuente y techos aplicados. Un número de confianza sin su
    -- derivación no es auditable, y este número puede terminar moviendo camiones.
    confidence_breakdown  JSONB   NOT NULL DEFAULT '{}'::jsonb,

    event_count           INTEGER NOT NULL DEFAULT 0,
    source_count          INTEGER NOT NULL DEFAULT 0,
    sources               TEXT[]  NOT NULL DEFAULT '{}'::text[],

    title                 TEXT,
    commune               TEXT,
    province              TEXT,

    first_seen_at         TIMESTAMPTZ NOT NULL,
    last_seen_at          TIMESTAMPTZ NOT NULL,
    resolved_at           TIMESTAMPTZ,
    correlated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    merged_into_id        BIGINT REFERENCES alertav.incidents(id) ON DELETE SET NULL,
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

-- ---------------------------------------------------------------------------
-- Vínculo señal ↔ incidente
-- ---------------------------------------------------------------------------
-- Guardar `link_method` no es contabilidad: es lo que permite desarmar el
-- trabajo del motor. Si el Paso B resulta ruidoso, se borran sólo sus enlaces y
-- se recalcula la confianza sin tocar el Paso A.

CREATE TABLE alertav.incident_events (
    incident_id     BIGINT NOT NULL REFERENCES alertav.incidents(id)  ON DELETE CASCADE,
    raw_event_id    BIGINT NOT NULL REFERENCES alertav.raw_events(id) ON DELETE CASCADE,
    link_method     alertav.link_method NOT NULL,
    -- Qué tan seguro es EL VÍNCULO, no la señal. Una coincidencia de comuna
    -- vale menos que una de 200 metros.
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

-- ---------------------------------------------------------------------------
-- Índices
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX uq_incidents_code      ON alertav.incidents (code);
CREATE UNIQUE INDEX uq_incidents_public_id ON alertav.incidents (public_id);

CREATE INDEX ix_incidents_geom           ON alertav.incidents USING GIST (geom);
CREATE INDEX ix_incidents_geom_last_seen ON alertav.incidents USING GIST (geom, last_seen_at);

-- Casi todas las consultas filtran por incidentes abiertos, y los cerrados se
-- acumulan sin parar. Un índice parcial se mantiene del tamaño del problema real.
CREATE INDEX ix_incidents_open_geom ON alertav.incidents USING GIST (geom)
    WHERE status IN ('active', 'controlled');

CREATE INDEX ix_incidents_status_last_seen ON alertav.incidents (status, last_seen_at DESC);
CREATE INDEX ix_incidents_commune          ON alertav.incidents (commune);
CREATE INDEX ix_incidents_merged_into_id   ON alertav.incidents (merged_into_id);

-- Asimetría de cardinalidad entre los dos pasos del motor:
--
--   * Paso A — una señal georreferenciada pertenece A LO SUMO A UN incidente.
CREATE UNIQUE INDEX uq_incident_events_spatial
    ON alertav.incident_events (raw_event_id) WHERE link_method = 'spatial';
--   * Paso B — una alerta comunal puede pertenecer A VARIOS. Una alerta roja
--     para Viña del Mar cubre de verdad todos los incendios activos de Viña del
--     Mar; forzarla a elegir uno sería inventar una precisión que el acto
--     administrativo no tiene.
CREATE INDEX ix_incident_events_raw_event_id    ON alertav.incident_events (raw_event_id);
CREATE INDEX ix_incident_events_incident_method ON alertav.incident_events (incident_id, link_method);

-- ---------------------------------------------------------------------------
-- Trigger y clave foránea pendiente
-- ---------------------------------------------------------------------------

CREATE TRIGGER trg_incidents_updated_at
    BEFORE UPDATE ON alertav.incidents
    FOR EACH ROW EXECUTE FUNCTION alertav.set_updated_at();

-- `raw_events.incident_id` existía desde la 0001 sin FK porque la tabla destino
-- aún no existía. Ahora es una referencia real. Mantiene el vínculo ESPACIAL
-- únicamente —el que es 1:1— y sirve para filtrar señales sin pasar por la
-- tabla intermedia.
ALTER TABLE alertav.raw_events
    ADD CONSTRAINT fk_raw_events_incident_id_incidents
    FOREIGN KEY (incident_id) REFERENCES alertav.incidents(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Vista de apoyo
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW alertav.v_active_incidents AS
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
FROM alertav.incidents i
WHERE i.status IN ('active', 'controlled')
ORDER BY i.confidence DESC, i.last_seen_at DESC;
