"""既存 DB に decisions.entry_signal_tags カラムを冪等に追加するワンショット migration。

実行: .venv/bin/python scripts/migrate_add_entry_signal_tags.py

追加対象:
  decisions.entry_signal_tags (JSON, DEFAULT '[]')
    PIPELINE v3 Track B：fill 時点の ZEELE signal_tags スナップショット（sector_rs / pead 等）。
    エントリ時点で固定（評価時の live join による陳腐化/look-ahead を避ける）。
    feedback_transparency で tag 別 hit率/avgR/net_excess を shadow 計測するための観測軸。

非破壊（nullable 追加のみ）。SQLite の ADD COLUMN ... DEFAULT '[]' は既存行も '[]' で backfill。
SQLModel(Column(JSON)) は TEXT '[]' を [] にデシリアライズする。
create_all は既存テーブルに列を足さないため migration が必要（Decision を SELECT する全クエリ保護）。
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
        if not _column_exists(conn, "decisions", "entry_signal_tags"):
            conn.execute(
                "ALTER TABLE decisions ADD COLUMN entry_signal_tags JSON DEFAULT '[]'"
            )
            conn.commit()
            n = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
            print(f"✅ migrated: decisions.entry_signal_tags (backfilled '[]' on {n} rows)")
        else:
            print("✅ schema already up-to-date")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
