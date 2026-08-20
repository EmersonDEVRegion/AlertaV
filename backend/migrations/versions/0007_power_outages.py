"""Capa de cortes de suministro eléctrico: fuentes y tipos nuevos.

Mismas dos advertencias que la 0003 y la 0004 —`ALTER TYPE ... ADD VALUE` en
bloque de autocommit, y un downgrade que no puede quitar valores de un enum—;
están explicadas en el docstring de la 0003.

Qué NO hay acá
--------------
No hay tabla satélite tipo `seismic_details`. Se evaluó y se descartó: los tres
campos propios de un corte —empresa, clientes afectados y hora estimada de
reposición— caben en `raw_data`, y ninguno necesita índice ni CHECK propio. La
tabla satélite del USGS existe porque un sismo tiene una decena de parámetros
con restricciones (rango de magnitud, niveles PAGER, estados de revisión) y
porque el mapa los consulta por sí solos; un corte no.

Si mañana hace falta consultar "cortes con más de N clientes afectados" de forma
eficiente, ese es el momento de materializarlo — no antes.

Revision ID: 0007_power_outages
Revises: 0006_incident_confidence_float8
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_power_outages"
down_revision: str | None = "0006_incident_confidence_float8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Valores nuevos de los enums existentes -------------------------------
    # Fuera de la transacción de Alembic: los INSERT de más abajo usan estos
    # valores, y PostgreSQL prohíbe usar un valor de enum en la misma
    # transacción que lo creó.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'chilquinta'"
        )
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'cge'")
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'power_outage'"
        )
        op.execute(
            f"ALTER TYPE {SCHEMA}.incident_type ADD VALUE IF NOT EXISTS 'power_outage'"
        )

    # -- Confianza base de cada distribuidora ---------------------------------
    # 1.00 e `is_official`, con el matiz escrito en la nota: son autoridad sobre
    # el CORTE, no sobre su causa. El número solo no transmite ese límite y es
    # justo el que evita que alguien lea un corte como evidencia de incendio.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'chilquinta', 1.00, TRUE,
            'Distribuidora electrica de la V Region. Autoridad sobre SU red: el '
            'corte lo registran sus propios equipos, no es una observacion '
            'indirecta que pueda equivocarse. ATENCION: confirma el CORTE, no su '
            'causa. Un corte no es evidencia de incendio ni de accidente; la '
            'familia power esta aislada del resto por esa razon.'
        ),
        (
            'cge', 1.00, TRUE,
            'Distribuidora electrica con presencia en la V Region. Mismo criterio '
            'que Chilquinta: autoridad sobre el corte en su propia red, no sobre '
            'lo que lo provoco.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM {SCHEMA}.source_confidence "
        f"WHERE source IN ('chilquinta', 'cge')"
    )
    # Las señales se borran porque sin su fuente en el catálogo quedarían
    # huérfanas de política de confianza. Los enlaces y el puntero
    # `raw_events.incident_id` caen por ON DELETE CASCADE.
    op.execute(
        f"DELETE FROM {SCHEMA}.raw_events WHERE source IN ('chilquinta', 'cge')"
    )
    # Los incidentes de esta familia dejan de tener sustento: se descartan en vez
    # de quedar como puntos sin señales que los expliquen.
    op.execute(
        f"""
        UPDATE {SCHEMA}.incidents
        SET status = 'dismissed'
        WHERE type = 'power_outage'
        """
    )
    # 'chilquinta', 'cge' y 'power_outage' siguen en sus enums: PostgreSQL no
    # implementa ALTER TYPE ... DROP VALUE. Ver el docstring de la 0003. Son
    # inertes mientras nadie los use.
