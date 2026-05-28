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
    price: float            # 市場価格（slippage 適用前・参考）
    amount_jpy: float       # 現金から引かれた合計（手数料・FX 含む）
    fill_price: float = 0.0  # 実約定価格（slippage 適用後）
    fee_jpy: float = 0.0    # 手数料
    fx_cost_jpy: float = 0.0  # 為替スプレッド分
    rationale: str = ""     # この約定を行った理由（思考ログ）


@dataclass
class PaperResult:
    fills: list[PaperFill] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (ticker, 理由)
    cash_after: float = 0.0


@dataclass
class PaperClose:
    """1 件の自動売却の結果。"""

    portfolio_id: int
    ticker: str
    qty: int
    buy_price: float
    sell_price: float
    proceeds_jpy: float  # 売却収入（手数料引き後）
    pnl_jpy: float       # 売却差益（手数料 × 2 引き後）
    reason: str          # "stop_loss" / "time_exit"


@dataclass
class CloseResult:
    closes: list[PaperClose] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def paper_close_due(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    is_jp_lookup: IsJpLookup,
    today: dt.date | None = None,
    personality_filter: str | None = None,
) -> CloseResult:
    """active な Portfolio のうち以下を自動売却して closed に進める。

    1. **stop_loss 到達**: 現在価格 ≤ buy_price × (1 + stop_loss_pct)
       （stop_loss_pct は負値で保存される慣行を尊重）
    2. **time_exit**: 今日 ≥ target_date

    `personality_filter` 指定時はその性格の保有のみ対象。
    moomoo シミュレーションで売却手数料・スリッページも適用する。
    """
    from trading_agent.portfolio.moomoo_sim import simulate_fill

    day = today or utcnow().date()
    result = CloseResult()
    with Session(engine, expire_on_commit=False) as session:
        stmt = select(Portfolio).where(col(Portfolio.status) == "active")
        if personality_filter is not None:
            stmt = stmt.where(col(Portfolio.personality) == personality_filter)
        ports = session.exec(stmt).all()

        for p in ports:
            if p.id is None:
                continue
            cur = price_lookup(p.ticker)
            if cur is None or cur <= 0:
                result.skipped.append((p.ticker, "現価取得不可"))
                continue
            stop_threshold = float(p.buy_price) * (1.0 + float(p.stop_loss_pct or 0))
            reason: str | None = None
            if cur <= stop_threshold:
                reason = "stop_loss"
            elif p.target_date and day >= p.target_date:
                reason = "time_exit"
            if reason is None:
                continue

            sell = simulate_fill(
                market_price=cur, qty=int(p.qty), is_jp=is_jp_lookup(p.ticker), side="sell"
            )
            # 取得時も同じスリッページ・手数料が引かれている前提で、PnL は売却収入 - 取得コスト
            buy_cost = float(p.buy_price) * int(p.qty)
            pnl = sell.total_cost_jpy - buy_cost  # sell.total_cost_jpy は売却の手取り
            p.status = "closed"
            p.closed_at = utcnow()
            p.closed_price = sell.fill_price
            p.closed_reason = reason
            session.add(p)
            result.closes.append(
                PaperClose(
                    portfolio_id=p.id,
                    ticker=p.ticker,
                    qty=int(p.qty),
                    buy_price=float(p.buy_price),
                    sell_price=sell.fill_price,
                    proceeds_jpy=sell.total_cost_jpy,
                    pnl_jpy=pnl,
                    reason=reason,
                )
            )
        session.commit()
    return result


