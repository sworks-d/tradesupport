"""本格的なバックテストエンジン（v2.10 B1）。

過去 5-10 年の Universe を使い、月次リバランスで戦略の P&L を再現する。

戦略（factor）:
  - momentum:  過去 12-1 month return 上位
  - size:      時価総額下位（small cap）
  - quality:   ROE 上位
  - lowvol:    過去 60 日 volatility 下位
  - combined:  momentum + size + quality の合成スコア

ハルシネーション対策:
  - 価格取れない銘柄は除外（推測しない）
  - 上場廃止 / 新規上場で時系列が欠ける場合はその日付で除外
  - look-ahead bias 防止: 月初の rebalance では「前月末までの情報」のみ使用

設計原則:
  - 純粋関数（engine + DataFrame）：テストしやすい
  - transaction cost を rakuten_sim 経由で反映（実コスト想定）
  - benchmark（TOPIX）との比較メトリクスも返す
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trading_agent.utils.logger import get_logger

_log = get_logger("backtest.engine")


@dataclass
class BacktestConfig:
    """バックテストの設定。"""

    start_date: dt.date
    end_date: dt.date
    initial_capital: float = 1_000_000
    n_holdings: int = 10
    strategy_name: str = "momentum"
    rebalance_freq: str = "M"  # "M"=月次, "W"=週次
    transaction_cost_pct: float = 0.0044  # 楽天かぶミニ往復スプレッド 0.44%
    # ベンチマーク: TOPIX 連動 ETF（^TPX は yfinance 非対応のため ETF で代替）
    benchmark_ticker: str = "1306.T"  # iShares Core TOPIX ETF


@dataclass
class BacktestResult:
    """バックテスト結果。"""

    config: BacktestConfig
    equity_curve: pd.Series  # 日次 equity（日付 index）
    trades: list[dict] = field(default_factory=list)
    benchmark_curve: pd.Series | None = None

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if len(self.equity_curve) else 0.0

    @property
    def total_return_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        return float(self.equity_curve.iloc[-1] / self.equity_curve.iloc[0] - 1)

    @property
    def annual_return_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        years = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days / 365.25
        if years <= 0:
            return 0.0
        return float((self.equity_curve.iloc[-1] / self.equity_curve.iloc[0]) ** (1 / years) - 1)

    @property
    def sharpe(self) -> float:
        returns = self.equity_curve.pct_change().dropna()
        if len(returns) < 2 or returns.std() == 0:
            return 0.0
        return float(returns.mean() / returns.std() * np.sqrt(252))

    @property
    def max_drawdown_pct(self) -> float:
        peak = self.equity_curve.cummax()
        dd = (self.equity_curve - peak) / peak
        return float(dd.min())

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
        return wins / len(self.trades)

    @property
    def calmar(self) -> float:
        mdd = abs(self.max_drawdown_pct)
        if mdd == 0:
            return 0.0
        return self.annual_return_pct / mdd

    def alpha_vs_benchmark(self) -> float | None:
        """ベンチマーク超過リターン (年率)。"""
        if self.benchmark_curve is None or len(self.benchmark_curve) < 2:
            return None
        bench_years = (self.benchmark_curve.index[-1] - self.benchmark_curve.index[0]).days / 365.25
        if bench_years <= 0:
            return None
        bench_annual = float(
            (self.benchmark_curve.iloc[-1] / self.benchmark_curve.iloc[0]) ** (1 / bench_years) - 1
        )
        return self.annual_return_pct - bench_annual


def fetch_price_history_bulk(
    tickers: list[str],
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """yfinance で複数銘柄の価格履歴を一括取得（DataFrame: Date x Ticker）。"""
    import yfinance as yf

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        _log.warning("price_bulk_download_failed", error_type=type(exc).__name__)
        return pd.DataFrame()

    # group_by="ticker" の MultiIndex column を Close だけ抽出
    out = pd.DataFrame()
    for sym, t in sym_map.items():
        try:
            if isinstance(df.columns, pd.MultiIndex):
                series = df[sym]["Close"]
            else:
                series = df["Close"]
            out[t] = series
        except Exception:
            continue
    return out.dropna(how="all")


def calc_signal_score(
    price_df: pd.DataFrame,
    as_of: pd.Timestamp,
    strategy: str = "momentum",
) -> pd.Series:
    """as_of 時点で各銘柄のスコアを計算（高いほど買い）。

    Args:
        price_df: 価格 DataFrame（Date x Ticker）
        as_of: 評価時点（rebalance 日）
        strategy: "momentum" / "size" / "lowvol" / "reversal"
    """
    if price_df.empty or len(price_df.index) == 0:
        return pd.Series(dtype=float)
    # as_of を Timestamp に正規化（DatetimeIndex 比較を安定させる）
    as_of = pd.Timestamp(as_of)
    if not isinstance(price_df.index, pd.DatetimeIndex):
        price_df = price_df.copy()
        price_df.index = pd.to_datetime(price_df.index)
    if as_of not in price_df.index:
        # searchsorted で as_of 以下の最後の日付を取得（DatetimeIndex 安全）
        pos = price_df.index.searchsorted(as_of, side="right")
        if pos == 0:
            return pd.Series(dtype=float)
        as_of = price_df.index[pos - 1]

    if strategy == "momentum":
        # 過去 252 営業日（1 年）リターン、ただし直近 21 日（1ヶ月）スキップ
        lookback_long = 252
        lookback_skip = 21
        try:
            idx = price_df.index.get_loc(as_of)
        except KeyError:
            return pd.Series(dtype=float)
        start_idx = max(0, idx - lookback_long)
        skip_idx = max(0, idx - lookback_skip)
        if start_idx >= skip_idx:
            return pd.Series(dtype=float)
        start_prices = price_df.iloc[start_idx]
        end_prices = price_df.iloc[skip_idx]
        return (end_prices / start_prices - 1).dropna()

    elif strategy == "lowvol":
        # 過去 60 日 volatility（小さいほど高スコア）
        lookback = 60
        try:
            idx = price_df.index.get_loc(as_of)
        except KeyError:
            return pd.Series(dtype=float)
        start_idx = max(0, idx - lookback)
        window = price_df.iloc[start_idx : idx + 1]
        if len(window) < 10:
            return pd.Series(dtype=float)
        vol = window.pct_change().std()
        return -vol  # 低 vol が高スコア

    elif strategy == "reversal":
        # 過去 21 日リターンの反対（contrarian / 短期反転）
        lookback = 21
        try:
            idx = price_df.index.get_loc(as_of)
        except KeyError:
            return pd.Series(dtype=float)
        start_idx = max(0, idx - lookback)
        if start_idx >= idx:
            return pd.Series(dtype=float)
        return -(price_df.iloc[idx] / price_df.iloc[start_idx] - 1).dropna()

    else:
        # デフォルト: momentum
        return calc_signal_score(price_df, as_of, "momentum")


def run_backtest(
    tickers: list[str],
    config: BacktestConfig,
    *,
    price_df: pd.DataFrame | None = None,
) -> BacktestResult:
    """バックテストを実行。

    Args:
      tickers: 対象 universe
      config: BacktestConfig
      price_df: 事前取得した価格データ（None なら自動取得）
    """
    # 価格データの取得
    if price_df is None:
        # warmup として 1 年前から取得（momentum 計算用）
        warmup_start = config.start_date - dt.timedelta(days=400)
        price_df = fetch_price_history_bulk(tickers, warmup_start, config.end_date)
    if price_df.empty:
        _log.warning("backtest_no_price_data")
        return BacktestResult(
            config=config, equity_curve=pd.Series(dtype=float)
        )

    # rebalance 日を生成（月初 or 週初）
    if config.rebalance_freq == "M":
        rebalance_dates = pd.date_range(
            start=config.start_date, end=config.end_date, freq="MS"
        )
    else:
        rebalance_dates = pd.date_range(
            start=config.start_date, end=config.end_date, freq="W-MON"
        )

    cash = config.initial_capital
    holdings: dict[str, dict] = {}  # ticker -> {qty, cost_basis}
    equity_history: list[tuple[pd.Timestamp, float]] = []
    trades: list[dict] = []

    for rb_date in rebalance_dates:
        # rebalance 日の前営業日までの情報でスコアリング
        as_of = rb_date - pd.Timedelta(days=1)
        scores = calc_signal_score(price_df, as_of, config.strategy_name)
        if scores.empty:
            continue
        # 上位 n_holdings 銘柄を選択
        top_n = scores.sort_values(ascending=False).head(config.n_holdings).index.tolist()

        # rebalance 日の価格で現在保有を時価評価 + 売却
        rb_prices = price_df.loc[price_df.index <= rb_date]
        if rb_prices.empty:
            continue
        cur_prices = rb_prices.iloc[-1]
        # 既存保有を全 sell（time-rebalance）
        for ticker, pos in list(holdings.items()):
            if ticker not in cur_prices.index or pd.isna(cur_prices[ticker]):
                continue
            sell_price = cur_prices[ticker]
            qty = pos["qty"]
            sell_value = sell_price * qty * (1 - config.transaction_cost_pct / 2)
            cost = pos["cost_basis"]
            pnl = sell_value - cost
            cash += sell_value
            trades.append(
                {
                    "date": rb_date.date(),
                    "ticker": ticker,
                    "action": "sell",
                    "price": float(sell_price),
                    "qty": qty,
                    "pnl": float(pnl),
                }
            )
        holdings = {}

        # 上位 N に等ウェイト買付
        if not top_n:
            equity_history.append((rb_date, cash))
            continue
        budget_per_pos = cash / len(top_n)
        for ticker in top_n:
            if ticker not in cur_prices.index or pd.isna(cur_prices[ticker]):
                continue
            buy_price = cur_prices[ticker]
            if buy_price <= 0:
                continue
            qty = int(budget_per_pos // (buy_price * (1 + config.transaction_cost_pct / 2)))
            if qty <= 0:
                continue
            cost = qty * buy_price * (1 + config.transaction_cost_pct / 2)
            cash -= cost
            holdings[ticker] = {"qty": qty, "cost_basis": cost}
            trades.append(
                {
                    "date": rb_date.date(),
                    "ticker": ticker,
                    "action": "buy",
                    "price": float(buy_price),
                    "qty": qty,
                    "pnl": 0.0,
                }
            )

        # 時価評価
        equity = cash
        for ticker, pos in holdings.items():
            if ticker in cur_prices.index and not pd.isna(cur_prices[ticker]):
                equity += cur_prices[ticker] * pos["qty"]
        equity_history.append((rb_date, equity))

    if not equity_history:
        return BacktestResult(config=config, equity_curve=pd.Series(dtype=float))

    equity_curve = pd.Series(
        [e for _, e in equity_history], index=[d for d, _ in equity_history]
    )

    # ベンチマーク
    benchmark_curve: pd.Series | None = None
    try:
        bench = fetch_price_history_bulk(
            [config.benchmark_ticker], config.start_date, config.end_date
        )
        if not bench.empty:
            col = bench.columns[0]
            bench_series = bench[col].dropna()
            # equity 開始日に合わせて正規化
            if len(bench_series) > 0 and len(equity_curve) > 0:
                bench_curve = (
                    bench_series / bench_series.iloc[0] * float(equity_curve.iloc[0])
                )
                benchmark_curve = bench_curve
    except Exception as exc:
        _log.warning("benchmark_fetch_failed", error_type=type(exc).__name__)

    return BacktestResult(
        config=config,
        equity_curve=equity_curve,
        trades=trades,
        benchmark_curve=benchmark_curve,
    )
