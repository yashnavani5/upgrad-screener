"""Alembic environment. Uses the engine passed by screener.db (attributes['engine']) or DATABASE_URL."""

from __future__ import annotations

from alembic import context

from screener.models import Base

config = context.config
target_metadata = Base.metadata


def _engine():
    engine = config.attributes.get("engine")
    if engine is not None:
        return engine
    from screener.db import get_engine

    return get_engine()


def run_migrations_offline() -> None:
    context.configure(url=str(_engine().url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with _engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