def _consensus_tickers(engine: Engine, peers: tuple[str, ...]) -> set[str]:
    """指定の性格群（peers）が全員 active 保有している ticker の集合を返す。

    KAWORU の "いいとこどり" 用：REI / ASUKA / SHINJI が共通で買った銘柄
    （合議シグナル＝皆が買うものは強い）を優先的に拾うために使う。
    """
    from trading_agent.models.portfolio import Portfolio

    with Session(engine) as session:
        sets: list[set[str]] = []
        for peer in peers:
            rows = session.exec(
                select(Portfolio)
                .where(col(Portfolio.personality) == peer)
                .where(col(Portfolio.status) == "active")
            ).all()
            sets.append({r.ticker for r in rows})
    if not sets or any(not s for s in sets):
        return set()
    return set.intersection(*sets)


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
    personality: object | None = None,
) -> PaperResult:
    """status=approved の decision を翌寄り価格で紙約定し、Portfolio(active)化＋record_entry する。

    `personality` 指定時はその性格固有の sizing/horizon/stop を適用し、Portfolio.personality
    に性格名を刻む。同じ decision に対して複数性格が並行 fill する場合、
    Decision.personalities_filled に追加して重複 fill を防ぐ。

    現金は引数で受け、残額を返す（永続化は呼び出し側＝run_paper の責務）。
    """
    day = today or utcnow().date()
    if personality is not None:
        # 性格固有の運用ルールで上書き
        stop = float(getattr(personality, "stop_loss_pct", params.default_stop_pct))
        horizon = int(getattr(personality, "horizon_days", horizon_days))
        accept_stances: set[str] | None = set(
            getattr(personality, "accept_stances", ())
        )
        # 動的 max_position_pct：現在の PnL に応じて ×0.8〜×1.2 で可変
        # （勝ってる時は強気・負けてる時は守り）
        try:
            from trading_agent.portfolio.personality import effective_max_position_pct

            overlay = float(getattr(personality, "overlay_cash_jpy", cash_jpy))
            current_total = cash_jpy + positions_value_jpy
            pnl_pct = ((current_total - overlay) / overlay * 100.0) if overlay else 0.0
            max_pos_pct = effective_max_position_pct(personality, pnl_pct=pnl_pct)
        except Exception:
            max_pos_pct = float(getattr(personality, "max_position_pct", 0.20))
        personality_name: str | None = str(getattr(personality, "name", ""))
    else:
        stop = params.default_stop_pct
        horizon = horizon_days
        accept_stances = None
        max_pos_pct = 0.20
        personality_name = None

    cash = cash_jpy
    result = PaperResult(cash_after=cash)

    with Session(engine, expire_on_commit=False) as session:
        # 性格モードでは複数機が同じ decision を独立に fill できるよう、
        # record_entry で進んだ "ordered"/"holding" 状態も対象に含める
        # （重複防止は personalities_filled で行う）。
        if personality is not None:
            decisions = session.exec(
                select(Decision)
                .where(
                    col(Decision.status).in_(
                        ("approved", "awaiting", "ordered", "holding")
                    )
                )
                .where(col(Decision.action) == "buy")
            ).all()
        else:
            decisions = session.exec(
                select(Decision)
                .where(col(Decision.status) == "approved")
                .where(col(Decision.action) == "buy")
            ).all()

    # KAWORU 限定：他 3 機の合議銘柄を取得し、decisions のソートを優先順位付け
    # （cash が許す限り合議銘柄から先に fill する＝いいとこどり戦略）。
    consensus: set[str] = set()
    if personality is not None and personality_name == "KAWORU":
        consensus = _consensus_tickers(engine, peers=("REI", "ASUKA", "SHINJI"))
        if consensus:
            decisions = sorted(decisions, key=lambda d: 0 if d.ticker in consensus else 1)

    with Session(engine, expire_on_commit=False) as session:

        for d in decisions:
            if d.id is None:
                continue
            # 性格モード：受容 stance + 重複 fill チェック
            if personality is not None:
                if accept_stances is not None and d.gendo_stance not in accept_stances:
                    continue
                filled_already = list(d.personalities_filled or [])
                if personality_name in filled_already:
                    continue
            price = price_lookup(d.ticker)
            if price is None or price <= 0:
                result.skipped.append((d.ticker, "価格取得不可"))
                continue
            is_jp = is_jp_lookup(d.ticker)
            total = cash + positions_value_jpy
            rec = recommend_position(
                price_jpy=price, total_assets_jpy=total, cash_jpy=cash,
                is_jp=is_jp, stop_pct=stop, params=params,
            )
            shares = int(rec.shares)
            # 性格モードでは max_position_pct で上限制約
            if personality is not None:
                max_cost = total * max_pos_pct
                max_shares = int(max_cost // price)
                shares = min(shares, max_shares)
            if shares <= 0:
                result.skipped.append((d.ticker, f"サイズ0（{rec.note}）"))
                continue
            # moomoo 実弾相当のコスト（手数料・スリッページ・FX スプレッド）を適用
            from trading_agent.portfolio.moomoo_sim import simulate_fill

            fill = simulate_fill(
                market_price=price, qty=shares, is_jp=is_jp, side="buy"
            )
            if fill.total_cost_jpy > cash:
                result.skipped.append(
                    (
                        d.ticker,
                        f"現金不足（必要 ¥{fill.total_cost_jpy:,.0f} / 残 ¥{cash:,.0f}）",
                    )
                )
                continue
            cash -= fill.total_cost_jpy
            # 思考ログ：性格別の判断理由を記録
            stance_label = d.gendo_stance or "—"
            rationale = (
                f"MAGI={stance_label} を accept_stances={list(accept_stances) if accept_stances else 'all'} に基づき採用。"
                f" サイジング: max {max_pos_pct*100:.0f}% / 保有 {horizon}日 / stop -{stop*100:.0f}%。"
                f" 実費 all-in {fill.all_in_pct*100:+.2f}%（手数料 ¥{fill.fee_jpy:,.0f}・スリッページ含む）。"
                if personality is not None
                else (
                    f"標準ペーパー fill: stance={stance_label}・stop -{stop*100:.0f}%・保有 {horizon}日"
                )
            )
            session.add(
                Portfolio(
                    ticker=d.ticker,
                    buy_date=day,
                    buy_price=fill.fill_price,  # 実約定価格を Portfolio に記録
                    qty=shares,
                    currency="JPY" if is_jp else "USD",
                    strategy_category="中期",
                    target_period_days=horizon,
                    target_pct=0.0,
                    stop_loss_pct=-stop,
                    target_date=day + dt.timedelta(days=horizon),
                    thesis=(d.thesis_at_decision or "コア（守り主導の質分散塊）") + " | " + rationale,
                    status="active",
                    personality=personality_name,
                )
            )
            if personality is not None:
                filled_new = list(d.personalities_filled or [])
                if personality_name not in filled_new:
                    filled_new.append(personality_name)
                d.personalities_filled = filled_new
            else:
                d.status = "holding"
            session.add(d)
            result.fills.append(
                PaperFill(
                    decision_id=d.id,
                    ticker=d.ticker,
                    shares=shares,
                    price=price,
                    amount_jpy=fill.total_cost_jpy,
                    fill_price=fill.fill_price,
                    fee_jpy=fill.fee_jpy,
                    fx_cost_jpy=fill.fx_cost_jpy,
                    rationale=rationale,
                )
            )
        session.commit()

    # 評価の前提（entry/stop/評価期日）を刻む。実約定価格（slippage 適用後）を渡す。
    for f in result.fills:
        record_entry(
            engine, f.decision_id,
            entry_price=f.fill_price if f.fill_price else f.price,
            stop_pct=stop, target_return=0.0,
            target_period_days=horizon, on_date=day,
        )

    result.cash_after = cash
    return result
