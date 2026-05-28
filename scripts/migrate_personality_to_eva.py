"""旧 personality キー（defender/aggressor/balanced）を EVA 命名（REI/ASUKA/SHINJI）に
マイグレーションする。KAWORU は新規（既存データなし）。

冪等：複数回実行しても結果は同じ。

実行: uv run python scripts/migrate_personality_to_eva.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_KEY_MAP = {
    "defender": "REI",
    "aggressor": "ASUKA",
    "balanced": "SHINJI",
}


def main() -> None:
    db = Path("data/trading.sqlite")
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(db)
    try:
        # portfolio.personality の旧キーを新キーに
        port_updates = 0
        for old, new in _KEY_MAP.items():
            cur = conn.execute(
                "UPDATE portfolio SET personality = ? WHERE personality = ?",
                (new, old),
            )
            port_updates += cur.rowcount

        # decisions.personalities_filled は JSON 配列。旧キーが含まれていれば置換
        dec_updates = 0
        rows = conn.execute(
            "SELECT id, personalities_filled FROM decisions "
            "WHERE personalities_filled IS NOT NULL AND personalities_filled != '[]'"
        ).fetchall()
        for did, raw in rows:
            try:
                lst = json.loads(raw or "[]")
            except json.JSONDecodeError:
                continue
            new_lst = [_KEY_MAP.get(x, x) for x in lst]
            if new_lst != lst:
                conn.execute(
                    "UPDATE decisions SET personalities_filled = ? WHERE id = ?",
                    (json.dumps(new_lst), did),
                )
                dec_updates += 1
        conn.commit()
    finally:
        conn.close()

    print(
        f"✅ migrated: portfolio.personality {port_updates} 行 / "
        f"decisions.personalities_filled {dec_updates} 行"
    )


if __name__ == "__main__":
    main()
