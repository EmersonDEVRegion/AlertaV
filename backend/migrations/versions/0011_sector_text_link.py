"""Vínculo por sector: el valor `sector_text` en el enum `link_method`.

Qué agrega
----------
Un solo valor al enum `link_method`. Nada más: no hay columna nueva —la clave
del sector viaja en `raw_data._extraction.sector_clave`, que es JSONB y ya tiene
su índice GIN `jsonb_path_ops`— y no hay tabla nueva.

Por qué
-------
El 2026-09-03 un incendio en Miraflores Alto (Viña del Mar) llegó por dos
fuentes que lo ubicaron a 2,5 km una de otra. El radio del Paso A (1500 m) no
podía unirlas, y el mapa mostró dos incidentes para una sola casa quemada. Lo
que las dos fuentes sí decían con las mismas palabras era el sector.

El motor ahora las une por eso, y el vínculo tiene que decir que lo hizo por
texto y no por geometría: `spatial` exige `distance_m` y promete una
coincidencia medible, y `commune_text` se reconstruye entero en cada pasada del
Paso B. Ninguno de los dos describe esto. Ver `CorrelationEngine._step_a_sector`.

Las dos advertencias de siempre —`ALTER TYPE ... ADD VALUE` fuera de la
transacción de Alembic, y un downgrade que no puede quitar valores de un enum—
están explicadas en el docstring de la 0003.

Sobre el downgrade
------------------
PostgreSQL no quita valores de un enum. El downgrade borra los vínculos
`sector_text` y suelta el puntero `raw_events.incident_id` de las señales que
sólo tenían ese vínculo, para que el motor viejo las vuelva a ver como libres;
el valor queda en el tipo, sin uso.

Revision ID: 0011_sector_text_link
Revises: 0010_collector_status_degraded
Create Date: 2026-09-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_sector_text_link"
down_revision: str | None = "0010_collector_status_degraded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # Fuera de la transacción de Alembic, como la 0008 y la 0009: PostgreSQL
    # prohíbe usar un valor de enum en la misma transacción que lo creó.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.link_method ADD VALUE IF NOT EXISTS 'sector_text'"
        )


def downgrade() -> None:
    # Primero el puntero, mientras todavía se puede saber qué señales colgaban
    # sólo de un vínculo por sector.
    op.execute(
        f"""
        UPDATE {SCHEMA}.raw_events AS e
           SET incident_id = NULL
         WHERE e.incident_id IS NOT NULL
           AND EXISTS (
                SELECT 1 FROM {SCHEMA}.incident_events AS l
                 WHERE l.raw_event_id = e.id AND l.link_method = 'sector_text'
           )
           AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.incident_events AS l
                 WHERE l.raw_event_id = e.id AND l.link_method = 'spatial'
           )
        """
    )
    op.execute(f"DELETE FROM {SCHEMA}.incident_events WHERE link_method = 'sector_text'")
