"""Emergencias de infraestructura vial: la fuente `mop`.

Qué agrega
----------
Un solo valor al enum `event_source`. Nada más.

No hay `event_type` nuevo —`road_closure` entró en la 0008 y es exactamente lo
que esta fuente emite—, no hay tabla satélite y no hay fila en
`source_confidence`: la confianza se declara en código
(`SOURCE_BASE_CONFIDENCE`), y la de esta fuente es 0.0.

Las dos advertencias de siempre —`ALTER TYPE ... ADD VALUE` fuera de la
transacción de Alembic, y un downgrade que no puede quitar valores de un enum—
están explicadas en el docstring de la 0003.

Por qué una fuente con confianza cero merece existir
-----------------------------------------------------
La tentación es meter estas emergencias bajo `other` y ahorrarse la migración.
Sería un error de trazabilidad: `raw_events.source` es lo que permite responder
«¿de dónde salió esto?» sin abrir `raw_data`, y es lo que agrupa el desglose por
fuente de un incidente. Una fuente institucional con nombre propio, cadencia
propia y un modo de fallo propio tiene que ser distinguible en la columna, no
sólo en el JSON.

Qué NO agrega, y por qué importa
--------------------------------
**No hay `incident_type` correspondiente**, por la misma razón que la 0008: lo
que el MOP publica es infraestructura dañada, no siniestros. Una ruta socavada
hace tres semanas es un hecho cierto y comprobable; el motor de correlación
existe para resolver incertidumbre por corroboración, y acá no hay ninguna.

El daño concreto que esto evita es el mismo que describe la 0008, sólo que peor
por la duración: estas emergencias **permanecen vigentes durante semanas**. Si
`mop` aportara peso a la familia `traffic`, cada choque ocurrido en esa cuesta
durante todo ese tiempo heredaría corroboración de un derrumbe que no tiene nada
que ver. Sería un sesgo persistente y silencioso, concentrado justo en las rutas
peores — que es donde más caro sale equivocarse.

Sobre el downgrade
------------------
Borra las señales `mop` porque son íntegramente de esta capa: ninguna existía
antes de esta revisión. No toca los `road_closure` de `transporte_informa`, que
son de la 0008 y siguen siendo válidos — de ahí que el DELETE filtre por
`source` y no por `type`.

Revision ID: 0009_mop_vialidad
Revises: 0008_road_closure
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_mop_vialidad"
down_revision: str | None = "0008_road_closure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # Fuera de la transacción de Alembic: PostgreSQL prohíbe usar un valor de
    # enum en la misma transacción que lo creó. Ver la 0008.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'mop'")


def downgrade() -> None:
    # Filtra por `source` y NO por `type`: `road_closure` lo emite también
    # `transporte_informa` desde la 0008, y borrar por tipo se llevaría por
    # delante la capa táctica del MTT, que no nació con esta revisión.
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE source = 'mop'")
    # 'mop' sigue en el enum: PostgreSQL no implementa ALTER TYPE ... DROP VALUE.
    # Es inerte mientras nadie lo use.
