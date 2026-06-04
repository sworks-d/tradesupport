"""既存 DB に decisions.entry_broker_mode を冪等に追加（broker_mode 分離・gate⑥汚染防止）。

実行: .venv/bin/python scripts/migrate_add_entry_broker_mode.py

追加対象:
  decisions.entry_broker_mode (VARCHAR)  約定時点の broker_mode（paper/live）。
    gate⑥/昇格を paper（システム edge 検証）と live（実運用実績）で分離集計するため。
    filled_via=manual は live 専用ではない（paper/manual もある）ので broker_mode が必須。

非破壊（nullable 追加のみ）。モデルが当該カラムを SELECT するため実 DB にも追加が必要。
既存行は NULL（= legacy・公式集合は新規からカウント）。
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
        if not _column_exists(conn, "decisions", "entry_broker_mode"):
            conn.execute("ALTER TABLE decisions ADD COLUMN entry_broker_mode VARCHAR")
            added.append("decisions.entry_broker_mode")
        conn.commit()
    finally:
        conn.close()

    print(f"✅ migrated: {', '.join(added)}" if added else "✅ schema already up-to-date")


if __name__ == "__main__":
    main()
