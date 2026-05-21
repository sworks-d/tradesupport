"""Alembic マイグレーション環境（SYSTEM_DESIGN.md §2.5）。

DB URL は ``DB_PATH`` 環境変数（既定 ``~/.trading-agent/db.sqlite``）から組み立てる。
``target_metadata`` は ``SQLModel.metadata``（全テーブルを autogenerate 対象にする）。
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# 全テーブルを metadata に登録（副作用 import）
import trading_agent.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    db_path = os.environ.get("DB_PATH", "~/.trading-agent/db.sqlite")
    return f"sqlite:///{Path(db_path).expanduser()}"


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """オフライン（URL のみ）でマイグレーションを実行する。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite の ALTER 制約に対応
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """オンライン（エンジン接続）でマイグレーションを実行する。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
