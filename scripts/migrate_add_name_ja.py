"""universe.name_ja カラムを冪等に追加し、JPX Excel から日本語名を投入する。

実行: uv run python scripts/migrate_add_name_ja.py
"""

from __future__ import annotations

import io
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
        if not _column_exists(conn, "universe", "name_ja"):
            conn.execute("ALTER TABLE universe ADD COLUMN name_ja VARCHAR")
            print("✅ added universe.name_ja")
        else:
            print("✅ universe.name_ja already exists")

        # JPX Excel から日本語名を取得して投入
        try:
            import httpx
            import pandas as pd

            url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
            print("fetching JPX Excel...")
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
            resp.raise_for_status()
            df = pd.read_excel(io.BytesIO(resp.content))
            updated = 0
            for code, name in zip(df["コード"], df["銘柄名"]):
                try:
                    code_str = str(int(code)).zfill(4)
                except (TypeError, ValueError):
                    code_str = str(code).strip()
                if not code_str or not name:
                    continue
                cur = conn.execute(
                    "UPDATE universe SET name_ja = ? WHERE ticker = ?",
                    (str(name).strip(), code_str),
                )
                updated += cur.rowcount
            conn.commit()
            print(f"✅ populated name_ja for {updated} tickers")
        except Exception as exc:
            print(f"⚠ JPX fetch failed: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
