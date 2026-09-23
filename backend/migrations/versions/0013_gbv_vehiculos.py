"""Feed de vehículos de GBV: la fuente `gbv` y el tipo `vehicle_report`.

Qué agrega
----------
Dos valores de enum y nada más:

* `event_source += 'gbv'`: GBV SpA (Grupo Búsqueda de Vehículos), que publica
  denuncias de vehículos robados, recuperados y abandonados.
* `event_type += 'vehicle_report'`: el aviso sobre un vehículo.

No hay tabla satélite —los campos del vehículo (patente, marca, estado, comuna)
viajan en `raw_data["gbv"]`, que ya tiene índice GIN— ni fila en
`source_confidence`: la confianza se declara en código y es 0.0.

Por qué un tipo nuevo y no `other`
-----------------------------------
`other` y `unknown` están en `CORRELATABLE_EVENT_TYPES`, y `services/backfill.py`
geocodifica contra Nominatim las señales sin coordenadas de esos tipos. Un auto
robado etiquetado `other` consumiría la cuota de geocodificación y podría
terminar abriendo un incidente en el mapa. `vehicle_report` queda fuera del
motor, del backfill y del mapa: sólo lo lee `GET /feed/vehiculos`.

Las dos advertencias de siempre —`ALTER TYPE ... ADD VALUE` fuera de la
transacción de Alembic, y un downgrade que no puede quitar valores de un enum—
están explicadas en el docstring de la 0003.

Sobre el downgrade
------------------
Borra las filas de `gbv`: son íntegramente de esta capa y ningún incidente las
referencia. Filtra por `source` y no por `type` para que el DELETE diga
exactamente lo que nació con esta revisión, aunque hoy coincidan.

Revision ID: 0013_gbv_vehiculos
Revises: 0012_push_subscriptions
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_gbv_vehiculos"
down_revision: str | None = "0012_push_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # Fuera de la transacción de Alembic: PostgreSQL prohíbe usar un valor de
    # enum en la misma transacción que lo creó. Ver la 0008.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'gbv'")
        op.execute(f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'vehicle_report'")


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'gbv'")
    op.execute(f"DELETE FROM {SCHEMA}.collector_runs WHERE source = 'gbv'")
    # 'gbv' y 'vehicle_report' siguen en sus enums: PostgreSQL no implementa
    # ALTER TYPE ... DROP VALUE. Son inertes mientras nadie los use.
