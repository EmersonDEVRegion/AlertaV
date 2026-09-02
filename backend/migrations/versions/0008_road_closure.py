"""Capa táctica de tránsito: el tipo de señal `road_closure`.

Qué agrega
----------
Un solo valor al enum `event_type`. Nada más: no hay fuente nueva
—`transporte_informa` entró en la 0004—, no hay tabla satélite y no hay fila en
`source_confidence`, porque la confianza se declara por FUENTE y no por tipo.

Las dos advertencias de siempre —`ALTER TYPE ... ADD VALUE` fuera de la
transacción de Alembic, y un downgrade que no puede quitar valores de un enum—
están explicadas en el docstring de la 0003.

Qué NO agrega, y por qué importa
--------------------------------
**No hay `incident_type` correspondiente.** Es la decisión de la migración y
conviene dejarla escrita acá, donde alguien la va a buscar cuando se pregunte
por qué los cortes de vía no aparecen en `incidents`.

`road_closure` es contexto, no siniestro. Una faena programada, un desvío por
obras o una restricción vehicular son hechos ciertos que alguien decidió con
anticipación; el motor de correlación existe para resolver incertidumbre por
corroboración, y acá no hay ninguna incertidumbre que resolver. Queda fuera de
`CORRELATABLE_EVENT_TYPES` por el mismo criterio que `weather_observation` y
`earthquake`.

El daño concreto que esto evita: si `road_closure` mapeara a
`IncidentType.ACCIDENT` —la tentación obvia, porque ambos son "tránsito"—
compartiría la familia `traffic`, y el radio de agrupamiento del Paso A (1500 m)
es exactamente la escala a la que un desvío por obras y un choque conviven en la
misma avenida sin tener relación alguna. El resultado sería un accidente cuya
confianza sube porque el MTT anunció una faena a tres cuadras. Eso es evidencia
fabricada, y es del tipo que nadie detecta después, porque el incidente resultante
se ve perfectamente razonable.

Sobre el downgrade
------------------
Borra las señales `road_closure` porque son íntegramente de esta capa: ninguna
existía antes de esta revisión. No toca `incidents` —no hay ninguno de este
tipo, por construcción— ni `raw_events` de otras fuentes: `transporte_informa`
sigue emitiendo accidentes y esos son de la 0004, no de acá.

Revision ID: 0008_road_closure
Revises: 0007_power_outages
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_road_closure"
down_revision: str | None = "0007_power_outages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # Fuera de la transacción de Alembic: PostgreSQL prohíbe usar un valor de
    # enum en la misma transacción que lo creó, y aunque esta migración no lo
    # use a continuación, el `autocommit_block` es la forma en que el resto del
    # historial lo hace y desviarse acá sólo crea una excepción que explicar.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_type ADD VALUE IF NOT EXISTS 'road_closure'"
        )


def downgrade() -> None:
    # Las señales tácticas se van enteras: nacieron con esta revisión y ningún
    # incidente las referencia (`road_closure` no está en
    # `CORRELATABLE_EVENT_TYPES`, así que nunca entró al motor). Los enlaces y el
    # puntero `raw_events.incident_id` caerían por ON DELETE CASCADE de todos
    # modos.
    # La columna se llama "type" y va entrecomillada: `event_type` es el nombre
    # del TIPO enum, no el de la columna. Confundirlos hace que el DELETE falle
    # con "column does not exist" en el peor momento posible, que es durante un
    # rollback.
    op.execute(f"DELETE FROM {SCHEMA}.raw_events WHERE \"type\" = 'road_closure'")
    # 'road_closure' sigue en el enum: PostgreSQL no implementa
    # ALTER TYPE ... DROP VALUE. Ver el docstring de la 0003. Es inerte mientras
    # nadie lo use.
