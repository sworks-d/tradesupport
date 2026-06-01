"""発注実績 vs 推奨の差分検知（v2.10 P14・買い忘れ防止）。

今日の awaiting Decision のうち、まだ filled になっていない銘柄を一覧表示し、
ユーザーに「買い忘れ」を意識させる。警告のみで強制はしない。

使い方:
  .venv/bin/python scripts/check_order_diff.py
  .venv/bin/python scripts/check_order_diff.py --broker-mode live
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.utils.time_utils import today_jst


def main() -> int:
    p = argparse.ArgumentParser(description="発注実績 vs 推奨の差分検知（P14）")
    p.add_argument("--broker-mode", choices=["paper", "live"], default="live")
    args = p.parse_args()

    engine = get_engine(Path("data") / "trading.sqlite")
    today = today_jst()

    with Session(engine) as s:
        decs = list(
            s.exec(
                select(Decision)
                .where(col(Decision.date) == today)
                .where(col(Decision.action) == "buy")
                .order_by(col(Decision.ticker))
            ).all()
        )

        awaiting = [d for d in decs if d.status == "awaiting"]
        filled = [d for d in decs if d.status == "filled"]
        cancelled = [d for d in decs if d.status == "cancelled"]

    print(f"=== 今日 ({today}) の Decision 集計 ===")
    print(f"  推奨総数: {len(decs)} 件")
    print(f"  ✓ filled (発注済): {len(filled)} 件")
    print(f"  ⏳ awaiting (未発注): {len(awaiting)} 件")
    print(f"  ✗ cancelled: {len(cancelled)} 件")
    print()

    if awaiting:
        print(f"⚠️ 未発注 {len(awaiting)} 件（買い忘れ?）:")
        with Session(engine) as s:
            for d in awaiting:
                u = s.get(Universe, d.ticker)
                name = (u.name if u else "?")[:20]
                stance = d.gendo_stance or "—"
                print(f"  - {d.ticker} {name}  stance={stance}")
        print()
        print("対処:")
        print("  - 楽天で発注済みなら → 'スマホで報告' or scripts/mark_filled.py で記録")
        print("  - 意図的にスキップ → 翌朝バッチで自動 cancel される")
        print("  - 忘れてた → 楽天アプリで発注 → mark_filled で記録")
    else:
        print("✓ 未発注なし。本日の推奨は全て処理済みです。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
