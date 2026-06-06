"""上場廃止 close の closed_price を後日入力し、元 buy Decision に損益を還元する CLI（survivorship-bias 是正）.

close_delisted_holdings.py は closed_reason='delisted'・closed_price=None で閉じる（実価格は後日人間が入力）。
本スクリプトがその「入力経路」: 実際の closed_price（株式交換比率／最終値／0=無価値）を入れると、
paper_exec.record_delisted_exit が元 buy Decision に hit_or_miss/actual_return を確定し、上場廃止の損失を
track_record/勝率に乗せる（漏らさない）。**closed_price 未入力の間は何も還元しない（H10・推測しない）。**

実行:
  入力待ち一覧: .venv/bin/python scripts/set_delisted_price.py --list
  価格入力     : .venv/bin/python scripts/set_delisted_price.py --portfolio-id <ID> --price <実価格> [--date YYYY-MM-DD]
                 （無価値で上場廃止＝--price 0）
売買は変えない（評価への損益還元のみ）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from sqlmodel import Session, col, select

from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.paper_exec import record_delisted_exit


def _list_pending(engine) -> list[dict]:
    """closed_reason='delisted' かつ closed_price 未入力（None）の Portfolio 一覧。"""
    with Session(engine) as s:
        rows = s.exec(
            select(Portfolio)
            .where(col(Portfolio.status) == "closed")
            .where(col(Portfolio.closed_reason) == "delisted")
            .where(col(Portfolio.closed_price).is_(None))
        ).all()
        out = []
        for p in rows:
            d = s.get(Decision, p.decision_id) if getattr(p, "decision_id", None) else None
            out.append({
                "portfolio_id": p.id,
                "ticker": p.ticker,
                "personality": p.personality,
                "buy_price": p.buy_price,
                "qty": p.qty,
                "broker_mode": p.broker_mode,
                "buy_decision_id": getattr(p, "decision_id", None),
                "buy_decision_status": d.hit_or_miss if d else "(no link)",
            })
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="入力待ち上場廃止 close を一覧")
    ap.add_argument("--portfolio-id", type=int, help="価格を入れる Portfolio ID")
    ap.add_argument("--price", type=float, help="実際の closed_price（0=無価値）")
    ap.add_argument("--date", type=str, default=None, help="退出日 YYYY-MM-DD（既定=今日 JST）")
    args = ap.parse_args()

    engine = get_engine("data/trading.sqlite")

    if args.list or (args.portfolio_id is None and args.price is None):
        pending = _list_pending(engine)
        print(f"=== 上場廃止 close（closed_price 未入力）: {len(pending)} 件 ===")
        for r in pending:
            print(
                f"  portfolio_id={r['portfolio_id']} {r['ticker']} {r['personality']} "
                f"qty={r['qty']} buy={r['buy_price']} broker={r['broker_mode']} "
                f"buy_decision={r['buy_decision_id']}({r['buy_decision_status']})"
            )
        if not args.portfolio_id:
            print(
                "\n価格を入れて還元: --portfolio-id <ID> --price <実価格>（無価値は --price 0）"
            )
            return 0

    if args.portfolio_id is None or args.price is None:
        print("ERROR: --portfolio-id と --price の両方が必要（--list で一覧）。", file=sys.stderr)
        return 1

    closed_date = dt.date.fromisoformat(args.date) if args.date else None
    result = record_delisted_exit(
        engine, portfolio_id=args.portfolio_id, closed_price=args.price, closed_date=closed_date
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("error"):
        return 1
    print(
        f"✓ portfolio_id={result['portfolio_id']} {result['ticker']} closed_price={result['closed_price']} "
        f"→ buy_decision {result['buy_decision_id']}: hit_or_miss={result['hit_or_miss']} "
        f"actual_return={result['actual_return']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
