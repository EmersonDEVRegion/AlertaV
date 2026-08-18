-- =============================================================================
--  AlertaV — Fire Data Collector
--  001_schema.sql — Esquema base de eventos crudos (PostGIS)
--
--  Requisitos: PostgreSQL >= 14, PostGIS >= 3.1
--
--  Nota de diseño
--  --------------
--  `raw_events` es la tabla de *señales crudas*, NO de incidentes. Cada fila es
--  una observación independiente reportada por una fuente (satélite, bomberos,
--  ciudadano, alerta oficial). El motor de correlación consume esta tabla y
--  produce `incidents` (fuera del alcance de este hito).
--
--  Regla del proyecto: nunca confundir detección, reporte e incidente
--  confirmado. Por eso `type` admite 'thermal_anomaly' (FIRMS) separado de
--  'wildfire' (CONAF/Bomberos confirmado).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Extensiones
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS postgis;      -- geometrías + índices GiST
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- índice combinado geom + tiempo
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid() en PG < 13

-- -----------------------------------------------------------------------------
-- 2. Schema lógico
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS alertav;

-- -----------------------------------------------------------------------------
-- 3. Tipos enumerados
--    Se usan ENUM nativos (no CHECK) para que el planner tenga cardinalidad
--    real y para que el ORM valide en el borde. Agregar valores nuevos con
--    `ALTER TYPE ... ADD VALUE` (operación no bloqueante desde PG 12).
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type t
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE t.typname = 'event_source' AND n.nspname = 'alertav') THEN
        CREATE TYPE alertav.event_source AS ENUM (
            'citizen',        -- reporte ciudadano vía PWA
            'broadcastify',   -- transcripción de comunicaciones de radio
            'nasa_firms',     -- anomalías térmicas satelitales (MODIS / VIIRS)
            'conaf',          -- CONAF SIT
            'senapred',       -- alertas oficiales
            'bomberos',       -- despacho / SIG Bomberos
            'municipality',   -- municipios
            'media',          -- medios de comunicación
            'social_media',   -- detección automática en RRSS
            'weather',        -- contexto meteorológico
            'camera',         -- detección por cámara
            'other'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type t
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE t.typname = 'event_type' AND n.nspname = 'alertav') THEN
        CREATE TYPE alertav.event_type AS ENUM (
            'wildfire',          -- incendio forestal (confirmado por fuente)
            'structural_fire',   -- incendio estructural
            'smoke',             -- humo avistado (no confirmado)
            'thermal_anomaly',   -- detección satelital: NO es un incendio confirmado
            'dispatch',          -- despacho de unidades
            'alert',             -- alerta oficial (preventiva / roja)
            'evacuation',
            'rescue',
            'accident',
            'flood',
            'landslide',
            'weather_observation',
            'other',
            'unknown'
        );
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 4. Tabla principal: raw_events
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertav.raw_events (
    -- Identidad -------------------------------------------------------------
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id       UUID                     NOT NULL DEFAULT gen_random_uuid(),

    -- Campos requeridos por el hito -----------------------------------------
    "timestamp"     TIMESTAMPTZ              NOT NULL,
    source          alertav.event_source     NOT NULL,
    "type"          alertav.event_type       NOT NULL DEFAULT 'unknown',
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    "text"          TEXT,
    external_id     TEXT,
    confidence      REAL                     NOT NULL DEFAULT 0.5,
    raw_data        JSONB                    NOT NULL DEFAULT '{}'::jsonb,

    -- Geometría derivada -----------------------------------------------------
    -- Columna generada: se mantiene siempre sincronizada con lat/lon y nunca
    -- puede divergir. Es la que indexa PostGIS. SRID 4326 (WGS84).
    geom            geometry(Point, 4326)
                    GENERATED ALWAYS AS (
                        CASE
                            WHEN lat IS NOT NULL AND lon IS NOT NULL
                            THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326)
                        END
                    ) STORED,

    -- Enriquecimiento territorial (lo puebla un job posterior contra OSM/DPA)
    commune         TEXT,
    province        TEXT,

    -- Ciclo de vida en el pipeline ------------------------------------------
    ingested_at     TIMESTAMPTZ              NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,             -- lo marca el correlation engine
    incident_id     BIGINT,                  -- FK diferida a alertav.incidents

    created_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),

    -- Invariantes ------------------------------------------------------------
    CONSTRAINT ck_raw_events_confidence
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT ck_raw_events_lat
        CHECK (lat IS NULL OR (lat >= -90.0  AND lat <= 90.0)),
    CONSTRAINT ck_raw_events_lon
        CHECK (lon IS NULL OR (lon >= -180.0 AND lon <= 180.0)),
    -- lat y lon van juntos o no van
    CONSTRAINT ck_raw_events_latlon_pair
        CHECK ((lat IS NULL) = (lon IS NULL)),
    -- un evento sin geometría y sin texto no aporta nada al correlacionador
    CONSTRAINT ck_raw_events_has_signal
        CHECK (lat IS NOT NULL OR "text" IS NOT NULL),
    CONSTRAINT ck_raw_events_raw_data_is_object
        CHECK (jsonb_typeof(raw_data) = 'object')
);

COMMENT ON TABLE  alertav.raw_events IS
    'Señales crudas normalizadas de todas las fuentes. Una fila = una observación, NO un incidente.';
COMMENT ON COLUMN alertav.raw_events."timestamp" IS
    'Momento del evento SEGÚN LA FUENTE (no el de ingesta). Siempre TIMESTAMPTZ en UTC.';
