"""Alembic migration environment (P2-1)."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the repository root is importable, so core.infrastructure.database is
# resolved when running `alembic` from the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.infrastructure.database.ledger_store import meta  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# If the environment provides DATABASE_URL, let it override alembic.ini. Default
# to the local SQLite database so migration works in single-host deployments.
_database_url = os.getenv("DATABASE_URL")
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url)
elif not config.get_main_option("sqlalchemy.url"):
    db_path = os.environ.get("GRID_DB_PATH", "data/web_ui_runtime.sqlite3")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

target_metadata = meta


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
