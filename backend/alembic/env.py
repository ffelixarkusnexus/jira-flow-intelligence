"""Alembic migration environment.

Reads `DATABASE_URL` from the app's `Settings` so migrations target the same
database as the application. Targets `Base.metadata` for autogeneration.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import get_settings
from app.db.models import Base
from app.db.types import UTCDateTime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Pull the DB URL straight from app settings. We deliberately do NOT round-trip
# through `config.set_main_option("sqlalchemy.url", ...)` — RDS passwords can
# contain `%` characters (URL-encoded specials) and ConfigParser tries to
# interpolate `%XX` as a variable substitution, which crashes alembic startup.
DATABASE_URL = get_settings().database_url

target_metadata = Base.metadata


def _render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render `UTCDateTime` columns as `sa.DateTime` in migrations.

    The `UTCDateTime` TypeDecorator only affects Python-side binding/result
    conversion; the SQL DDL is plain `DateTime`. Rendering it that way keeps
    migration files free of app-module imports.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
