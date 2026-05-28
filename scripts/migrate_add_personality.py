"""既存 DB に personality 系カラムを冪等に追加するワンショット migration。

実行: uv run python scripts/migrate_add_personality.py

追加対象:
  portfolio.personality (VARCHAR)               性格別 portfolio 振り分け
  decisions.personalities_filled (JSON/TEXT)    どの性格が約定したか配列
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
        if not _column_exists(conn, "portfolio", "personality"):
            conn.execute("ALTER TABLE portfolio ADD COLUMN personality VARCHAR")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_portfolio_personality ON portfolio (personality)"
            )
            added.append("portfolio.personality")
        if not _column_exists(conn, "decisions", "personalities_filled"):
            # SQLite に JSON 型は無いので TEXT で受け、SQLAlchemy 側で JSON シリアライズ
            conn.execute(
                "ALTER TABLE decisions ADD COLUMN personalities_filled TEXT DEFAULT '[]'"
            )
            added.append("decisions.personalities_filled")
        conn.commit()
    finally:
        conn.close()

    if added:
        print(f"✅ migrated: {', '.join(added)}")
    else:
        print("✅ schema already up-to-date")


if __name__ == "__main__":
    main()
