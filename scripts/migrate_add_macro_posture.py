"""既存 DB に decisions の Track A macro posture カラムを冪等に追加する migration。

実行: .venv/bin/python scripts/migrate_add_macro_posture.py

追加対象（全て nullable・record-only）:
  decisions.entry_breadth_score            (FLOAT)    市場 breadth（0-100）
  decisions.entry_exposure_recommendation  (VARCHAR)  NEW_ENTRY_ALLOWED/REDUCE_ONLY/CASH_PRIORITY
  decisions.macro_adjustment               (FLOAT)    exposure 由来の想定サイジング係数（未適用）

PIPELINE v3 Track A（マクロ×ミクロ配線）。entry 時点の市場 posture を shadow 計測するため。
非破壊（nullable 追加のみ）。create_all は既存テーブルに列を足さないため migration が必要
（Decision を SELECT する全クエリ保護）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_COLUMNS = (
    ("entry_breadth_score", "FLOAT"),
    ("entry_exposure_recommendation", "VARCHAR"),
    ("macro_adjustment", "FLOAT"),
)


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
        for col, sqltype in _COLUMNS:
            if not _column_exists(conn, "decisions", col):
                conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {sqltype}")
                added.append(col)
        conn.commit()
        if added:
            print(f"✅ migrated: decisions += {', '.join(added)}")
        else:
            print("✅ schema already up-to-date")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
