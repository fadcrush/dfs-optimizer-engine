"""
Alembic environment configuration for DFS Edge Pro.

Reads DATABASE_URL from the environment (via root .env) and runs
migrations against the same Postgres instance used by the backend.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make sure ``backend/`` is on sys.path so model imports work when running
# ``alembic`` from the backend/ directory.
# ---------------------------------------------------------------------------
_backend_dir = str(Path(__file__).resolve().parent.parent)
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
for p in (_backend_dir, _repo_root):
    if p not in sys.path:
        sys.path.insert(0, p)

# Load .env before anything else touches os.getenv
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(_repo_root) / ".env", override=False)

# ---------------------------------------------------------------------------
# Import the single declarative Base + every model module so that
# Base.metadata contains ALL tables for autogenerate to diff against.
# ---------------------------------------------------------------------------
from models.user import Base  # noqa: E402
import models.analytics  # noqa: E402, F401 — registers analytics tables on Base.metadata

target_metadata = Base.metadata

# Alembic Config object — provides access to alembic.ini values.
config = context.config

# Set up Python logging from the .ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with the runtime DATABASE_URL so credentials
# never need to live in alembic.ini.
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)
else:
    # Fallback to a local SQLite database for development / testing when
    # no Postgres connection is available.
    _sqlite_path = Path(_repo_root) / "data" / "alembic_local.db"
    _sqlite_url = f"sqlite:///{_sqlite_path}"
    config.set_main_option("sqlalchemy.url", _sqlite_url)
    import logging
    logging.getLogger("alembic.env").info(
        "DATABASE_URL not set — using local SQLite at %s", _sqlite_path,
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a live connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect to the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
