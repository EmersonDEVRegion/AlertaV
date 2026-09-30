"""Quórum ciudadano: `incidents.publico` e `incidents.ciudadanos_independientes`.

Qué agrega
----------
Dos columnas en `incidents` (§C, 2026-09-30):

* `ciudadanos_independientes`: cuántos reportes ciudadanos de dispositivos Y
  redes distintos sostienen el incidente. Lo escribe el motor en `_refresh`.
* `publico`: si el incidente se muestra en el mapa, la ficha y el historial.
  Todo lo que tenga una fuente no ciudadana es público, como antes. Lo sólo
  ciudadano, cuando junta el quórum (`CITIZEN_QUORUM`, 3).

Parte en `true` para que cualquier incidente creado por otra vía que no sea el
motor siga viéndose; el motor lo recalcula en la misma transacción en que crea
un incidente, así que uno sólo ciudadano nunca llega a verse sin quórum.

Los incidentes abiertos que hoy son sólo ciudadanos pasan a `publico = false`:
con la regla nueva ninguno tiene quórum contado. El motor los vuelve a evaluar
en su próxima pasada si reciben señales.

Sobre el downgrade
------------------
Borra las dos columnas. Con eso el mapa vuelve a mostrar los reportes sueltos.

Revision ID: 0018_quorum_ciudadano
Revises: 0017_rejilla_lluvia
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_quorum_ciudadano"
down_revision: str | None = "0017_rejilla_lluvia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.incidents
            ADD COLUMN IF NOT EXISTS publico BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN IF NOT EXISTS ciudadanos_independientes INTEGER NOT NULL DEFAULT 0
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_incidents_ciudadanos_independientes'
            ) THEN
                ALTER TABLE {SCHEMA}.incidents
                    ADD CONSTRAINT ck_incidents_ciudadanos_independientes
                    CHECK (ciudadanos_independientes >= 0);
            END IF;
        END $$;
        """
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.incidents
           SET publico = false
         WHERE status IN ('active', 'controlled')
           AND is_official_confirmed = false
           AND sources <@ ARRAY['citizen']::text[]
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.incidents
            DROP CONSTRAINT IF EXISTS ck_incidents_ciudadanos_independientes,
            DROP COLUMN IF EXISTS ciudadanos_independientes,
            DROP COLUMN IF EXISTS publico
        """
    )
