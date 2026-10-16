"""Poda del esquema: vistas e índices sin uso, `search_path` fijo y corridas retiradas.

Qué hace
--------
* Borra tres vistas que ningún código lee: `v_events_by_source_day` (0001),
  `v_active_incidents` (0002, recreada en 0006) y `v_recent_seismic_events`
  (0003). La API consulta las tablas directamente.
* Borra cinco índices que no registraron ni un uso en `pg_stat_user_indexes`
  desde que existe la base: la búsqueda de texto completo de `raw_events` (no
  hay búsqueda de texto), el de señales sin procesar, el de comuna de
  `incidents` y los de magnitud y profundidad de `seismic_details`.
* Fija `search_path = ''` en `set_updated_at()` y `next_incident_code()`. Las
  dos ya califican cada objeto con el esquema; sin un `search_path` fijo, quien
  pueda crear objetos en un esquema anterior del camino podría suplantarlos
  (asesor de seguridad de Supabase, `function_search_path_mutable`).
* Borra las corridas de `collector_runs` de `waze` y `social_media`, fuentes
  retiradas en septiembre de 2026. Las demás corridas las poda la aplicación
  (`COLLECTOR_RUNS_RETENTION_DAYS`).

Sobre el downgrade
------------------
Recrea vistas e índices tal como estaban y vuelve a dejar el `search_path` sin
fijar. Las corridas borradas no se recuperan.

Revision ID: 0020_poda
Revises: 0019_cruces_y_radios
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_poda"
down_revision: str | None = "0019_cruces_y_radios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"

_VISTAS = ("v_events_by_source_day", "v_active_incidents", "v_recent_seismic_events")

_INDICES = (
    "ix_raw_events_text_fts",
    "ix_raw_events_unprocessed",
    "ix_incidents_commune",
    "ix_seismic_details_magnitude",
    "ix_seismic_details_depth_km",
)

_FUNCIONES = ("set_updated_at()", "next_incident_code()")


def upgrade() -> None:
    for vista in _VISTAS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{vista}")
    for indice in _INDICES:
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{indice}")
    for funcion in _FUNCIONES:
        op.execute(f"ALTER FUNCTION {SCHEMA}.{funcion} SET search_path = ''")
    op.execute(
        f"DELETE FROM {SCHEMA}.collector_runs WHERE source IN ('waze', 'social_media')"
    )


def downgrade() -> None:
    for funcion in _FUNCIONES:
        op.execute(f"ALTER FUNCTION {SCHEMA}.{funcion} RESET search_path")

    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_raw_events_text_fts ON {SCHEMA}.raw_events '
        f"""USING GIN (to_tsvector('spanish', COALESCE("text", '')))"""
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_raw_events_unprocessed ON {SCHEMA}.raw_events "
        f'("timestamp") WHERE processed_at IS NULL'
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_incidents_commune ON {SCHEMA}.incidents (commune)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_seismic_details_magnitude "
        f"ON {SCHEMA}.seismic_details (magnitude DESC NULLS LAST)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_seismic_details_depth_km "
        f"ON {SCHEMA}.seismic_details (depth_km)"
    )

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
        GROUP BY 1, 2, 3
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_active_incidents AS
        SELECT
            i.code, i."type", i.status, i.lat, i.lon, i.confidence,
            i.alert_level, i.alert_confidence, i.is_official_confirmed,
            i.commune, i.event_count, i.source_count, i.sources,
            i.first_seen_at, i.last_seen_at,
            now() - i.last_seen_at AS silence
        FROM {SCHEMA}.incidents i
        WHERE i.status IN ('active', 'controlled')
        ORDER BY i.confidence DESC, i.last_seen_at DESC
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.v_recent_seismic_events AS
        SELECT
            s.usgs_id, s.magnitude, s.mag_type, s.depth_km, s.place, s.tsunami,
            s.pager_alert, s.review_status, s.usgs_url,
            e.public_id, e."timestamp", e.lat, e.lon, e.commune, e.ingested_at
        FROM {SCHEMA}.seismic_details s
        JOIN {SCHEMA}.raw_events e ON e.id = s.raw_event_id
        ORDER BY e."timestamp" DESC
        """
    )
