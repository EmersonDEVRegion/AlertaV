-- AlertaV — Sismos del USGS
--
-- Mismo DDL que la migración 0003_seismic.py. Este archivo existe para poder
-- leer el modelo de una sentada; el que se ejecuta en cualquier entorno es
-- Alembic.
--
-- La idea que ordena el esquema:
--
--     un sismo es una señal como cualquier otra (entra a `raw_events`),
--     pero tiene dos dimensiones que ninguna otra fuente tiene.
--
-- Magnitud y profundidad no son metadatos del origen —para eso está `raw_data`—:
-- son por lo que un operador filtra. Como sólo aplican a sismos, viven en una
-- tabla satélite en vez de dejar dos columnas NULL en cada incendio de CONAF y
-- en cada detección de FIRMS, para siempre.
--
-- OJO: `ALTER TYPE ... ADD VALUE` no puede usarse en la misma transacción que lo
-- declara. Si se ejecuta este archivo a mano en psql, hacer los dos ALTER, un
-- COMMIT, y recién después el resto.

-- -- Valores nuevos de los enums existentes -----------------------------------
ALTER TYPE alertav.event_source ADD VALUE IF NOT EXISTS 'usgs';
ALTER TYPE alertav.event_type   ADD VALUE IF NOT EXISTS 'earthquake';

COMMIT;

-- -- Confianza base de la fuente ----------------------------------------------
INSERT INTO alertav.source_confidence (source, base_confidence, is_official, notes)
VALUES (
    'usgs', 1.00, TRUE,
    'Red sismológica global. El sismo es un hecho medido, no una hipótesis. '
    'NO implica que haya un siniestro en el epicentro: es contexto, causa '
    'posible de incendios, derrumbes o tsunami.'
)
ON CONFLICT (source) DO NOTHING;

-- -- Detalle sismológico ------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertav.seismic_details (
    raw_event_id      BIGINT      PRIMARY KEY
                      REFERENCES alertav.raw_events (id) ON DELETE CASCADE,
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

    -- Cotas de disparate, no de dominio: la mayor magnitud registrada es 9.5
    -- (Valdivia, 1960) y el sismo más profundo conocido ronda los 750 km.
    -- La profundidad puede ser negativa: se mide desde el nivel del mar.
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

-- El id del USGS ya vive en `raw_events.external_id` como 'usgs:<id>'; repetirlo
-- desnudo permite cruzar con el catálogo sin parsear el prefijo.
CREATE UNIQUE INDEX IF NOT EXISTS uq_seismic_details_usgs_id
    ON alertav.seismic_details (usgs_id);

-- La consulta que de verdad se hace: los sismos fuertes primero. NULLS LAST
-- porque una solución preliminar sin magnitud no encabeza ninguna lista.
CREATE INDEX IF NOT EXISTS ix_seismic_details_magnitude
    ON alertav.seismic_details (magnitude DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS ix_seismic_details_depth_km
    ON alertav.seismic_details (depth_km);

DROP TRIGGER IF EXISTS trg_seismic_details_updated_at ON alertav.seismic_details;
CREATE TRIGGER trg_seismic_details_updated_at
    BEFORE UPDATE ON alertav.seismic_details
    FOR EACH ROW EXECUTE FUNCTION alertav.set_updated_at();

-- -- Vista de apoyo -----------------------------------------------------------
CREATE OR REPLACE VIEW alertav.v_recent_seismic_events AS
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
FROM alertav.seismic_details s
JOIN alertav.raw_events e ON e.id = s.raw_event_id
ORDER BY e."timestamp" DESC;
