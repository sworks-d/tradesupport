"""既存 DB に portfolio.decision_id カラムを冪等に追加するワンショット migration。

実行: .venv/bin/python scripts/migrate_add_portfolio_decision_id.py

追加対象:
  portfolio.decision_id (INTEGER)  ポジションを生んだ buy Decision への紐付け（C・測定正確性）

非破壊（nullable 追加のみ）。stop/time 退出時に元 Decision へ実退出損益を書き戻すため、
Portfolio→Decision の紐付けを保存する。create_all は既存テーブルに列を足さないため migration が必要。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def main() -> None:
    db = Path("data/trading.sqlite")
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(db)
    try:
        if not _column_exists(conn, "portfolio", "decision_id"):
            conn.execute("ALTER TABLE portfolio ADD COLUMN decision_id INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_portfolio_decision_id ON portfolio (decision_id)"
            )
            conn.commit()
            print("✅ migrated: portfolio.decision_id")
        else:
            print("✅ schema already up-to-date")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
