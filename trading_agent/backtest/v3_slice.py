"""BT-1: v3 価格スライス backtest（J-Quants Free 制約・決定論コア・財務OFF版）。

選定者 DS 相当の価格信号（relative_strength の leading/improving）で entry を出し、
R-mult サイジング + stop/target/time-exit の規律で ¥10万から forward シミュレートする。

look-ahead 回避（codex 仕様）:
  - signal は prices[date ≤ as_of] のみで計算（未来価格を選定に渡さない）
  - entry は **翌営業日** の AdjC（signal 当日終値での同日約定を避ける）
  - exit は当日 AdjC の close-to-close 判定（最小構成）
  - market_cap filter は OFF（現時価総額 look-ahead 回避）
  - BacktestState は本番 DB と完全分離（cash/positions/trades/equity を独立保持）

制約と正直な位置づけ:
  - J-Quants Free: 2 年履歴 / 12 週遅延 / Summary 財務 → 窓は取得可能範囲（>12週前）に限定
  - survivorship: 現 universe を使うなら「current universe / survivorship biased」と明記
  - LLM/news/CASPER/財務(V字) は本 v1 では不使用（neutral）→ 名称「v3 deterministic core」
  - 結果は **増額判断（ゲート⑥）の証明ではなく、規律/状態/選定ロジックの退行検出・粗い筋確認**

全てコード計算・LLM 非関与。価格は注入（fixtures でテスト可・実 API 非依存）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from statistics import fmean

from trading_agent.risk.params import DEFAULT_RISK, RiskParams
from trading_agent.screening.relative_strength import compute_relative_strength

# (date, adjusted_close) の時系列（古→新）
Quotes = list[tuple[dt.date, float]]


@dataclass
class BacktestConfig:
    start: dt.date
    end: dt.date
    initial_capital: float = 100_000.0
    stop_pct: float = 0.12          # 損切り幅（R-mult の分母）
    target_pct: float = 0.20        # 利確目標
    hold_days: int = 60             # time-exit（中期 31-90 日帯）
    n_holdings: int = 5             # 同時保有上限
    rs_short: int = 21
    rs_long: int = 63
    buy_quadrants: tuple[str, ...] = ("leading", "improving")  # 相対力 buy 象限
    params: RiskParams = DEFAULT_RISK


@dataclass
class Position:
    ticker: str
    entry_date: dt.date
    entry_price: float
    shares: int
    stop_price: float
    target_price: float
    deadline: dt.date


@dataclass
class Trade:
    ticker: str
    entry_date: dt.date
    exit_date: dt.date
    entry_price: float
    exit_price: float
    shares: int
    actual_return: float
    r_multiple: float
    outcome: str  # hit / miss / neutral
    reason: str   # stop / target / time


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[dt.date, float]] = field(default_factory=list)
    n_trades: int = 0
    hit_rate: float | None = None
    avg_return: float = 0.0
    avg_r: float = 0.0
    max_drawdown: float = 0.0
    final_equity: float = 0.0
    open_positions: int = 0  # 終了時に未決済（n_trades/hit_rate に含まれない・codex 指摘 C）

    def summary(self) -> str:
        hr = f"{self.hit_rate:.0%}" if self.hit_rate is not None else "—"
        ret_total = (self.final_equity / self.config.initial_capital - 1.0)
        interp = "" if self.n_trades >= 10 else "（取引<10 ＝ 成績解釈は禁止・スモーク扱い）"
        return (
            "=== v3 価格スライス backtest（deterministic core / J-Quants Free 制約）===\n"
            f"期間 {self.config.start}〜{self.config.end} / 初期 ¥{self.config.initial_capital:,.0f}\n"
            f"確定取引 {self.n_trades} 件{interp} / 未決済 {self.open_positions} 件 / "
            f"命中率 {hr} / 平均R {self.avg_r:+.2f} / 平均リターン {self.avg_return:+.1%}\n"
            f"最大DD {self.max_drawdown:.1%} / 最終 equity ¥{self.final_equity:,.0f}"
            f"（総リターン {ret_total:+.1%}）\n"
            "※ 増額判断(ゲート⑥)の証明ではない。survivorship/Free薄サンプル/財務&LLM OFF/"
            "close-to-close 簡略化あり＝規律・選定ロジックの退行検出/粗い筋確認用。"
        )


def _series_upto(quotes: Quotes, as_of: dt.date) -> list[float]:
    """as_of 以前の終値だけを古→新で返す（look-ahead 回避の要）。"""
    return [c for (d, c) in quotes if d <= as_of]


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def run_price_slice_backtest(
    quotes_by_ticker: dict[str, Quotes],
    market_quotes: Quotes,
    config: BacktestConfig,
) -> BacktestResult:
    """価格スライス backtest を実行（純粋・データ注入）。

    Args:
        quotes_by_ticker: {ticker: [(date, AdjC), ...古→新]}（warm-up のため start より前も含めて渡す）
        market_quotes: ベンチマーク（TOPIX ETF 等）の [(date, AdjC), ...]。相対力の分母＆取引日 calendar。
        config: BacktestConfig
    """
    price_map: dict[str, dict[dt.date, float]] = {
        t: {d: c for (d, c) in q} for t, q in quotes_by_ticker.items()
    }
    # 取引日 calendar は market_quotes の Date から作る（土日祝を手計算しない）
    calendar = sorted(d for (d, _) in market_quotes if config.start <= d <= config.end)

    cash = config.initial_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_curve: list[tuple[dt.date, float]] = []
    pending_entries: list[str] = []  # 前日 signal → 当日 entry（翌営業日約定）

    for today in calendar:
        # 始値相当の equity（サイジング基準・anti-martingale）
        equity_now = cash + sum(
            p.shares * (price_map.get(t, {}).get(today) or p.entry_price)
            for t, p in positions.items()
        )

        # 1. 前日 signal の pending を当日 AdjC で entry（翌営業日約定）
        for t in pending_entries:
            if t in positions or len(positions) >= config.n_holdings:
                continue
            px = price_map.get(t, {}).get(today)
            if px is None or px <= 0:
                continue  # 当日価格欠損 → skip（未来日へ飛ばさない）
            # R-mult サイジング: リスク額 = equity × risk%、shares = リスク額 /(px × stop)
            risk_amt = equity_now * config.params.risk_per_trade
            shares = int(risk_amt / (px * config.stop_pct)) if config.stop_pct > 0 else 0
            # 1 銘柄上限（総資産比）でクランプ
            max_val = equity_now * config.params.max_position_weight
            if shares * px > max_val:
                shares = int(max_val / px)
            cost = shares * px
            # cash_floor（G-0）: equity × cash_floor は常時現金として残す（codex 指摘 A）
            investable_cash = cash - equity_now * config.params.cash_floor
            if shares <= 0 or cost > investable_cash:
                continue
            cash -= cost
            positions[t] = Position(
                ticker=t, entry_date=today, entry_price=px, shares=shares,
                stop_price=px * (1 - config.stop_pct),
                target_price=px * (1 + config.target_pct),
                deadline=today + dt.timedelta(days=config.hold_days),
            )
        pending_entries = []

        # 2. exit 判定（当日 AdjC・stop/target/time）。二重評価しない（exit したら positions から除去）
        for t, pos in list(positions.items()):
            px = price_map.get(t, {}).get(today)
            if px is None:
                continue
            reason: str | None = None
            if px <= pos.stop_price:
                reason = "stop"
            elif px >= pos.target_price:
                reason = "target"
            elif today >= pos.deadline:
                reason = "time"
            if reason is None:
                continue
            cash += pos.shares * px
            ret = (px - pos.entry_price) / pos.entry_price
            r = ret / config.stop_pct if config.stop_pct > 0 else 0.0
            outcome = (
                "miss" if ret <= -config.stop_pct
                else "hit" if ret >= config.target_pct
                else "neutral"
            )
            trades.append(Trade(
                ticker=t, entry_date=pos.entry_date, exit_date=today,
                entry_price=pos.entry_price, exit_price=px, shares=pos.shares,
                actual_return=round(ret, 4), r_multiple=round(r, 3),
                outcome=outcome, reason=reason,
            ))
            del positions[t]

        # 3. equity mark（当日終値で評価）
        equity = cash + sum(
            p.shares * (price_map.get(t, {}).get(today) or p.entry_price)
            for t, p in positions.items()
        )
        equity_curve.append((today, equity))

        # 4. signal 生成（prices ≤ today のみ）→ 翌営業日 entry に積む
        slots = config.n_holdings - len(positions)
        if slots > 0:
            market_series = _series_upto(market_quotes, today)
            scored: list[tuple[float, str]] = []
            for t, q in quotes_by_ticker.items():
                if t in positions:
                    continue
                # stale 除外（codex 指摘 B）: 当日価格が無い銘柄は signal 候補にしない
                # （翌営業日 entry もできない＝過去終値だけの stale signal を防ぐ）
                if price_map.get(t, {}).get(today) is None:
                    continue
                rs = compute_relative_strength(
                    _series_upto(q, today), market_series,
                    short=config.rs_short, long=config.rs_long,
                )
                if rs.quadrant in config.buy_quadrants and rs.rs_short is not None:
                    scored.append((rs.rs_short, t))
            scored.sort(reverse=True)
            pending_entries = [t for _, t in scored[:slots]]

    # === metrics ===
    decided = [t for t in trades if t.outcome in ("hit", "miss")]
    hits = [t for t in decided if t.outcome == "hit"]
    final_equity = equity_curve[-1][1] if equity_curve else cash
    return BacktestResult(
        config=config,
        trades=trades,
        equity_curve=equity_curve,
        n_trades=len(trades),
        hit_rate=(len(hits) / len(decided)) if decided else None,
        avg_return=round(fmean(t.actual_return for t in trades), 4) if trades else 0.0,
        avg_r=round(fmean(t.r_multiple for t in trades), 3) if trades else 0.0,
        max_drawdown=round(_max_drawdown([e for _, e in equity_curve]), 4),
        final_equity=round(final_equity, 0),
        open_positions=len(positions),
    )
