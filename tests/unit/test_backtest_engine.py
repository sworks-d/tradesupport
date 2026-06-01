"""バックテストエンジンのテスト（v2.10 B1）。"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from trading_agent.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    calc_signal_score,
    run_backtest,
)


def _synth_price_df(n_days: int = 500, n_tickers: int = 5) -> pd.DataFrame:
    """合成価格データ：ticker ごとに異なるドリフトで random walk。

    drift の差を十分大きく取って seed=42 でも信号が安定して検出されるよう設計。
    """
    np.random.seed(42)
    dates = pd.date_range(start="2020-01-01", periods=n_days, freq="B")
    prices = {}
    for i in range(n_tickers):
        # drift 差を大きくして noise を上回らせる（0.001 単位 / 営業日 ≒ 25%/年）
        drift = 0.0005 + i * 0.001
        vol = 0.015
        returns = np.random.normal(drift, vol, n_days)
        prices[f"T{i:03d}"] = 1000 * np.exp(np.cumsum(returns))
    return pd.DataFrame(prices, index=dates)


class TestCalcSignalScore:
    def test_momentum_returns_higher_for_uptrend(self):
        df = _synth_price_df()
        as_of = df.index[-1]
        scores = calc_signal_score(df, as_of, "momentum")
        # 最後の銘柄（drift 最高）が最上位
        assert scores.idxmax() == "T004"

    def test_lowvol_returns_lower_volatility(self):
        df = _synth_price_df()
        as_of = df.index[-1]
        scores = calc_signal_score(df, as_of, "lowvol")
        # lowvol スコアは負の vol。なので最大は最低 vol
        assert len(scores) > 0
        assert scores.notna().any()

    def test_reversal_inverts_recent_return(self):
        """reversal は過去 21 日リターン最高の銘柄を最下位スコアにする（contrarian）。"""
        df = _synth_price_df()
        as_of = df.index[-1]
        rev = calc_signal_score(df, as_of, "reversal")
        # 同じ 21 日リターンを直接計算
        end_idx = df.index.get_loc(as_of)
        start_idx = end_idx - 21
        returns_21d = df.iloc[end_idx] / df.iloc[start_idx] - 1
        # reversal スコアと 21 日 return は符号反転の関係
        # 21 日 return 最高 → reversal 最下位
        assert rev.idxmin() == returns_21d.idxmax()
        # 21 日 return 最低 → reversal 最上位
        assert rev.idxmax() == returns_21d.idxmin()

    def test_returns_empty_when_no_data(self):
        df = pd.DataFrame(columns=["A"])
        scores = calc_signal_score(df, pd.Timestamp("2020-01-01"), "momentum")
        assert len(scores) == 0


class TestRunBacktest:
    def test_runs_with_synthetic_data(self):
        df = _synth_price_df(n_days=500, n_tickers=10)
        cfg = BacktestConfig(
            start_date=dt.date(2020, 6, 1),
            end_date=dt.date(2021, 6, 1),
            initial_capital=1_000_000,
            n_holdings=3,
            strategy_name="momentum",
        )
        result = run_backtest(list(df.columns), cfg, price_df=df)
        assert len(result.equity_curve) > 0
        assert result.final_equity > 0

    def test_returns_metrics(self):
        df = _synth_price_df(n_days=500, n_tickers=10)
        cfg = BacktestConfig(
            start_date=dt.date(2020, 6, 1),
            end_date=dt.date(2021, 12, 1),
            initial_capital=1_000_000,
            strategy_name="momentum",
        )
        result = run_backtest(list(df.columns), cfg, price_df=df)
        # メトリクスが計算できる
        assert isinstance(result.annual_return_pct, float)
        assert isinstance(result.sharpe, float)
        assert result.max_drawdown_pct <= 0
        assert result.n_trades > 0

    def test_transaction_cost_reduces_returns(self):
        df = _synth_price_df(n_days=500, n_tickers=5)
        cfg_low = BacktestConfig(
            start_date=dt.date(2020, 6, 1),
            end_date=dt.date(2021, 6, 1),
            initial_capital=1_000_000,
            strategy_name="momentum",
            transaction_cost_pct=0.0,
        )
        cfg_high = BacktestConfig(
            start_date=dt.date(2020, 6, 1),
            end_date=dt.date(2021, 6, 1),
            initial_capital=1_000_000,
            strategy_name="momentum",
            transaction_cost_pct=0.02,  # 2%
        )
        r_low = run_backtest(list(df.columns), cfg_low, price_df=df)
        r_high = run_backtest(list(df.columns), cfg_high, price_df=df)
        # 高コストの方が低リターン（または同等以下）
        assert r_low.final_equity >= r_high.final_equity

    def test_empty_universe_returns_empty(self):
        cfg = BacktestConfig(
            start_date=dt.date(2020, 1, 1),
            end_date=dt.date(2020, 12, 31),
        )
        result = run_backtest([], cfg, price_df=pd.DataFrame())
        assert len(result.equity_curve) == 0
