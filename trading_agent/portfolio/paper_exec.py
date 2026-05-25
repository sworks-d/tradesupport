"""P4-1 ペーパー執行ループ：approved な decision を紙約定→Portfolio化→record_entry。

実ブローカー不要（前向き＝今日のデータは定義上 point-in-time）。**決裁は人間（原則2）**＝
`status="approved"` の decision だけを執行する（自動承認はしない）。紙約定の価格は
**翌営業日の寄り**（先読み回避）を呼び出し側が `price_lookup` で渡す前提（JPY建て）。

コアは守り主導の質分散塊（B'＝利確で刻まない・固定stop＋保有期限で出口）。サイジングは
規律層 `recommend_position`（R-mult・現金下限・枠は呼び出し側で制御）。

注（v1の簡約）：Portfolio.qty は整数。US端株(小数)は当面**整数株に丸める**（高単価USは
小予算で買えないことがある＝正直な制約）。小数株対応はモデル拡張後（別タスク）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.evaluation.job import record_entry
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.sizing import recommend_position
from trading_agent.risk.params import DEFAULT_RISK, RiskParams
from trading_agent.utils.time_utils import utcnow

PriceLookup = Callable[[str], float | None]  # ticker → 翌寄りの約定価格(JPY)。取得不可は None
IsJpLookup = Callable[[str], bool]  # ticker → 日本株か（端株可否・通貨に使用）

_HORIZON_DAYS = 120  # B' 保有期限（time-exit）の既定


@dataclass
class PaperFill:
    decision_id: int
    ticker: str
    shares: int
    price: float
    amount_jpy: float


@dataclass
class PaperResult:
    fills: list[PaperFill] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (ticker, 理由)
    cash_after: float = 0.0


def paper_fill_approved(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    is_jp_lookup: IsJpLookup,
    cash_jpy: float,
    positions_value_jpy: float = 0.0,
    params: RiskParams = DEFAULT_RISK,
    horizon_days: int = _HORIZON_DAYS,
    today: dt.date | None = None,
) -> PaperResult:
    """status=approved の decision を翌寄り価格で紙約定し、Portfolio(active)化＋record_entry する。

    現金は引数で受け、残額を返す（永続化は呼び出し側＝run_paper の責務）。`positions_value_jpy`
    は既存保有の時価（総資産＝cash+positions でサイジングするため）。
    """
    day = today or utcnow().date()
    stop = params.default_stop_pct
    cash = cash_jpy
    result = PaperResult(cash_after=cash)

    with Session(engine, expire_on_commit=False) as session:
        approved = session.exec(
            select(Decision)
            .where(col(Decision.status) == "approved")
            .where(col(Decision.action) == "buy")
        ).all()
        for d in approved:
            if d.id is None:
                continue
            price = price_lookup(d.ticker)  # 翌寄り(JPY)
            if price is None or price <= 0:
                result.skipped.append((d.ticker, "価格取得不可"))
                continue
            is_jp = is_jp_lookup(d.ticker)
            total = cash + positions_value_jpy
            rec = recommend_position(
                price_jpy=price, total_assets_jpy=total, cash_jpy=cash,
                is_jp=is_jp, stop_pct=stop, params=params,
            )
            shares = int(rec.shares)  # v1：整数株に丸める
            cost = shares * price
            if shares <= 0 or cost > cash:
                result.skipped.append((d.ticker, f"サイズ0/現金不足（{rec.note}）"))
                continue
            cash -= cost
            session.add(
                Portfolio(
                    ticker=d.ticker,
                    buy_date=day,
                    buy_price=price,
                    qty=shares,
                    currency="JPY" if is_jp else "USD",
                    strategy_category="中期",
                    target_period_days=horizon_days,
                    target_pct=0.0,  # B'：利確で刻まない＝目標で売らない（出口は固定stop＋期限）
                    stop_loss_pct=-stop,  # Portfolio規約：損切りは負値
                    target_date=day + dt.timedelta(days=horizon_days),
                    thesis=d.thesis_at_decision or "コア（守り主導の質分散塊）",
                    status="active",
                )
            )
            d.status = "holding"
            session.add(d)
            result.fills.append(PaperFill(d.id, d.ticker, shares, price, cost))
        session.commit()

    # 評価の前提（entry/stop/評価期日）を刻む。record_entry は holding を維持する。
    for f in result.fills:
        record_entry(
            engine, f.decision_id,
            entry_price=f.price, stop_pct=stop, target_return=0.0,
            target_period_days=horizon_days, on_date=day,
        )

    result.cash_after = cash
    return result
