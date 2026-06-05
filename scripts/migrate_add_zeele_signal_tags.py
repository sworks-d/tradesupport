"""既存 DB に zeele_state.signal_tags カラムを冪等に追加するワンショット migration。

実行: .venv/bin/python scripts/migrate_add_zeele_signal_tags.py

追加対象:
  zeele_state.signal_tags (JSON, DEFAULT '[]')
    PIPELINE v3 Track B：ZEELE 候補の signal_tags（sector_rs / pead / macro_tailwind 等）。
    record-only（売買判断は変えない）。tag 別 hit率/avgR を shadow 計測するための観測軸。

非破壊（nullable 追加のみ）。SQLite の ADD COLUMN ... DEFAULT '[]' は既存行も '[]' で backfill
するため、ZeeleState を SELECT する既存クエリ（zeele_curator / scout / snapshot）が落ちない。
SQLModel(Column(JSON)) は TEXT '[]' を [] にデシリアライズする。
create_all は既存テーブルに列を足さないため migration が必要。
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
        if not _column_exists(conn, "zeele_state", "signal_tags"):
            conn.execute(
                "ALTER TABLE zeele_state ADD COLUMN signal_tags JSON DEFAULT '[]'"
            )
            conn.commit()
            n = conn.execute("SELECT count(*) FROM zeele_state").fetchone()[0]
            print(f"✅ migrated: zeele_state.signal_tags (backfilled '[]' on {n} rows)")
        else:
            print("✅ schema already up-to-date")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
