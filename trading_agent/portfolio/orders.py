"""A-5 / S3：決裁 → 発注リスト（貫通の出口）。

decision（status="awaiting"）を人間が決裁し、承認分を**規律を効かせた発注リスト**にする。
- 決裁：`decide()` が status を approved/denied/held に更新（既定は防御層 default_hold＝保留）。
- 発注リスト：承認 buy decision に R-multサイジング(§3)＋ポートフォリオ規律ゲート(§4)を適用し、
  銘柄/数量/想定損失(1R)/stop価格を確定。**自動発注はしない（Tier1：人間がmoomooで手動発注）**。

数値はすべてコード（LLM非関与）。サイジング/規律の純粋ロジックはDB非依存（テスト可能）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.portfolio.sizing import recommend_position
from trading_agent.risk import Candidate, Held, RiskParams, evaluate_portfolio_guard
from trading_agent.risk.params import DEFAULT_RISK
from trading_agent.utils.time_utils import utcnow

PriceLookup = Callable[[str], float | None]
SectorLookup = Callable[[str], str]

_DECISION_ACTIONS = {"approved", "denied", "held"}


@dataclass
class OrderLine:
    ticker: str
    market: str  # US / JP
    side: str  # buy / sell
    shares: float
    amount_jpy: float
    stop_price_jpy: float
    risk_jpy: float  # 想定損失（≈1R）
    note: str


def _is_jp(ticker: str) -> bool:
    return ticker.split(".")[0].isdigit()


def decide(engine: Engine, decision_id: int, *, action: str, reason: str = "") -> bool:
    """1件の decision を決裁する（status 更新）。action∈approved/denied/held。"""
    if action not in _DECISION_ACTIONS:
        raise ValueError(f"不正な決裁: {action}（{_DECISION_ACTIONS}）")
    with Session(engine, expire_on_commit=False) as session:
        d = session.get(Decision, decision_id)
        if d is None:
            return False
        d.status = action
        d.user_action = {"approved": "adopted", "denied": "skipped", "held": "deferred"}[action]
        d.user_note = reason or None
        d.user_acted_at = utcnow()
        session.add(d)
        session.commit()
    return True


def approved_buy_decisions(engine: Engine, *, on_date: dt.date | None = None) -> list[Decision]:
    """承認済みの買い decision（発注リストの素）。"""
    day = on_date or utcnow().date()
    with Session(engine) as session:
        rows = session.exec(
            select(Decision)
            .where(col(Decision.date) == day)
            .where(col(Decision.status) == "approved")
            .where(col(Decision.action) == "buy")
        ).all()
    return list(rows)


def build_order_list(
    tickers: list[str],
    *,
    price_lookup: PriceLookup,
    sector_lookup: SectorLookup,
    account_total_jpy: float,
    cash_jpy: float,
    held: list[Held] | None = None,
    peak_total_jpy: float | None = None,
    params: RiskParams = DEFAULT_RISK,
) -> list[OrderLine]:
    """承認銘柄に サイジング(§3)→規律ゲート(§4) を適用して発注リストを作る（純粋関数）。"""
    held = held or []
    # 1) R-mult サイジングで各候補の初期額・stop を出す
    sized: dict[str, object] = {}
    candidates: list[Candidate] = []
    for ticker in tickers:
        price = price_lookup(ticker)
        if price is None or price <= 0:
            continue
        rec = recommend_position(
            price_jpy=price,
            total_assets_jpy=account_total_jpy,
            cash_jpy=cash_jpy,
            is_jp=_is_jp(ticker),
            params=params,
        )
        sized[ticker] = (rec, price)
        candidates.append(
            Candidate(ticker=ticker, sector=sector_lookup(ticker), amount_jpy=rec.amount_jpy)
        )

    # 2) ポートフォリオ規律ゲート（集中・DD・保有数）
    guard = evaluate_portfolio_guard(
        candidates,
        held=held,
        account_total_jpy=account_total_jpy,
        peak_total_jpy=peak_total_jpy,
        params=params,
    )

    # 3) allow/reduce のものを OrderLine 化（reduce は最終額で株数を再計算）
    orders: list[OrderLine] = []
    for v in guard.verdicts:
        if v.action == "block" or v.amount_jpy <= 0:
            continue
        rec, price = sized[v.ticker]  # type: ignore[misc]
        is_jp = _is_jp(v.ticker)
        amount = v.amount_jpy
        shares = float(int(amount // price)) if is_jp else round(amount / price, 4)
        if shares <= 0:
            continue
        amount = shares * price
        stop = rec.stop_pct  # type: ignore[attr-defined]
        orders.append(
            OrderLine(
                ticker=v.ticker,
                market="JP" if is_jp else "US",
                side="buy",
                shares=shares,
                amount_jpy=round(amount),
                stop_price_jpy=round(price * (1.0 - stop)),
                risk_jpy=round(amount * stop),
                note=v.reason if v.action == "reduce" else "規律クリア",
            )
        )
    return orders
