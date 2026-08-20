"""`incidents.confidence` pasa de REAL a DOUBLE PRECISION.

Por qué hace falta
------------------
La regla de muerte súbita —descartar a los 5 minutos un incidente que sólo
sostiene un reporte ciudadano sin corroborar— no descartaba nada en producción.
Los reportes se quedaban clavados en el mapa con 40 % de confianza durante
horas, con el motor corriendo cada 120 s sin un solo error.

La causa era el tipo de la columna. `incidents.confidence` se creó `REAL`
(float4, 24 bits de mantisa) mientras que el motor calcula y compara en float8:

    el motor escribe            0.40
    la columna REAL guarda      0.4000000059604645   (redondeo al alza)
    la guarda comparaba         0.4000000059604645 <= 0.40  →  false

El `UPDATE` afectaba 0 filas, devolvía 0 y nadie tenía por qué sospechar: no hay
excepción, no hay log, la consulta es correcta. El defecto es simétrico y muerde
también en las lecturas: un filtro `confidence >= 0.35` esconde los incidentes
que valen exactamente 0.35, porque float4 los guarda como 0.3499999940395355.

Qué hace esta migración
-----------------------
Alinea el tipo físico con el tipo en el que el sistema piensa. `confidence` y
`alert_confidence` pasan a `DOUBLE PRECISION`, que es lo que el ORM ya declaraba
(`Float` sin precisión) y lo que el motor produce. A partir de aquí, el número
que se lee es exactamente el que se escribió.

`v_active_incidents` se recrea porque PostgreSQL no deja alterar el tipo de una
columna de la que depende una vista. La definición se copia literal de la
migración `0002`.

Qué NO hace
-----------
No toca `raw_events.confidence`, que sigue siendo `REAL`. Es la tabla que crece
—decenas de miles de señales por temporada— y `ALTER TYPE` la reescribe entera
tomando un `ACCESS EXCLUSIVE`; hacerlo en el mismo despliegue que arregla una
regresión de producción es cambiar un problema por otro. Las lecturas sobre esa
columna ya pasan por `confidence_at_least`, que es correcto con cualquiera de los
dos tipos. Queda como trabajo aparte, en una ventana de mantenimiento.

Ganancia de precisión, no de exactitud
--------------------------------------
Los valores ya escritos en float4 no se recuperan: 0.4000000059604645 seguirá
siendo 0.4000000059604645 después del `ALTER`, ahora en float8. No hace falta
recalcularlos —`confidence_at_most` los cubre— y el próximo `_refresh` de cada
incidente los reescribe con el valor exacto.

Revision ID: 0006_incident_confidence_float8
Revises: 0005_csn_seismic_provider
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_incident_confidence_float8"
down_revision: str | None = "0005_csn_seismic_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"

#: Copia literal de la vista creada en `0002`. Se repite acá —en vez de
#: importarla— porque una migración tiene que seguir aplicándose igual dentro de
#: diez versiones, aunque la vista haya cambiado tres veces para entonces.
_VIEW = f"""
CREATE OR REPLACE VIEW {SCHEMA}.v_active_incidents AS
SELECT
    i.code,
    i."type",
    i.status,
    i.lat,
    i.lon,
    i.confidence,
    i.alert_level,
    i.alert_confidence,
    i.is_official_confirmed,
    i.commune,
    i.event_count,
    i.source_count,
    i.sources,
    i.first_seen_at,
    i.last_seen_at,
    now() - i.last_seen_at AS silence
FROM {SCHEMA}.incidents i
WHERE i.status IN ('active', 'controlled')
ORDER BY i.confidence DESC, i.last_seen_at DESC;
"""


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.v_active_incidents")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.incidents
            ALTER COLUMN confidence       TYPE DOUBLE PRECISION,
            ALTER COLUMN alert_confidence TYPE DOUBLE PRECISION;
        """
    )
    op.execute(_VIEW)


def downgrade() -> None:
    # Volver a REAL pierde bits de mantisa. Se acepta porque es exactamente el
    # estado del que se viene, y porque un downgrade que no restituye el esquema
    # anterior no es un downgrade.
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.v_active_incidents")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.incidents
            ALTER COLUMN confidence       TYPE REAL,
            ALTER COLUMN alert_confidence TYPE REAL;
        """
    )
    op.execute(_VIEW)
