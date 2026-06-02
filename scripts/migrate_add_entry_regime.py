"""既存 DB に decisions.entry_market_regime カラムを冪等に追加するワンショット migration。

実行: .venv/bin/python scripts/migrate_add_entry_regime.py

追加対象:
  decisions.entry_market_regime (VARCHAR)  エントリ時点の市場 regime（A3・ゲート⑥両局面判定）

非破壊（nullable 追加のみ）。モデル(Decision)が当該カラムを SELECT するため、実 DB にも
カラムを追加しないと既存クエリが落ちる。create_all は既存テーブルに列を足さないため migration が必要。
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
        if not _column_exists(conn, "decisions", "entry_market_regime"):
            conn.execute("ALTER TABLE decisions ADD COLUMN entry_market_regime VARCHAR")
            conn.commit()
            print("✅ migrated: decisions.entry_market_regime")
        else:
            print("✅ schema already up-to-date")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
