"""T-a: 観測台帳の孤児 filled 12件を SKIP マーキングする冪等 migration（3者一致裁定: SKIP）.

■背景と裁定
filled 12件（date=2026-05-31・status=filled・entry_date 等が全 NULL）は **active Portfolio に紐付かない
放棄記録**（Portfolio.decision_id リンク 0件・対象 ticker の Portfolio は paper closed のみ・active 0件）。
当初 backfill 案だったが、entry_date/evaluation_date/filled_via を後付けすると、経済露出のない/復元不能な
decision を 2026-08-29 採点母数に戻し **gate⑥/unlock 分母を汚染**する（幻データ）。
→ codex + master + pipelineCK の 3者独立一致で **SKIP**（評価対象外に明示マーキング）。
「entry_price がある＝実測に戻してよい」ではない（Portfolio リンクが無い以上、公式成績に戻す根拠がない）。

■対象（厳密一致・広く当てない）
  status='filled' AND entry_date IS NULL AND date='2026-05-31' AND hit_or_miss='pending'   （= 12件）

■set（最小・公式値に寄せない）
  hit_or_miss = 'skipped'
  evaluated_at = now（UTC・既存 evaluated_at と同形式の naive datetime 文字列）
  user_note    = 'legacy_orphan_no_portfolio_link'

■触らない（backfill しない）
  entry_date / evaluation_date / filled_via / entry_broker_mode / stop_pct / expected_return /
  actual_return / entry_price / score / shares_filled 等

■冪等性
  対象述語に hit_or_miss='pending' を含む。--apply 後は 'skipped' になるため、再実行で対象 0 件＝差分ゼロ。

実行:
  dry-run（既定・書込なし）: .venv/bin/python scripts/migrations/skip_t_a_orphan_fills.py
  適用                     : .venv/bin/python scripts/migrations/skip_t_a_orphan_fills.py --apply
  --apply 時は data/trading.sqlite を timestamp 付きでバックアップしてから書く（master 指示でのみ実行）。
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

DB = Path("data/trading.sqlite")
TARGET_DATE = "2026-05-31"
SKIP_OUTCOME = "skipped"
SKIP_NOTE = "legacy_orphan_no_portfolio_link"

_SELECT_COLS = ("id", "ticker", "hit_or_miss", "evaluated_at", "user_note")


def _now_str() -> str:
    """既存 evaluated_at と同形式（naive UTC・'YYYY-MM-DD HH:MM:SS.ffffff'）。"""
    return str(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))


def _targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        f"SELECT {', '.join(_SELECT_COLS)} FROM decisions "
        "WHERE status='filled' AND entry_date IS NULL AND date=? AND hit_or_miss='pending'",
        (TARGET_DATE,),
    ).fetchall()


def _backup_db() -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = DB.with_name(f"trading.sqlite.bak_t_a_skip_{ts}")
    shutil.copy2(DB, dst)
    return dst


def main() -> None:
    apply = "--apply" in sys.argv
    if not DB.exists():
        print(f"DB not found: {DB}")
        return

    conn = sqlite3.connect(DB)
    try:
        rows = _targets(conn)
        print(f"=== T-a SKIP ({'APPLY' if apply else 'DRY-RUN'}) ===")
        print(
            "対象: status='filled' AND entry_date IS NULL AND date="
            f"'{TARGET_DATE}' AND hit_or_miss='pending'  → {len(rows)} 件"
        )
        if not rows:
            print("対象 0 件（既に skip 済み or 該当なし）= 差分ゼロ。")
            return

        now = _now_str()
        for row in rows:
            print(
                f"  id={row['id']} {row['ticker']}  "
                f"hit_or_miss:{row['hit_or_miss']!r}→{SKIP_OUTCOME!r}  "
                f"evaluated_at:{row['evaluated_at']!r}→{now!r}  "
                f"user_note:{row['user_note']!r}→{SKIP_NOTE!r}  "
                "（entry_date/evaluation_date/filled_via/entry_broker_mode/stop_pct/"
                "expected_return/entry_price/score は不触）"
            )

        if not apply:
            print("\n[DRY-RUN] 書込なし。適用するには --apply（master 指示でのみ）。")
            return

        backup = _backup_db()
        print(f"\n[BACKUP] {backup}")
        for row in rows:
            conn.execute(
                "UPDATE decisions SET hit_or_miss=?, evaluated_at=?, user_note=? WHERE id=?",
                (SKIP_OUTCOME, now, SKIP_NOTE, row["id"]),
            )
        conn.commit()
        print(f"[APPLIED] {len(rows)} 件を skipped にマーキング。再実行で対象 0 件（冪等）。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
