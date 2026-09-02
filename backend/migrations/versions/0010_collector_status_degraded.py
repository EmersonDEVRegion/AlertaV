"""`collector_runs.status` acepta 'degraded'.

Qué agrega y por qué
--------------------
Un quinto estado entre `partial` y `failed`: **la corrida corrió y no ve el
presente**.

`partial` significa «rechacé filas», y como cualquier rechazo lo produce, vive
encendido en fuentes perfectamente sanas —el USGS descarta 235 sismos por
corrida por estar fuera de la región—. Eso lo inutilizó como señal de salud:
mirarlo entrena a ignorarlo.

El 2026-09-02 eso costó un accidente. El Actor de Instagram estuvo detenido dos
horas; el collector siguió leyendo el dataset de su última corrida buena y
reportando `partial` cada cinco minutos, indistinguible del USGS. Se publicó un
choque en Avenida España, la base sabía que esa capa estaba ciega, y el mapa
mostró un cero idéntico al de un día tranquilo.

Sobre el CHECK y no un ENUM
---------------------------
`status` es `text` con un CHECK, no un tipo enumerado de PostgreSQL. Se respeta
esa decisión: ampliar un CHECK es un `ALTER TABLE` transaccional y reversible,
mientras que `ALTER TYPE ... ADD VALUE` no se puede deshacer ni correr dentro de
la transacción de Alembic (ver la 0008 y la 0009, que necesitan
`autocommit_block` justamente por eso).

Por qué el nombre no se escribe a mano
--------------------------------------
La 0001 creó la tabla con el CHECK **en línea y sin nombre**, así que quien lo
bautizó fue PostgreSQL (`collector_runs_status_check`) y no la convención de
nombres del proyecto (`ck_collector_runs_status`, que es lo que produciría el
modelo). Escribir cualquiera de los dos a mano es apostar: si se yerra, el
`DROP ... IF EXISTS` no encuentra nada, **no falla**, y el `ADD` agrega una
segunda restricción junto a la vieja. El resultado es una migración que aplica
sin error y deja la base rechazando `degraded` igual que antes — un fallo que
sólo aparece en producción, la primera vez que un collector se declare ciego.

Por eso se buscan en `pg_constraint` todos los CHECK de la tabla que mencionen
la columna y se eliminan, sin depender de cómo se llamen.

El downgrade
------------
Reponer el CHECK viejo con filas en `degraded` lo haría fallar, así que primero
se reescriben esas filas a `partial`, que es donde vivían antes de esta
revisión. Es la conversión correcta: se pierde la distinción —el motivo sigue
íntegro en `error`— pero ninguna corrida se pierde.

Revision ID: 0010_collector_status_degraded
Revises: 0009_mop_vialidad
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_collector_status_degraded"
down_revision: str | None = "0009_mop_vialidad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "alertav"
TABLA = f"{SCHEMA}.collector_runs"
#: Nombre que se le da al reponerlo: el de la convención del proyecto, para que
#: a partir de acá deje de depender de quién creó la tabla.
RESTRICCION = "ck_collector_runs_status"

ESTADOS_NUEVOS = "'running', 'success', 'partial', 'degraded', 'failed'"
ESTADOS_VIEJOS = "'running', 'success', 'partial', 'failed'"


def _soltar_checks_de_status() -> None:
    """Elimina TODO CHECK de `collector_runs` que hable de `status`.

    Se consulta el catálogo en vez de nombrar la restricción porque su nombre
    depende de quién creó la tabla: la 0001 la declaró en línea y sin nombre, así
    que PostgreSQL la bautizó, y una base reconstruida desde el modelo usaría la
    convención del proyecto. Un `DROP ... IF EXISTS` con el nombre equivocado no
    falla —simplemente no hace nada— y ese silencio es el modo de fallo que hay
    que evitar acá.
    """
    op.execute(
        f"""
        DO $$
        DECLARE nombre text;
        BEGIN
            FOR nombre IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE con.contype = 'c'
                  AND rel.relname = 'collector_runs'
                  AND nsp.nspname = '{SCHEMA}'
                  AND pg_get_constraintdef(con.oid) ILIKE '%status%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE {TABLA} DROP CONSTRAINT %I', nombre
                );
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _soltar_checks_de_status()
    op.execute(
        f"ALTER TABLE {TABLA} ADD CONSTRAINT {RESTRICCION} "
        f"CHECK (status IN ({ESTADOS_NUEVOS}))"
    )


def downgrade() -> None:
    # Primero las filas, después la restricción: al revés, el ADD CONSTRAINT
    # falla contra las corridas que ya se registraron como `degraded`. Se pierde
    # la distinción, no la corrida: el motivo sigue íntegro en `error`.
    op.execute(f"UPDATE {TABLA} SET status = 'partial' WHERE status = 'degraded'")
    _soltar_checks_de_status()
    op.execute(
        f"ALTER TABLE {TABLA} ADD CONSTRAINT {RESTRICCION} "
        f"CHECK (status IN ({ESTADOS_VIEJOS}))"
    )
