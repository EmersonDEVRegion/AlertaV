"""Base declarativa de SQLAlchemy 2.0."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Convención de nombres explícita: Alembic genera migraciones estables y los
# constraints tienen el mismo nombre en el SQL manual y en el ORM.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=settings.DB_SCHEMA,
        naming_convention=NAMING_CONVENTION,
    )
