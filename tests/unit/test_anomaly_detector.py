"""異常検知 + HALT 機構のテスト（v2.10 Phase I-10）。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.portfolio import anomaly_detector


@pytest.fixture
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "anomaly.sqlite")
    create_all(eng)
    return eng


@pytest.fixture
def isolated_halt(tmp_path: Path, monkeypatch):
    """HALT ファイルを tmp_path に分離（既存ユーザー HALT を汚染しない）。"""
    fake = tmp_path / "HALT"
    monkeypatch.setattr(anomaly_detector, "DEFAULT_HALT_FILE", fake)
    yield fake
    if fake.exists():
        fake.unlink()


class TestHaltState:
    def test_no_halt_file_returns_not_halted(self, isolated_halt: Path):
        assert anomaly_detector.is_halted() is False
        assert anomaly_detector.get_halt_state() == {"halted": False}

    def test_trigger_halt_writes_json(self, isolated_halt: Path):
        anomaly_detector.trigger_halt("test reason", source="unit_test")
        assert isolated_halt.exists()
        data = json.loads(isolated_halt.read_text(encoding="utf-8"))
        assert data["reason"] == "test reason"
        assert data["source"] == "unit_test"
        assert "triggered_at" in data

    def test_is_halted_true_after_trigger(self, isolated_halt: Path):
        anomaly_detector.trigger_halt("r", source="t")
        assert anomaly_detector.is_halted() is True

    def test_trigger_halt_does_not_overwrite(self, isolated_halt: Path):
        anomaly_detector.trigger_halt("first", source="a")
        anomaly_detector.trigger_halt("second", source="b")
        data = json.loads(isolated_halt.read_text(encoding="utf-8"))
        assert data["reason"] == "first"  # 最初の発火元を保持

    def test_clear_halt_removes_file(self, isolated_halt: Path):
        anomaly_detector.trigger_halt("r", source="t")
        assert anomaly_detector.clear_halt() is True
        assert not isolated_halt.exists()
        assert anomaly_detector.clear_halt() is False  # 二度目は False

    def test_plain_text_halt_file_compat(self, isolated_halt: Path):
        """既存運用の plain text HALT ファイル互換性。"""
        isolated_halt.parent.mkdir(parents=True, exist_ok=True)
        isolated_halt.write_text("manual halt by ops")
        state = anomaly_detector.get_halt_state()
        assert state["halted"] is True
        assert "manual halt by ops" in state["reason"]


class TestDrawdownDetection:
    def _add_snapshot(self, engine, date: dt.date, total_jpy: float, daily_pnl: float):
        with Session(engine) as s:
            s.add(
                PortfolioSnapshot(
                    date=date,
                    total_assets_jpy=total_jpy,
                    cash_jpy=total_jpy,
                    us_stocks_value_jpy=0.0,
                    jp_stocks_value_jpy=0.0,
                    satellite_value_jpy=0.0,
                    core_value_jpy=0.0,
                    usd_jpy_rate=150.0,
                    holding_count=0,
                    daily_pnl_jpy=daily_pnl,
                )
            )
            s.commit()

    def test_no_drawdown_returns_none(self, engine):
        today = dt.date(2026, 5, 31)
        self._add_snapshot(engine, today - dt.timedelta(days=2), 100000, 0)
        self._add_snapshot(engine, today - dt.timedelta(days=1), 101000, 1000)
        self._add_snapshot(engine, today, 102000, 1000)
        result = anomaly_detector.detect_portfolio_drawdown(engine, today=today)
        assert result is None

    def test_cumulative_drawdown_triggers(self, engine):
        today = dt.date(2026, 5, 31)
        self._add_snapshot(engine, today - dt.timedelta(days=3), 100000, 0)
        self._add_snapshot(engine, today - dt.timedelta(days=2), 110000, 10000)
        self._add_snapshot(engine, today - dt.timedelta(days=1), 95000, -15000)
        self._add_snapshot(engine, today, 90000, -5000)  # peak 110000 から -18%
        result = anomaly_detector.detect_portfolio_drawdown(engine, today=today)
        assert result is not None
        assert result["type"] == "cumulative_drawdown"
        assert result["value"] < -0.15

    def test_daily_drawdown_triggers(self, engine):
        today = dt.date(2026, 5, 31)
        self._add_snapshot(engine, today - dt.timedelta(days=1), 100000, 0)
        self._add_snapshot(engine, today, 92000, -8000)  # daily -8%
        result = anomaly_detector.detect_portfolio_drawdown(engine, today=today)
        assert result is not None
        assert result["type"] in ("daily_drawdown", "cumulative_drawdown")
        assert result["value"] <= -0.07

    def test_insufficient_data_returns_none(self, engine):
        today = dt.date(2026, 5, 31)
        self._add_snapshot(engine, today, 50000, -100000)  # 1 件のみ
        result = anomaly_detector.detect_portfolio_drawdown(engine, today=today)
        assert result is None  # 推測しない


class TestConsecutiveFailures:
    def _add_decision(self, engine, date: dt.date, ticker: str, status: str):
        with Session(engine) as s:
            s.add(
                Decision(
                    date=date,
                    ticker=ticker,
                    action="buy",
                    status=status,
                    thesis_at_decision="test",
                )
            )
            s.commit()

    def test_no_failures_returns_none(self, engine):
        today = dt.date(2026, 5, 31)
        self._add_decision(engine, today, "AAPL", "approved")
        result = anomaly_detector.detect_consecutive_fill_failures(engine, today=today)
        assert result is None

    def test_three_business_days_consecutive_skips_triggers(self, engine):
        """v2.10 致命 5 修正: 3 営業日連続 skip で発火（同日内複数ではない）。"""
        today = dt.date(2026, 5, 31)
        self._add_decision(engine, today - dt.timedelta(days=2), "X1", "skipped")
        self._add_decision(engine, today - dt.timedelta(days=1), "X2", "skipped")
        self._add_decision(engine, today, "X3", "skipped")
        result = anomaly_detector.detect_consecutive_fill_failures(engine, today=today)
        assert result is not None
        assert result["type"] == "consecutive_fill_failures"
        assert result["value"] >= 3

    def test_same_day_multiple_skips_counted_as_one_day(self, engine):
        """同日内の複数 skipped は 1 営業日扱い（営業日ベース判定）。"""
        today = dt.date(2026, 5, 31)
        self._add_decision(engine, today, "X1", "skipped")
        self._add_decision(engine, today, "X2", "skipped")
        self._add_decision(engine, today, "X3", "skipped")
        # 1 営業日連続 skip しかない → threshold=3 未満
        result = anomaly_detector.detect_consecutive_fill_failures(engine, today=today)
        assert result is None

    def test_same_day_partial_approved_breaks_streak(self, engine):
        """同日に approved が 1 つでもあれば「失敗営業日」ではない。"""
        today = dt.date(2026, 5, 31)
        self._add_decision(engine, today - dt.timedelta(days=2), "X1", "skipped")
        self._add_decision(engine, today - dt.timedelta(days=1), "X2", "skipped")
        self._add_decision(engine, today, "X3", "skipped")
        self._add_decision(engine, today, "GOOD", "approved")  # 同日 approved
        # today は失敗営業日ではない → 連続途切れ
        result = anomaly_detector.detect_consecutive_fill_failures(engine, today=today)
        assert result is None

    def test_approved_breaks_consecutive_streak(self, engine):
        """別営業日に approved が出れば連続途切れ。"""
        today = dt.date(2026, 5, 31)
        # 5/29 skip, 5/30 skip, 5/31 approved → 5/31 で連続途切れ
        self._add_decision(engine, today - dt.timedelta(days=2), "S1", "skipped")
        self._add_decision(engine, today - dt.timedelta(days=1), "S2", "skipped")
        self._add_decision(engine, today, "OK", "approved")
        result = anomaly_detector.detect_consecutive_fill_failures(engine, today=today)
        assert result is None


class TestPriceAnomaly:
    def test_within_threshold_is_normal(self):
        assert anomaly_detector.is_price_anomaly(105.0, 100.0) is False

    def test_above_threshold_is_anomaly(self):
        assert anomaly_detector.is_price_anomaly(130.0, 100.0) is True

    def test_zero_reference_is_normal(self):
        assert anomaly_detector.is_price_anomaly(100.0, 0.0) is False  # 推測しない

    def test_zero_current_is_normal(self):
        assert anomaly_detector.is_price_anomaly(0.0, 100.0) is False


class TestRunAnomalyCheck:
    def test_already_halted_returns_early(self, engine, isolated_halt: Path):
        anomaly_detector.trigger_halt("pre-existing", source="setup")
        result = anomaly_detector.run_anomaly_check(engine)
        assert result["status"] == "already_halted"

    def test_no_detections_returns_ok(self, engine, isolated_halt: Path):
        result = anomaly_detector.run_anomaly_check(engine)
        assert result["status"] == "ok"
        assert result["detections"] == []

    def test_manual_mode_warns_no_halt(self, engine, isolated_halt: Path, monkeypatch):
        # automation_mode = manual (default)
        # DD を意図的に発生させる
        today = dt.date(2026, 5, 31)
        with Session(engine) as s:
            s.add(
                PortfolioSnapshot(
                    date=today - dt.timedelta(days=1),
                    total_assets_jpy=100000,
                    cash_jpy=100000, us_stocks_value_jpy=0, jp_stocks_value_jpy=0,
                    satellite_value_jpy=0, core_value_jpy=0,
                    usd_jpy_rate=150.0, holding_count=0, daily_pnl_jpy=0,
                )
            )
            s.add(
                PortfolioSnapshot(
                    date=today,
                    total_assets_jpy=80000,  # cum -20%, daily -20%
                    cash_jpy=80000, us_stocks_value_jpy=0, jp_stocks_value_jpy=0,
                    satellite_value_jpy=0, core_value_jpy=0,
                    usd_jpy_rate=150.0, holding_count=0, daily_pnl_jpy=-20000,
                )
            )
            s.commit()
        result = anomaly_detector.run_anomaly_check(engine, today=today)
        assert result["status"] == "warning"
        assert len(result["detections"]) >= 1
        assert not isolated_halt.exists()  # manual モードは HALT 発火しない

    def test_auto_mode_triggers_halt(self, engine, isolated_halt: Path, monkeypatch):
        monkeypatch.setenv("WILLE_AUTOMATION_AUTO", "1")
        today = dt.date(2026, 5, 31)
        with Session(engine) as s:
            s.add(
                PortfolioSnapshot(
                    date=today - dt.timedelta(days=1),
                    total_assets_jpy=100000,
                    cash_jpy=100000, us_stocks_value_jpy=0, jp_stocks_value_jpy=0,
                    satellite_value_jpy=0, core_value_jpy=0,
                    usd_jpy_rate=150.0, holding_count=0, daily_pnl_jpy=0,
                )
            )
            s.add(
                PortfolioSnapshot(
                    date=today,
                    total_assets_jpy=80000,
                    cash_jpy=80000, us_stocks_value_jpy=0, jp_stocks_value_jpy=0,
                    satellite_value_jpy=0, core_value_jpy=0,
                    usd_jpy_rate=150.0, holding_count=0, daily_pnl_jpy=-20000,
                )
            )
            s.commit()
        result = anomaly_detector.run_anomaly_check(engine, today=today)
        assert result["status"] == "halt_triggered"
        assert isolated_halt.exists()
        assert anomaly_detector.is_halted() is True
