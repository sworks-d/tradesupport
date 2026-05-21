"""データベース接続と初期化（Task 1.0.4）。

SQLite + SQLModel。エンジン生成・全テーブル作成・settings の初期投入を提供する。
スキーマは ``SQLModel.metadata.create_all`` で一括作成（SYSTEM_DESIGN.md §2.5：
Phase 1 は Alembic 導入のみ、初期スキーマで稼働）。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# 全テーブルを metadata に登録するため import（副作用 import）
import trading_agent.models  # noqa: F401
from trading_agent.models.settings import Setting, default_setting_rows


def get_engine(db_path: str | Path, *, echo: bool = False) -> Engine:
    """SQLite エンジンを生成する（親ディレクトリは自動作成）。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", echo=echo)


def create_all(engine: Engine) -> None:
    """全テーブルを作成する（既存はスキップ）。"""
    SQLModel.metadata.create_all(engine)


def seed_default_settings(session: Session) -> int:
    """settings テーブルに未登録のデフォルト値を投入する（冪等）。

    Returns:
        新規に追加した件数。
    """
    inserted = 0
    for row in default_setting_rows():
        if session.get(Setting, row.key) is None:
            session.add(row)
            inserted += 1
    session.commit()
    return inserted


def init_database(db_path: str | Path) -> dict[str, object]:
    """DB を初期化する：全テーブル作成 + settings シード。

    Returns:
        実行結果のサマリ（db_path / tables / settings_inserted）。
    """
    engine = get_engine(db_path)
    create_all(engine)
    with Session(engine) as session:
        inserted = seed_default_settings(session)
    table_names = sorted(inspect(engine).get_table_names())
    return {
        "db_path": str(db_path),
        "tables": len(table_names),
        "table_names": table_names,
        "settings_inserted": inserted,
    }
