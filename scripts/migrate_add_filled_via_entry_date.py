"""既存 DB に decisions.filled_via / decisions.entry_date を冪等に追加（A7）。

実行: .venv/bin/python scripts/migrate_add_filled_via_entry_date.py

追加対象:
  decisions.filled_via (VARCHAR)  約定経路（ds_dispatch / manual / paper_auto）。公式集合識別。
  decisions.entry_date (DATE)     実約定日（benchmark 起点。遅延 fill の α 歪み防止）。

非破壊（nullable 追加のみ）。モデルが当該カラムを SELECT するため実 DB にも追加が必要。
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
        added = []
        if not _column_exists(conn, "decisions", "filled_via"):
            conn.execute("ALTER TABLE decisions ADD COLUMN filled_via VARCHAR")
            added.append("decisions.filled_via")
        if not _column_exists(conn, "decisions", "entry_date"):
            conn.execute("ALTER TABLE decisions ADD COLUMN entry_date DATE")
            added.append("decisions.entry_date")
        conn.commit()
    finally:
        conn.close()

    print(f"✅ migrated: {', '.join(added)}" if added else "✅ schema already up-to-date")


if __name__ == "__main__":
    main()
