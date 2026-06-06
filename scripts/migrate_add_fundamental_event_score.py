"""decisions.fundamental_event_score / event_score_version を冪等に追加（(c) 連続スコア）。

実行（適用）: .venv/bin/python scripts/migrate_add_fundamental_event_score.py --apply
ドライ（既定）: .venv/bin/python scripts/migrate_add_fundamental_event_score.py

追加対象（nullable・非破壊）:
  decisions.fundamental_event_score (REAL)  (b)イベント + news タグ由来の 0-100 連続スコア
    （50=中立・>50 net positive）。(d) が bucket 別に成績を相関測定する土台（record-only）。
  decisions.event_score_version (VARCHAR)   そのスコアの version（式・cut point とセット）。

モデルが当該カラムを SELECT/INSERT するため実 DB にも追加が**必須**。Decision モデルがカラムを
宣言した時点で、未適用 DB では `session.get(Decision,...)` の全 load が OperationalError で落ちる
（codex P1）。よって **migration は code commit と同時適用が前提**（後回し不可。ガードは
多層防御で安全保証ではない）。既存行は NULL（=(c)未処理）→ bucket_event_score が unscored 化。
--apply 時は ALTER の前に DB を timestamp 付きでバックアップする。
※ commit/--apply は master が同時実行（本スクリプトは書くだけ・既定ドライ）。
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

_COLUMNS = (
    ("fundamental_event_score", "REAL"),
    ("event_score_version", "VARCHAR"),
)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _backup_db(db: Path) -> Path:
    """ALTER 前に DB を timestamp 付きでコピー（T-a migration と同様・codex P1）。"""
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db.with_name(f"{db.name}.bak_event_score_{ts}")
    shutil.copy2(db, dst)
    return dst


def main() -> None:
    apply = "--apply" in sys.argv
    db = Path("data/trading.sqlite")
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(db)
    try:
        missing = [
            (col, typ) for col, typ in _COLUMNS
            if not _column_exists(conn, "decisions", col)
        ]
        if not missing:
            print("✅ schema already up-to-date（event_score カラム 追加済）")
            return
        if not apply:
            cols = ", ".join(f"decisions.{c} ({t})" for c, t in missing)
            print(f"[dry-run] 追加予定: {cols}")
            print("  → 適用するには --apply を付けて実行（master 承認後）")
            return
        backup = _backup_db(db)
        print(f"🛟 backup: {backup}")
        for col, typ in missing:
            conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {typ}")
        conn.commit()
        print(f"✅ migrated: {', '.join(f'decisions.{c}' for c, _ in missing)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
