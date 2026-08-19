"""Capa de accidentes viales: fuentes `waze` y `transporte_informa`.

Mismo criterio y mismas dos advertencias que la 0003 —`ALTER TYPE ... ADD VALUE`
en bloque de autocommit, y un downgrade que no puede quitar valores de un enum—;
están explicadas en el docstring de esa migración.

Qué NO hay acá
--------------
No hay columna nueva, ni tabla, ni índice. El aislamiento entre accidentes e
incendios —el cambio funcional de este hito— es **enteramente de consulta**: vive
en el `PARTITION BY` de `cluster_unassigned_events` y en los filtros de familia de
`find_nearest_open_incident` y `find_mergeable`. La familia se deriva del `type`
que la fila ya tiene; materializarla en una columna sería duplicar un dato que se
calcula en microsegundos y abrir la puerta a que las dos copias discrepen.

`event_type.accident` ya existía en el enum desde la 0001, así que tampoco hace
falta agregarlo: lo que faltaba era quién lo emitiera.

Revision ID: 0004_traffic_accidents
Revises: 0003_seismic
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_traffic_accidents"
down_revision: str | None = "0003_seismic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"


def upgrade() -> None:
    # -- Fuentes nuevas -------------------------------------------------------
    # Fuera de la transacción de Alembic: los INSERT de más abajo usan estos
    # valores, y PostgreSQL prohíbe usar un valor de enum en la misma transacción
    # que lo creó (`unsafe use of new value of enum type`).
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.event_source ADD VALUE IF NOT EXISTS 'waze'")
        op.execute(
            f"ALTER TYPE {SCHEMA}.event_source "
            f"ADD VALUE IF NOT EXISTS 'transporte_informa'"
        )

    # -- Confianza base de cada fuente ----------------------------------------
    # Espejo de SOURCE_BASE_CONFIDENCE en app/models/enums.py. Las notas existen
    # porque el número solo no transmite el matiz, y el matiz es lo que evita que
    # alguien recalibre a ciegas dentro de seis meses.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.source_confidence (source, base_confidence, is_official, notes)
        VALUES (
            'transporte_informa', 0.80, TRUE,
            'Canal oficial del Ministerio de Transportes. Informa el hecho, no lo '
            'constata en terreno: por eso no llega al 1.00 de Bomberos o CONAF. '
            'ATENCION: no entrega coordenadas. El punto lo reconstruye este backend '
            'geocodificando texto libre contra Nominatim, asi que esta confianza '
            'califica el HECHO, no la PRECISION del punto; el margen de la '
            'geocodificacion queda en raw_data._geocoding.'
        ),
        (
            'waze', 0.40, FALSE,
            'Reportes de conductores (feed CCP). Georreferenciados por el GPS del '
            'telefono y contemporaneos, pero sin verificar por nadie: un atasco por '
            'obras acumula confirmaciones igual que un choque. Techo 0.65 en '
            'confidence.py — un racimo de Waze prueba que ALGO interrumpe el '
            'transito, nunca que hubo un accidente.'
        )
        ON CONFLICT (source) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM {SCHEMA}.source_confidence "
        f"WHERE source IN ('waze', 'transporte_informa')"
    )
    # Las señales se borran porque sin su fuente en el catálogo quedarían
    # huérfanas de política de confianza. Los enlaces y el puntero
    # `raw_events.incident_id` caen por ON DELETE CASCADE; los incidentes que
    # queden vacíos los recoge la caducidad del motor en la siguiente pasada.
    op.execute(
        f"DELETE FROM {SCHEMA}.raw_events "
        f"WHERE source IN ('waze', 'transporte_informa')"
    )
    # 'waze' y 'transporte_informa' siguen en el enum: PostgreSQL no implementa
    # ALTER TYPE ... DROP VALUE. Ver el docstring de la 0003. Son inertes.
