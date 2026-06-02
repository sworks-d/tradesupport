"""P4-3 ペーパー運用 一気通貫（監視=日次／コア積立=月次DCA／決裁=人間）。

  python scripts/run_paper.py                              # 監視：朝バッチ→GENDO推奨カード提示
  python scripts/run_paper.py --fill                       # 承認(approved)分を紙約定（手動運用）
  python scripts/run_paper.py --evaluate                   # 評価期日到来分を実価格で採点
  python scripts/run_paper.py --personality defender --auto-approve
      # 性格別の自動承認＋紙約定（accept_stances にマッチする awaiting を一括処理）

性格モード（--personality）:
  defender   守り（推しのみ・5% 上限・180 日保有・stop -12%）
  aggressor  攻め（推し+要検討・10%・60 日・stop -8%）
  balanced   中庸（推し+要検討・7%・90 日・stop -10%）
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.brokers import load_account, load_personality_account
from trading_agent.config import load_settings
from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.job import evaluate_due_decisions
from trading_agent.evaluation.paper_review import check_process_adherence
from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.orchestrator.morning_batch import run_morning_batch
from trading_agent.portfolio.operator_view import operator_cards, render_card
from trading_agent.portfolio.paper_exec import paper_close_due, paper_fill_approved
from trading_agent.portfolio.personality import PERSONALITIES, all_personalities, get_personality
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
    """口座 cash（live は moomoo そのまま / paper は moomoo + overlay − 紙約定累計）。"""
    acct, _ = load_account(engine, load_settings())
    return acct.cash


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
    # 守りの本体＝プロセス遵守（規律を守れたか）。ネット不要。
    print("=== プロセス遵守（守りはリターンでなく規律で測る）===")
    for f in check_process_adherence(engine, cash_jpy=_cash(engine)):
        print(f"  {'✓' if f.ok else '✗'} {f.rule}：{f.detail}")
    # 副次＝命中率/平均R（払戻比の産物になり得る＝過信しない）。
    price, _ = _live_lookups(engine)
    from trading_agent.evaluation.benchmark import make_topix_benchmark_lookup

    n, tr = evaluate_due_decisions(
        engine, price_lookup=price, benchmark_lookup=make_topix_benchmark_lookup()
    )
    label = "暫定" if tr.provisional else "確定"
    net_excess = f"{tr.avg_net_excess:+.1%}" if tr.avg_net_excess is not None else "—"
    print(f"\n[副次] 評価 {n} 件採点 / n={tr.n} 命中率={tr.hit_rate} 平均R={tr.avg_r}（{label}）")
    print(f"        コスト後α（対ベンチ超過）={net_excess}  ← 勝ち定義の中核")
    print("※守り(自爆回避)は数ヶ月のリターンに現れない＝正常。履歴が貯まればコアvsパッシブのリスク調整で測る。")


def _auto_approve_for_personality(engine: Engine, personality_name: str) -> int:
    """指定性格の accept_stances にマッチする awaiting decisions を approved に進める。

    既に approved/holding な行は触らない（冪等）。
    """
    personality = get_personality(personality_name)
    accept = set(personality.accept_stances)
    count = 0
    with Session(engine) as session:
        rows = session.exec(
            select(Decision)
            .where(col(Decision.status) == "awaiting")
            .where(col(Decision.action) == "buy")
        ).all()
        for d in rows:
            if d.gendo_stance in accept:
                d.status = "approved"
                session.add(d)
                count += 1
        session.commit()
    return count


def _close_due_for_personality(engine: Engine, personality_name: str) -> None:
    """その性格の保有のうち stop_loss / time_exit に到達したものを自動売却する。"""
    personality = get_personality(personality_name)
    price, is_jp = _live_lookups(engine)
    res = paper_close_due(
        engine,
        price_lookup=price,
        is_jp_lookup=is_jp,
        personality_filter=personality_name,
    )
    if res.closes:
        print(
            f"[{personality.icon} {personality.label}] 自動売却 {len(res.closes)} 件"
        )
        for c in res.closes:
            sign = "+" if c.pnl_jpy >= 0 else ""
            print(
                f"  {c.ticker} {c.qty}株 ¥{c.buy_price:,.0f}→¥{c.sell_price:,.0f}"
                f" / {c.reason} / PnL {sign}¥{c.pnl_jpy:,.0f}"
            )


def _fill_for_personality(engine: Engine, personality_name: str) -> None:
    """1 つの性格で auto-close → auto-approve → 紙約定の連続実行。"""
    # 先に保有チェック（stop / 期限到達分を売却）してから新規買い fill
    _close_due_for_personality(engine, personality_name)
    personality = get_personality(personality_name)
    approved_n = _auto_approve_for_personality(engine, personality_name)
    print(
        f"[{personality.icon} {personality.label}] auto-approved {approved_n} 件"
        f" (stances={list(personality.accept_stances)})"
    )

    # 性格別の cash を計算（性格固有の overlay − その性格の active コスト）
    acct = load_personality_account(
        engine,
        personality=personality_name,
        overlay_cash_jpy=personality.overlay_cash_jpy,
    )
    price, is_jp = _live_lookups(engine)
    res = paper_fill_approved(
        engine,
        price_lookup=price,
        is_jp_lookup=is_jp,
        cash_jpy=acct.cash,
        personality=personality,
    )
    print(
        f"[{personality.icon} {personality.label}] 紙約定 {len(res.fills)} 件 /"
        f" 残現金 ¥{res.cash_after:,.0f}"
    )
    for f in res.fills:
        print(f"  {f.ticker} {f.shares}株 @¥{f.price:,.0f} = ¥{f.amount_jpy:,.0f}")
    for t, why in res.skipped:
        print(f"  skip {t}: {why}")


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    # 性格モード: --personality {defender|aggressor|balanced|all} と --auto-approve
    personality_arg: str | None = None
    for i, a in enumerate(sys.argv):
        if a == "--personality" and i + 1 < len(sys.argv):
            personality_arg = sys.argv[i + 1]
            break

    if personality_arg:
        auto = "--auto-approve" in sys.argv
        if not auto:
            print(
                "⚠ --personality 指定時は --auto-approve も必須です（性格モードは自動運用）。"
            )
            return
        targets = (
            [p.name for p in all_personalities()]
            if personality_arg == "all"
            else [personality_arg]
        )
        for name in targets:
            if name not in PERSONALITIES:
                print(f"⚠ unknown personality: {name}（候補: {list(PERSONALITIES.keys())}）")
                continue
            _fill_for_personality(engine, name)
        return

    if "--fill" in sys.argv:
        _fill(engine)
    elif "--evaluate" in sys.argv:
        _evaluate(engine)
    else:
        asyncio.run(_monitor(engine))


if __name__ == "__main__":
    main()
