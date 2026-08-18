"""Entorno de Alembic.

Usa el driver síncrono (psycopg2) a propósito: las migraciones son un proceso
puntual y el modo sync evita toda la complejidad de un loop async en CLI.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.core.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Objetos que Alembic no debe intentar gestionar.
_EXCLUDED_TABLES = {"spatial_ref_sys"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Objetos que Alembic no debe intentar gestionar."""
    if type_ == "table" and name in _EXCLUDED_TABLES:
        return False
    # GeoAlchemy2 crea sus propios índices espaciales; aquí se declaran a mano.
    is_geoalchemy_index = (
        type_ == "index"
        and bool(name)
        and name.startswith("idx_")
        and name.endswith("_geom")
    )
    return not is_geoalchemy_index


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table_schema=settings.DB_SCHEMA,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # La tabla `alembic_version` vive dentro del schema del proyecto, así que
        # el schema debe existir ANTES de que Alembic intente crearla. Sin esto,
        # `alembic upgrade head` falla contra una base recién creada.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {settings.DB_SCHEMA}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=settings.DB_SCHEMA,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
