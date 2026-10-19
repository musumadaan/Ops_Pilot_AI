"""Alembic environment. Migrations are raw SQL (the SQL files are the source of truth)."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from modules.common.settings import database_url

config = context.config
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.attributes.get("url") or database_url()
    engine = create_engine(url, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
