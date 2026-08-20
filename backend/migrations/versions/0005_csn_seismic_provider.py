"""Red sismológica de origen en `seismic_details`, y la fuente `csn`.

Por qué hace falta
------------------
Hasta ahora el USGS era la única red y `seismic_details.usgs_id` era único a
secas. Con el CSN entrando al sistema, ese índice pasa a cubrir dos catálogos
distintos bajo el mismo espacio de nombres. Hoy no colisionarían —el USGS usa
cadenas alfanuméricas ('us6000tlm3') y el CSN numera con enteros ('379889')—
pero apoyarse en esa casualidad es construir sobre una coincidencia. La
unicidad pasa a ser por `(provider, usgs_id)`.

Qué NO hace esta migración
--------------------------
No renombra `usgs_id` a `source_event_id`, que es lo que el nombre pide a
gritos ahora que guarda ids del CSN. La razón es de despliegue, no de gusto:
`usgs_id` viaja en el JSON que la PWA ya consume en producción, así que
renombrarlo es una rotura de contrato entre backend y frontend y merece su
propio cambio coordinado. Queda anotado en el docstring del modelo.

El mismo sismo puede quedar dos veces
-------------------------------------
Uno por red, si ambas lo detectan. Es lo correcto y no un duplicado: son dos
mediciones independientes, con magnitudes y profundidades que suelen diferir.
La deduplicación entre redes —si alguna vez se quiere— es una decisión de
producto, no de esquema.

Revision ID: 0005_csn_seismic_provider
Revises: 0004_traffic_accidents
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_csn_seismic_provider"
down_revision: str | None = "0004_traffic_accidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Fuente nueva ---------------------------------------------------------
    # Fuera de la transacción de Alembic: el INSERT de más abajo usa el valor y
    # PostgreSQL prohíbe usar un valor de enum en la misma transacción que lo creó.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'csn'")

    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'csn', 1.00, TRUE,
            'Centro Sismologico Nacional (U. de Chile). Red oficial chilena: mide '
            'el fenomeno con instrumentos y su umbral de deteccion baja muy por '
            'debajo del feed global del USGS, que en Chile ignora casi todo lo '
            'menor a M4.5. Confianza 1.0 sobre el HECHO del sismo; peso 0 sobre '
            'cualquier siniestro, igual que USGS: un epicentro no es una emergencia.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )

    # -- Red de origen en el detalle sismológico ------------------------------
    # `server_default 'usgs'` rellena las filas existentes, que por definición
    # vienen todas de ahí. Se deja puesto y no se quita: un default correcto
    # ahorra un NOT NULL roto el día que alguien inserte a mano.
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.seismic_details
        ADD COLUMN IF NOT EXISTS provider VARCHAR(16) NOT NULL DEFAULT 'usgs'
        """
    )

    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_seismic_details_usgs_id")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_seismic_details_provider_event "
        f"ON {SCHEMA}.seismic_details (provider, usgs_id)"
    )


def downgrade() -> None:
    # Se borran primero los detalles del CSN: sin ellos, restaurar la unicidad
    # por `usgs_id` a secas podría fallar por un choque entre redes.
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.seismic_details
        WHERE provider <> 'usgs'
        """
    )
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_seismic_details_provider_event")
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_seismic_details_usgs_id "
        f"ON {SCHEMA}.seismic_details (usgs_id)"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.seismic_details DROP COLUMN IF EXISTS provider")

    op.execute(f"DELETE FROM {SCHEMA}.source_confidence WHERE source = 'csn'")
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'csn'")
    # 'csn' sigue en el enum: PostgreSQL no implementa ALTER TYPE ... DROP VALUE.
    # Ver el docstring de la 0003. Es inerte.
