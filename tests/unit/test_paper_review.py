"""P4-4 ペーパー評価の向け直し（プロセス遵守＋リスク調整）の単体テスト。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.paper_review import (
    check_process_adherence,
    max_drawdown,
    risk_adjusted_vs_passive,
    sharpe,
)
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import SellSignal


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "rev.sqlite")
    create_all(eng)
    return eng


def _pos(eng, ticker: str, buy: float, qty: int) -> None:
    with Session(eng) as s:
        s.add(
            Portfolio(
                ticker=ticker, buy_date=dt.date(2026, 5, 25), buy_price=buy, qty=qty,
                currency="JPY", strategy_category="中期", target_period_days=120,
                target_pct=0.0, stop_loss_pct=-0.12, target_date=dt.date(2026, 9, 25),
                thesis="core", status="active",
            )
        )
        s.commit()


def _profit_cap_signal(eng, ticker: str) -> None:
    with Session(eng) as s:
        s.add(SellSignal(ticker=ticker, signal_type="profit_taking", score=60, ai_confidence=0.5))
        s.commit()


def _ok(findings, rule: str) -> bool:
    return next(f.ok for f in findings if f.rule == rule)


class TestProcessAdherence:
    def test_disciplined_portfolio_passes(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _pos(eng, "7203", 1000.0, 10)  # 取得¥10,000
        findings = check_process_adherence(eng, cash_jpy=90_000.0)  # 現金90%
        assert all(f.ok for f in findings)

    def test_profit_cap_signal_flags_violation(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _pos(eng, "7203", 1000.0, 10)
        _profit_cap_signal(eng, "7203")  # B'違反＝利確シグナルの痕跡
        findings = check_process_adherence(eng, cash_jpy=90_000.0)
        assert _ok(findings, "利確で刻まない(B')") is False

    def test_cash_floor_breach_flags(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _pos(eng, "7203", 1000.0, 95)  # 取得¥95,000
        findings = check_process_adherence(eng, cash_jpy=5_000.0)  # 現金5% < 20%
        assert _ok(findings, "現金下限を維持") is False

    def test_overweight_position_flags(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _pos(eng, "7203", 1000.0, 50)  # 取得¥50,000＝総資産¥100kの50% > 20%
        findings = check_process_adherence(eng, cash_jpy=50_000.0)
        assert _ok(findings, "1銘柄上限以下") is False


class TestRiskAdjusted:
    def test_sharpe_zero_when_flat(self) -> None:
        assert sharpe([0.0, 0.0, 0.0]) == 0.0

    def test_sharpe_positive_for_net_gains(self) -> None:
        # 平均プラス＋変動あり → 正のSharpe（定数列は分散0で0を返す仕様）
        assert sharpe([0.02, -0.005, 0.015, 0.01]) > 0

    def test_max_drawdown(self) -> None:
        assert max_drawdown([100, 120, 60, 90]) == pytest.approx(-0.5)  # 120→60 で -50%

    def test_risk_adjusted_excess(self) -> None:
        r = risk_adjusted_vs_passive([100, 110, 121], [100, 105, 110])
        assert r["excess_total"] == pytest.approx(0.21 - 0.10, abs=1e-9)
        assert "core_max_dd" in r and "passive_sharpe" in r
