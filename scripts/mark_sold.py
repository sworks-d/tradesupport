"""売却完了マーク CLI（A-2b・楽天本番の手動売却を DB に反映）。

発注リストの「売り」ブロックでユーザーが楽天証券で売却した後、この CLI で
sell Decision に対応する active Portfolio を closed にし、Treasury に売却代金を
加算する（買いの mark_filled.py と対称・連携漏れ防止）。

使い方:
  # sell Decision を実売却価格で確定
  .venv/bin/python scripts/mark_sold.py --decision-id 123 --price 850

  # 売却日を明示（省略時は今日 JST）
  .venv/bin/python scripts/mark_sold.py --decision-id 123 --price 850 --date 2026-06-06

設計意図:
  - DB が単一の真実源（発注リストの表示だけでは live 売り出口は閉じない）
  - 売却完了 = Portfolio.status="closed" + sell Decision.status="ordered"（評価対象）
    + Treasury に売却代金を加算
  - paper_close_approved（paper 自動執行）と意味を揃える
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.portfolio.paper_exec import close_sold_decision


def main() -> int:
    p = argparse.ArgumentParser(description="売却完了マーク CLI（楽天本番の手動売却を DB 反映）")
    p.add_argument("--decision-id", type=int, required=True, help="sell Decision ID")
    p.add_argument("--price", type=float, required=True, help="実売却価格（1 株あたり）")
    p.add_argument("--date", type=str, default=None, help="売却日 YYYY-MM-DD（省略時は今日 JST）")
    p.add_argument(
        "--broker-mode",
        type=str,
        default="live",
        choices=["paper", "live"],
        help='記録先 broker_mode（既定 live=楽天本番）',
    )
    args = p.parse_args()

    closed_date: dt.date | None = None
    if args.date:
        try:
            closed_date = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"--date の形式が不正: {args.date}（YYYY-MM-DD）")
            return 1

    engine = get_engine(Path("data") / "trading.sqlite")
    result = close_sold_decision(
        engine,
        args.decision_id,
        closed_price=args.price,
        closed_date=closed_date,
        broker_mode=args.broker_mode,
    )

    if "error" in result:
        print(f"⛔ 売却反映失敗: {result['error']} {result}")
        return 1

    mode_label = "🟢 楽天本番" if args.broker_mode == "live" else "🟡 試験運用"
    pnl = result["pnl_jpy"]
    sign = "+" if pnl >= 0 else ""
    print(f"✓ Decision id={result['decision_id']} ticker={result['ticker']} を売却反映 ({mode_label})")
    print(f"  {result['action']}: ¥{result['closed_price']:,.0f} × {result['qty']} 株 = ¥{result['proceeds_jpy']:,.0f}")
    print(f"  損益 {sign}¥{pnl:,.0f}（{sign}{(result['actual_return'] or 0) * 100:.1f}%）")
    if result.get("treasury_error"):
        print(f"  ⚠️ Treasury 加算失敗: {result['treasury_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
