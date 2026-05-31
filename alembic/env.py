import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Make src/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from voiceagent.db.models import Base
from voiceagent.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """Convert asyncpg URL to psycopg2-compatible sync URL for Alembic."""
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgres://", "postgresql://")
    )


def run_migrations_offline() -> None:
    url = _sync_url(settings.database_url)
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
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _sync_url(settings.database_url)

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
