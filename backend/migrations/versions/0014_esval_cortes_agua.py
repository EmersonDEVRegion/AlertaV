"""Cortes de agua de Esval: la fuente `esval` y el tipo `water_cut`.

Qué agrega
----------
* `event_source += 'esval'`: la sanitaria de la V Región.
* `event_type += 'water_cut'`: un corte de agua potable, programado o de
  emergencia, tal como lo publica la empresa.
* Una fila en `source_confidence` (1.00, oficial), como la 0007 hizo con las
  distribuidoras eléctricas: Esval es la autoridad sobre su propia red.

No hay `incident_type` nuevo ni tabla satélite. `water_cut` queda FUERA de
`CORRELATABLE_EVENT_TYPES`: es una capa de contexto, como `road_closure`, que no
crea incidentes ni manda push. El detalle del corte —sisda, fechas, calles,
motivo y los polígonos del visor oficial— viaja en `raw_data["_esval"]`, que ya
tiene índice GIN.

Las dos advertencias de siempre —`ALTER TYPE ... ADD VALUE` fuera de la
transacción de Alembic, y un downgrade que no puede quitar valores de un enum—
están explicadas en el docstring de la 0003.

Sobre el downgrade
------------------
Borra las filas de `esval`: son íntegramente de esta capa y ningún incidente las
referencia. Filtra por `source` y no por `type`, igual que la 0013, para que el
DELETE diga exactamente lo que nació con esta revisión.

Revision ID: 0014_esval_cortes_agua
Revises: 0013_gbv_vehiculos
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_esval_cortes_agua"
down_revision: str | None = "0013_gbv_vehiculos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # Fuera de la transacción de Alembic: el INSERT de más abajo usa 'esval', y
    # PostgreSQL prohíbe usar un valor de enum en la misma transacción que lo
    # creó. Ver la 0008.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'esval'")
        op.execute(f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'water_cut'")

    # 1.00 e `is_official`, con el mismo matiz que Chilquinta y CGE: confirma el
    # CORTE, no una emergencia. Sin tildes, como las notas de la 0007.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'esval', 1.00, TRUE,
            'Sanitaria de la V Region. Autoridad sobre SU red: el corte lo '
            'registra su propio sistema. ATENCION: confirma el CORTE de agua, no '
            'una emergencia. water_cut es capa de contexto y no correlaciona.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.source_confidence WHERE source = 'esval'")
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'esval'")
    op.execute(f"DELETE FROM {SCHEMA}.collector_runs WHERE source = 'esval'")
    # 'esval' y 'water_cut' siguen en sus enums: PostgreSQL no implementa
    # ALTER TYPE ... DROP VALUE. Son inertes mientras nadie los use.
