"""Tests for discipline/exposure_coach.py and discipline/holding_health.py.

Covers:
- exposure_coach: 5 inputs → posture / D-23 DD-15% hard gate / LOW confidence fallback
- holding_health: T1 dividend cut / T3 relative perf / T4 keyword scan / max-severity priority
"""
from __future__ import annotations

import pytest

from trading_agent.discipline import exposure_coach as ec
from trading_agent.discipline import holding_health as hh


# === exposure_coach ============================================================


class TestExposureCoachBasic:
    def test_all_inputs_high_ceiling(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(
                breadth_score=80,
                uptrend_score=75,
                top_risk_score=20,
                regime_score=70,
                institutional_score=80,
            )
        )
        assert d.recommendation == "NEW_ENTRY_ALLOWED"
        assert d.confidence == "HIGH"
        assert d.ceiling_pct >= 70
        assert d.participation == "BROAD"
        assert len(d.inputs_provided) == 5

    def test_all_inputs_low_ceiling(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(
                breadth_score=20,
                uptrend_score=15,
                top_risk_score=80,
                regime_score=25,
                institutional_score=20,
            )
        )
        assert d.recommendation == "CASH_PRIORITY"
        assert d.participation == "NARROW"

    def test_d23_dd_hard_gate_overrides_everything(self) -> None:
        """D-23 -15% は他がすべて良くても強制 CASH_PRIORITY。"""
        d = ec.decide_exposure(
            ec.ExposureInputs(
                breadth_score=90,
                uptrend_score=90,
                top_risk_score=10,
                regime_score=90,
                institutional_score=90,
                portfolio_dd_pct=-0.16,  # -16%（-15%超過）
            )
        )
        assert d.recommendation == "CASH_PRIORITY"
        assert "D-23" in d.rationale

    def test_dd_at_minus_14_does_not_trigger_gate(self) -> None:
        """-14% は -15%未満なのでゲート発火しない。"""
        d = ec.decide_exposure(
            ec.ExposureInputs(
                breadth_score=80,
                uptrend_score=75,
                top_risk_score=20,
                regime_score=70,
                institutional_score=80,
                portfolio_dd_pct=-0.14,
            )
        )
        # D-23 ゲート発火していない → 通常ロジック
        assert d.recommendation == "NEW_ENTRY_ALLOWED"

    def test_no_inputs_fallback_low_confidence(self) -> None:
        d = ec.decide_exposure(ec.ExposureInputs())
        assert d.confidence == "LOW"
        # LOW confidence は保守的に REDUCE_ONLY
        assert d.recommendation == "REDUCE_ONLY"
        assert d.participation == "UNKNOWN"
        assert len(d.inputs_missing) == 5

    def test_partial_inputs_medium_confidence(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(breadth_score=70, uptrend_score=65)
        )
        assert d.confidence == "MEDIUM"  # 2/5 provided
        assert d.participation == "BROAD"


class TestExposureCoachClassification:
    def test_growth_bias(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(regime_score=75, institutional_score=80)
        )
        assert d.bias == "GROWTH"

    def test_value_bias(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(regime_score=30, institutional_score=35)
        )
        assert d.bias == "VALUE"

    def test_neutral_bias(self) -> None:
        d = ec.decide_exposure(
            ec.ExposureInputs(regime_score=50, institutional_score=50)
        )
        assert d.bias == "NEUTRAL"


# === holding_health =============================================================


class TestT1DividendCut:
    def test_t1_zero_dividend_review(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "dividend": {"latest_regular": 0, "prior_regular": 100}}
        )
        assert f.state == "REVIEW"
        assert "T1" in f.triggers_fired
        assert f.manual_check_required

    def test_t1_half_cut_review(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "dividend": {"latest_regular": 40, "prior_regular": 100}}
        )
        assert f.state == "REVIEW"
        assert "T1" in f.triggers_fired

    def test_t1_small_cut_no_trigger(self) -> None:
        """30% 減配は T1 のしきい値（50%減）を満たさない。"""
        f = hh.check_holding(
            {"ticker": "7203", "dividend": {"latest_regular": 70, "prior_regular": 100}}
        )
        assert f.state == "OK"
        assert "T1" not in f.triggers_fired

    def test_t1_no_data_no_trigger(self) -> None:
        f = hh.check_holding({"ticker": "7203"})
        assert f.state == "OK"


class TestT3CreditProxy:
    def test_t3_relative_underperf_warn(self) -> None:
        f = hh.check_holding(
            {
                "ticker": "7203",
                "perf_pct": -0.30,
                "benchmark_perf_pct": -0.05,
                "volatility": 0.30,
            }
        )
        assert f.state == "WARN"
        assert "T3" in f.triggers_fired

    def test_t3_relative_underperf_with_high_vol_warn(self) -> None:
        f = hh.check_holding(
            {
                "ticker": "7203",
                "perf_pct": -0.30,
                "benchmark_perf_pct": -0.05,
                "volatility": 0.50,  # 高ボラ
            }
        )
        assert f.state == "WARN"
        assert "T3" in f.triggers_fired
        # ボラ高は metric に記録
        t3_ev = [e for e in f.evidence if e.trigger_id == "T3"][0]
        assert "volatility" in t3_ev.metric

    def test_t3_no_underperf(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "perf_pct": -0.05, "benchmark_perf_pct": -0.08}
        )
        assert f.state == "OK"


class TestT4Disclosure:
    def test_t4_review_keyword(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "filings_text": "本日、不適切会計の疑いが報じられた..."}
        )
        assert f.state == "REVIEW"
        assert "T4" in f.triggers_fired

    def test_t4_warn_keyword(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "filings_text": "業績下方修正を発表..."}
        )
        assert f.state == "WARN"
        assert "T4" in f.triggers_fired

    def test_t4_no_keyword(self) -> None:
        f = hh.check_holding(
            {"ticker": "7203", "filings_text": "決算は予想通り..."}
        )
        assert f.state == "OK"

    def test_t4_english_keyword(self) -> None:
        f = hh.check_holding(
            {"ticker": "AAPL", "filings_text": "Company announces restatement of financials"}
        )
        assert f.state == "REVIEW"


class TestMaxSeverity:
    def test_review_overrides_warn(self) -> None:
        """T1 (REVIEW) と T3 (WARN) が同時発火しても全体は REVIEW。"""
        f = hh.check_holding(
            {
                "ticker": "7203",
                "dividend": {"latest_regular": 0, "prior_regular": 100},
                "perf_pct": -0.30,
                "benchmark_perf_pct": -0.05,
            }
        )
        assert f.state == "REVIEW"
        assert "T1" in f.triggers_fired
        assert "T3" in f.triggers_fired


class TestReport:
    def test_check_all_holdings_summary(self) -> None:
        holdings = [
            {"ticker": "OK_STOCK"},
            {"ticker": "WARN_STOCK", "filings_text": "業績下方修正"},
            {"ticker": "REVIEW_STOCK", "filings_text": "不適切会計"},
        ]
        r = hh.check_all_holdings(holdings, data_asof="2026-05-26")
        assert r.summary == {"OK": 1, "WARN": 1, "REVIEW": 1}
        assert len(r.review_tickets) == 1
        assert r.review_tickets[0].ticker == "REVIEW_STOCK"
        assert r.data_asof == "2026-05-26"
