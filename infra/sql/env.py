"""alembic の実行環境。接続先は Settings から取る（DSN を設定ファイルに書かない）。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from sbp.core.config import get_settings


def _url() -> str:
    """``SBP_PG_DSN`` を psycopg3 用の URL に直して返す。"""
    dsn = get_settings().pg_dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


def run_migrations_offline() -> None:
    """SQL を標準出力に出すだけのモード（レビュー用）。"""
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """実際に接続して適用するモード。"""
    config = context.config
    config.set_main_option("sqlalchemy.url", _url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
