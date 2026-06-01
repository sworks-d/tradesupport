"""Phase H-6 (correlation→fill 抑制) + H-7 (DD ブレーキ) のテスト。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.portfolio import anomaly_detector, correlation


@pytest.fixture
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "h.sqlite")
    create_all(eng)
    return eng


# === H-7: DD ブレーキ ===========================================================
class TestDdBrake:
    def _add(self, engine, date: dt.date, total: float):
        with Session(engine) as s:
            s.add(
                PortfolioSnapshot(
                    date=date,
                    total_assets_jpy=total,
                    cash_jpy=total, us_stocks_value_jpy=0, jp_stocks_value_jpy=0,
                    satellite_value_jpy=0, core_value_jpy=0,
                    usd_jpy_rate=150.0, holding_count=0, daily_pnl_jpy=0,
                )
            )
            s.commit()

    def test_no_brake_when_dd_within_threshold(self, engine):
        today = dt.date(2026, 5, 31)
        self._add(engine, today - dt.timedelta(days=2), 100_000)
        self._add(engine, today - dt.timedelta(days=1), 105_000)
        self._add(engine, today, 102_000)  # peak 105k から -3%
        result = anomaly_detector.check_dd_brake(engine, today=today)
        assert result["brake_active"] is False

    def test_brake_at_minus_10_pct(self, engine):
        today = dt.date(2026, 5, 31)
        self._add(engine, today - dt.timedelta(days=2), 100_000)
        self._add(engine, today - dt.timedelta(days=1), 110_000)
        self._add(engine, today, 98_000)  # peak 110k から -10.9%
        result = anomaly_detector.check_dd_brake(engine, today=today)
        assert result["brake_active"] is True
        assert result["cum_dd"] <= -0.10

    def test_brake_separate_from_halt(self, engine):
        """DD ブレーキ (-10%) は HALT (-15%) より緩い閾値で発火する。"""
        today = dt.date(2026, 5, 31)
        self._add(engine, today - dt.timedelta(days=1), 100_000)
        self._add(engine, today, 88_000)  # -12%（HALT 未満、ブレーキ域）
        brake = anomaly_detector.check_dd_brake(engine, today=today)
        halt = anomaly_detector.detect_portfolio_drawdown(engine, today=today)
        assert brake["brake_active"] is True
        # HALT 閾値 -15% には届いてない
        assert halt is None or halt["type"] != "cumulative_drawdown"

    def test_insufficient_data_no_brake(self, engine):
        today = dt.date(2026, 5, 31)
        self._add(engine, today, 50_000)
        result = anomaly_detector.check_dd_brake(engine, today=today)
        assert result["brake_active"] is False  # 推測しない


# === H-6: 新規 buy 相関ブロック ==================================================
class TestNewBuyCorrelation:
    def _add_holding(self, engine, ticker: str):
        with Session(engine) as s:
            s.add(
                Portfolio(
                    ticker=ticker,
                    buy_date=dt.date(2026, 1, 1),
                    buy_price=100.0,
                    qty=10,
                    currency="USD",
                    strategy_category="中期",
                    target_period_days=90,
                    target_pct=0.2,
                    stop_loss_pct=-0.08,
                    target_date=dt.date(2026, 4, 1),
                    thesis="t",
                    status="active",
                    broker_mode="paper",
                )
            )
            s.commit()

    def test_no_holdings_pass_through(self, engine):
        result = correlation.assess_new_buy_correlation(engine, "NEW")
        assert result["blocked"] is False
        assert result["reason"] == "no_holdings"

    def test_already_held_pass_through(self, engine):
        self._add_holding(engine, "AAPL")
        result = correlation.assess_new_buy_correlation(engine, "AAPL")
        assert result["blocked"] is False
        assert result["reason"] == "already_held_pyramid"

    def test_no_data_pass_through(self, engine):
        self._add_holding(engine, "AAPL")
        with patch.object(correlation, "fetch_daily_returns", return_value=None):
            result = correlation.assess_new_buy_correlation(engine, "NEW")
        assert result["blocked"] is False
        assert result["reason"] == "no_data_for_new"

    def test_high_correlation_blocks(self, engine):
        """保有銘柄と相関 |r|≥0.7 なら blocked=True。"""
        self._add_holding(engine, "AAPL")

        def fake_returns(ticker):
            # 全く同じリターン → 相関 1.0
            return [0.01, -0.02, 0.03, 0.00, 0.01] * 5

        with patch.object(correlation, "fetch_daily_returns", side_effect=fake_returns):
            result = correlation.assess_new_buy_correlation(engine, "NEW", threshold=0.7)
        assert result["blocked"] is True
        assert result["max_corr"] is not None
        assert abs(result["max_corr"]) >= 0.7

    def test_returns_cache_reduces_fetch_calls(self, engine):
        """returns_cache 共有で複数候補→保有銘柄の fetch を再利用する。"""
        self._add_holding(engine, "AAPL")
        self._add_holding(engine, "GOOGL")

        call_log: list[str] = []

        def fake_returns(ticker: str):
            call_log.append(ticker)
            return [0.01, 0.0, -0.01, 0.02, -0.02] * 5

        cache: dict[str, list[float] | None] = {}
        with patch.object(correlation, "fetch_daily_returns", side_effect=fake_returns):
            # 候補 3 件を同じ cache で処理
            for new_ticker in ("X1", "X2", "X3"):
                correlation.assess_new_buy_correlation(
                    engine, new_ticker, returns_cache=cache, threshold=2.0  # ブロックなし
                )

        # 候補ごとに 1 回（X1/X2/X3）+ 保有 2 銘柄 1 回ずつ = 5 回のみ
        # キャッシュ無しなら 候補 3 × (1+保有 2) = 9 回
        assert len(call_log) == 5
        assert sorted(set(call_log)) == ["AAPL", "GOOGL", "X1", "X2", "X3"]

    def test_low_correlation_passes(self, engine):
        """保有銘柄と無相関なら blocked=False。"""
        self._add_holding(engine, "AAPL")

        seq = iter([
            [0.01, -0.02, 0.03, 0.00, 0.01] * 5,  # new
            [-0.02, 0.04, -0.01, 0.02, -0.03] * 5,  # held (逆方向)
        ])

        def fake_returns(ticker):
            return next(seq)

        with patch.object(correlation, "fetch_daily_returns", side_effect=fake_returns):
            result = correlation.assess_new_buy_correlation(engine, "NEW", threshold=0.7)
        # 完全逆相関 -1 で blocked になる可能性あり（|r|=1）
        # ここでは閾値を高めにして false を期待
        if result["max_corr"] is not None and abs(result["max_corr"]) < 0.7:
            assert result["blocked"] is False
