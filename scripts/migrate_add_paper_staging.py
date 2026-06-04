"""Phase C 段階大規模化（¥10万→¥100万）用のスキーマ追加（冪等）。

実行: .venv/bin/python scripts/migrate_add_paper_staging.py

追加対象:
  misato_treasury.target_ceiling_jpy (REAL)  staged deposit の上限（paper=¥100万 想定）。
  treasury_injection テーブル                資本注入台帳（P&L と区別・cashflow 補正用）。

非破壊（nullable/新規追加のみ）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def main() -> None:
    db = Path("data/trading.sqlite")
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(db)
    try:
        added = []
        if not _column_exists(conn, "misato_treasury", "target_ceiling_jpy"):
            conn.execute(
                "ALTER TABLE misato_treasury ADD COLUMN target_ceiling_jpy REAL DEFAULT 0.0"
            )
            added.append("misato_treasury.target_ceiling_jpy")
        # unlock 方式（codex 推奨）: 解放済み運用上限 + 多重 unlock 防止の評価件数スナップショット。
        if not _column_exists(conn, "misato_treasury", "unlocked_budget_jpy"):
            conn.execute(
                "ALTER TABLE misato_treasury ADD COLUMN unlocked_budget_jpy REAL DEFAULT 0.0"
            )
            added.append("misato_treasury.unlocked_budget_jpy")
        if not _column_exists(conn, "misato_treasury", "last_unlock_eval_n"):
            conn.execute(
                "ALTER TABLE misato_treasury ADD COLUMN last_unlock_eval_n INTEGER DEFAULT 0"
            )
            added.append("misato_treasury.last_unlock_eval_n")
        if not _table_exists(conn, "treasury_injection"):
            conn.execute(
                "CREATE TABLE treasury_injection ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "broker_mode VARCHAR, "
                "amount_jpy REAL DEFAULT 0.0, "
                "reason VARCHAR, "
                "tier_after_jpy REAL DEFAULT 0.0, "
                "created_at DATETIME)"
            )
            conn.execute(
                "CREATE INDEX ix_treasury_injection_broker_mode "
                "ON treasury_injection(broker_mode)"
            )
            added.append("treasury_injection (table)")
        conn.commit()
    finally:
        conn.close()

    print(f"✅ migrated: {', '.join(added)}" if added else "✅ schema already up-to-date")


if __name__ == "__main__":
    main()
