"""Vínculos entre familias: «cerca y poco después».

Qué agrega
----------
* `incident_relations`: un par por fila entre un incidente y otro incidente
  (choque ↔ corte de luz) o una señal que no crea incidentes (deslizamiento ↔
  corte de ruta), con la regla que lo propuso, la distancia y el desfase.

Un vínculo no funde nada ni mueve la confianza: los incidentes siguen separados
por familia (ver `services/correlation/engine.py`). Sólo deja dicho que dos
hechos ocurrieron cerca y con poco tiempo de diferencia. Lo escribe
`services/correlation/relaciones.py`.

El índice único va sobre expresiones porque uno de los dos destinos siempre es
nulo, y `NULL` no choca consigo mismo en un índice común.

Sobre el downgrade
------------------
Borra la tabla. Los vínculos se vuelven a calcular para lo reciente.

Revision ID: 0023_incident_relations
Revises: 0022_avisos_dmc
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_incident_relations"
down_revision: str | None = "0022_avisos_dmc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.incident_relations (
            id BIGSERIAL PRIMARY KEY,
            incident_id BIGINT NOT NULL
                REFERENCES {SCHEMA}.incidents (id) ON DELETE CASCADE,
            otro_incident_id BIGINT
                REFERENCES {SCHEMA}.incidents (id) ON DELETE CASCADE,
            otro_event_id BIGINT
                REFERENCES {SCHEMA}.raw_events (id) ON DELETE CASCADE,
            regla VARCHAR(60) NOT NULL,
            distancia_m DOUBLE PRECISION NOT NULL,
            desfase_s INTEGER NOT NULL,
            creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_incident_relations_un_destino CHECK (
                (otro_incident_id IS NULL) <> (otro_event_id IS NULL)
            ),
            CONSTRAINT ck_incident_relations_no_consigo CHECK (
                otro_incident_id IS NULL OR otro_incident_id <> incident_id
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_incident_relations_par
        ON {SCHEMA}.incident_relations (
            incident_id, regla, (COALESCE(otro_incident_id, 0)), (COALESCE(otro_event_id, 0))
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_incident_relations_otro "
        f"ON {SCHEMA}.incident_relations (otro_incident_id)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.incident_relations")
