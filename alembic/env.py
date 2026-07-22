from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# --- Project-specific additions ---
# These two imports connect Alembic to YOUR app instead of a blank template.
# `settings` gives us the DB URL from .env (no hardcoding, no second source
# of truth for connection strings). `Base` gives Alembic the actual table
# definitions it needs to compare against the live database schema.
from orchestrator.config import settings
from orchestrator.persistence.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# --- Project-specific addition ---
# Overrides whatever is (or isn't) in alembic.ini with the sync DB URL from
# our own settings object. This is the key fix: alembic.ini ships with a
# placeholder/blank sqlalchemy.url, and without this line, Alembic has no
# idea where your database actually is.
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Project-specific change ---
# This was `target_metadata = None` in the generated file — that's what
# made `--autogenerate` produce empty migrations. Pointing it at your
# actual Base.metadata is what lets Alembic diff your models against the
# live DB and generate real CREATE TABLE statements.
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.
    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.
    Calls to context.execute() here emit the given string to the
    script output.
    """
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
    """Run migrations in 'online' mode.
    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()