"""P4-3 ペーパー運用 一気通貫（監視=日次／コア積立=月次DCA／決裁=人間）。

  python scripts/run_paper.py            # 監視：朝バッチ→GENDO推奨カード提示（決裁待ち）
  python scripts/run_paper.py --fill     # 承認(approved)分を翌寄りで紙約定（月次DCA想定）
  python scripts/run_paper.py --evaluate # 評価期日到来分を実価格で採点

決裁は人間（原則2）：カードを見て承認は別操作（decision.status を approved に更新）。
攻めは情報のみ＝枠0（GENDOカードで灰色表示）。前提：universe投入済・.env（LLM/ネット使用）。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.brokers.standin import StandInBroker
from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.job import evaluate_due_decisions
from trading_agent.models.universe import Universe
from trading_agent.orchestrator.morning_batch import run_morning_batch
from trading_agent.portfolio.operator_view import operator_cards, render_card
from trading_agent.portfolio.paper_exec import paper_fill_approved
from trading_agent.screening import fetch_financials

PriceFn = Callable[[str], float | None]
IsJpFn = Callable[[str], bool]


def _live_lookups(engine: Engine) -> tuple[PriceFn, IsJpFn]:
    """universe の市場で JPY建て価格を返す lookup（US は USDJPY 換算）。"""
    import yfinance as yf

    with Session(engine) as s:
        rows = s.exec(select(Universe).where(col(Universe.is_active))).all()
    market = {u.ticker: u.market for u in rows}
    try:
        usdjpy = float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception:
        usdjpy = 150.0

    def is_jp(t: str) -> bool:
        return market.get(t) == "JP"

    def price(t: str) -> float | None:
        m = market.get(t)
        sym = f"{t}.T" if m == "JP" else t
        try:
            p = float(yf.Ticker(sym).fast_info.last_price)
        except Exception:
            return None
        return p if m == "JP" else p * usdjpy

    return price, is_jp


def _cash(engine: Engine) -> float:
    acct = StandInBroker().get_account()
    return acct.cash if acct is not None else 100_000.0


async def _monitor(engine: Engine) -> None:
    await run_morning_batch(engine, financials_fetcher=fetch_financials)
    price, is_jp = _live_lookups(engine)
    cards = operator_cards(
        engine, price_lookup=price, is_jp_lookup=is_jp, cash_jpy=_cash(engine)
    )
    print(f"\n=== GENDO推奨カード（{len(cards)}件・決めるのはあなた）===")
    for oc in cards:
        print("\n" + render_card(oc))
    print("\n→ 承認は decision.status を approved に更新（月次 --fill で翌寄り紙約定）。")


def _fill(engine: Engine) -> None:
    price, is_jp = _live_lookups(engine)
    res = paper_fill_approved(
        engine, price_lookup=price, is_jp_lookup=is_jp, cash_jpy=_cash(engine)
    )
    print(f"紙約定 {len(res.fills)} 件 / 残現金 ¥{res.cash_after:,.0f}")
    for f in res.fills:
        print(f"  {f.ticker} {f.shares}株 @¥{f.price:,.0f} = ¥{f.amount_jpy:,.0f}")
    for t, why in res.skipped:
        print(f"  skip {t}: {why}")


def _evaluate(engine: Engine) -> None:
    price, _ = _live_lookups(engine)
    n, tr = evaluate_due_decisions(engine, price_lookup=price)
    label = "暫定" if tr.provisional else "確定"
    print(f"評価 {n} 件採点 / n={tr.n} 命中率={tr.hit_rate} 平均R={tr.avg_r}（{label}）")
    print("※P4-4：守りはリターンで測らない。コアの質パッシブ追随＋プロセス遵守の評価は次タスク。")


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    if "--fill" in sys.argv:
        _fill(engine)
    elif "--evaluate" in sys.argv:
        _evaluate(engine)
    else:
        asyncio.run(_monitor(engine))


if __name__ == "__main__":
    main()