COMMENT ON COLUMN alertav.raw_events.external_id IS
    'ID estable en el sistema de origen. Para fuentes sin ID propio (p. ej. FIRMS) se genera un hash determinista de sus atributos → idempotencia.';
COMMENT ON COLUMN alertav.raw_events.confidence IS
    'Confianza de la señal en [0,1]. NO es la confianza del incidente: eso lo calcula el confidence engine agregando señales.';
COMMENT ON COLUMN alertav.raw_events.raw_data IS
    'Payload original íntegro de la fuente. Permite reprocesar sin volver a consultar la API.';
COMMENT ON COLUMN alertav.raw_events.geom IS
    'Punto WGS84 generado desde lat/lon. Columna STORED: no se escribe manualmente.';

-- -----------------------------------------------------------------------------
-- 5. Índices
-- -----------------------------------------------------------------------------

-- 5.1 Deduplicación / idempotencia de los collectors.
--     Índice parcial: los reportes ciudadanos no tienen external_id y no deben
--     colisionar entre sí. Es el índice que usa ON CONFLICT en el upsert.
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_source_external_id
    ON alertav.raw_events (source, external_id)
    WHERE external_id IS NOT NULL;

-- 5.2 Consultas espaciales (mapa, radio de correlación).
CREATE INDEX IF NOT EXISTS ix_raw_events_geom
    ON alertav.raw_events USING GIST (geom);

-- 5.3 Correlación espacio-temporal: el motor busca "cerca en el espacio Y en
--     el tiempo". btree_gist permite un solo índice para ambas dimensiones.
CREATE INDEX IF NOT EXISTS ix_raw_events_geom_timestamp
    ON alertav.raw_events USING GIST (geom, "timestamp");

-- 5.4 Ventanas temporales y feeds.
CREATE INDEX IF NOT EXISTS ix_raw_events_timestamp
    ON alertav.raw_events ("timestamp" DESC);

CREATE INDEX IF NOT EXISTS ix_raw_events_source_timestamp
    ON alertav.raw_events (source, "timestamp" DESC);

CREATE INDEX IF NOT EXISTS ix_raw_events_type_timestamp
    ON alertav.raw_events ("type", "timestamp" DESC);

-- 5.5 Cola del correlation engine: sólo lo no procesado.
CREATE INDEX IF NOT EXISTS ix_raw_events_unprocessed
    ON alertav.raw_events ("timestamp")
    WHERE processed_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_raw_events_incident_id
    ON alertav.raw_events (incident_id)
    WHERE incident_id IS NOT NULL;

-- 5.6 Búsqueda dentro del payload original.
CREATE INDEX IF NOT EXISTS ix_raw_events_raw_data
    ON alertav.raw_events USING GIN (raw_data jsonb_path_ops);

-- 5.7 Full-text en español sobre transcripciones de Broadcastify y reportes.
CREATE INDEX IF NOT EXISTS ix_raw_events_text_fts
    ON alertav.raw_events
    USING GIN (to_tsvector('spanish', COALESCE("text", '')));

-- -----------------------------------------------------------------------------
-- 6. Trigger de updated_at
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION alertav.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_raw_events_updated_at ON alertav.raw_events;
CREATE TRIGGER trg_raw_events_updated_at
    BEFORE UPDATE ON alertav.raw_events
    FOR EACH ROW
    EXECUTE FUNCTION alertav.set_updated_at();

-- -----------------------------------------------------------------------------
-- 7. Confianza base por fuente (calibrable con datos reales)
--    Valores iniciales orientativos tomados de la definición del proyecto.
--    El collector usa esta tabla como fallback cuando la fuente no entrega
--    una confianza propia.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertav.source_confidence (
    source          alertav.event_source PRIMARY KEY,
    base_confidence REAL        NOT NULL CHECK (base_confidence >= 0.0 AND base_confidence <= 1.0),
    is_official     BOOLEAN     NOT NULL DEFAULT FALSE,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO alertav.source_confidence (source, base_confidence, is_official, notes) VALUES
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

-- -----------------------------------------------------------------------------
-- 8. Trazabilidad de ejecuciones de los collectors
--    Imprescindible para la fase de recolección de 7–14 días: permite saber si
--    un hueco en los datos fue "no pasó nada" o "el collector estaba caído".
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertav.collector_runs (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           alertav.event_source NOT NULL,
    collector        TEXT        NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    status           TEXT        NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running', 'success', 'partial', 'failed')),
    events_fetched   INTEGER     NOT NULL DEFAULT 0,
    events_inserted  INTEGER     NOT NULL DEFAULT 0,
    events_duplicate INTEGER     NOT NULL DEFAULT 0,
    error            TEXT,
    params           JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_collector_runs_source_started
    ON alertav.collector_runs (source, started_at DESC);

-- -----------------------------------------------------------------------------
-- 9. Vista de apoyo para el análisis de la ventana de recolección
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW alertav.v_events_by_source_day AS
SELECT
    date_trunc('day', "timestamp")        AS day,
    source,
    "type",
    count(*)                              AS events,
    count(*) FILTER (WHERE geom IS NOT NULL) AS georeferenced,
    round(avg(confidence)::numeric, 3)    AS avg_confidence
FROM alertav.raw_events
GROUP BY 1, 2, 3;

COMMIT;

-- =============================================================================
-- Nota de escalado (fuera del MVP)
-- -----------------------------------------------------------------------------
-- Cuando el volumen lo exija, convertir raw_events en tabla particionada por
-- RANGE ("timestamp") con particiones mensuales. Todos los índices de arriba
-- son compatibles; el único cambio es que la PK pasa a ser (id, "timestamp").
-- =============================================================================
