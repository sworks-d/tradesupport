"""portfolio-level リスク指標の単体テスト（v2.10 Phase 2A）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.portfolio.risk_metrics import (
    compute_max_drawdown,
    compute_risk_metrics,
    compute_var_cvar,
)


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "risk.sqlite")
    create_all(eng)
    return eng


def _add_snapshot(eng, day: dt.date, total: float) -> None:
    with Session(eng, expire_on_commit=False) as s:
        s.add(
            PortfolioSnapshot(
                date=day,
                total_assets_jpy=total,
                cash_jpy=total * 0.5,
                us_stocks_value_jpy=0,
                jp_stocks_value_jpy=total * 0.5,
                satellite_value_jpy=0,
                core_value_jpy=total * 0.5,
                usd_jpy_rate=150.0,
                holding_count=0,
                daily_pnl_jpy=0.0,
            )
        )
        s.commit()


class TestVarCvar:
    def test_サンプル不足でNone(self) -> None:
        var, cvar = compute_var_cvar([0.01, -0.02, 0.005])
        assert var is None and cvar is None

    def test_NaN含むとNone_推測しない(self) -> None:
        rets = [0.01] * 19 + [float("nan")]
        var, cvar = compute_var_cvar(rets)
        assert var is None and cvar is None

    def test_VaR95は分布の5分位(self) -> None:
        # 100 個 -0.05 〜 +0.05 の昇順、VaR(95%) は 5 番目 ≈ -0.05 付近
        rets = [(-0.05) + i * 0.001 for i in range(100)]
        var, cvar = compute_var_cvar(rets, confidence=0.95)
        # 5 番目（idx=5）= -0.05 + 0.005 = -0.045
        assert var is not None
        assert abs(var - (-0.045)) < 0.01
        # CVaR は VaR より悪い側の平均（より負）
        assert cvar is not None
        assert cvar <= var


class TestMaxDrawdown:
    def test_snapshotなしでNone(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert (
            compute_max_drawdown(
                current_total=100_000.0, lookback_days=60, engine=eng
            )
            is None
        )

    def test_ピークから下落でDD計算(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        today = dt.date.today()
        _add_snapshot(eng, today - dt.timedelta(days=10), 100_000.0)
        _add_snapshot(eng, today - dt.timedelta(days=5), 120_000.0)  # ピーク
        _add_snapshot(eng, today - dt.timedelta(days=1), 100_000.0)
        dd = compute_max_drawdown(
            current_total=100_000.0, lookback_days=60, engine=eng
        )
        # peak 120k → 現在 100k → DD = -1/6 ≈ -16.7%
        assert dd is not None
        assert abs(dd - (-1 / 6)) < 0.01


class TestComputeRiskMetrics:
    def test_サンプル不足でinsufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        r = compute_risk_metrics(eng)
        assert r["status"] == "insufficient_data"

    def test_十分なサンプルでVaR_DD計算(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        today = dt.date.today()
        # 25 日分の snapshot（ランダム的に増減）
        base = 100_000.0
        values = [
            100_000, 101_000, 99_000, 102_000, 100_500, 103_000, 101_500,
            105_000, 104_000, 107_000, 105_500, 109_000, 108_000, 112_000,
            110_000, 113_000, 111_500, 115_000, 114_000, 117_000,
            116_500, 119_000, 117_000, 121_000, 119_500,
        ]
        for i, v in enumerate(values):
            _add_snapshot(eng, today - dt.timedelta(days=25 - i), float(v))

        r = compute_risk_metrics(eng, current_total=119_500.0)
        assert r["status"] == "active"
        assert r["samples"] == 25
        assert r["var_95"] is not None
        assert r["cvar_95"] is not None
        assert r["max_drawdown"] is not None
        assert r["alert_level"] in ("正常", "警告", "危険", "致命的")

    def test_DDアラート段階(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        today = dt.date.today()
        # 30 日かけて急上昇 → 急下落（25% DD）
        for i in range(20):
            _add_snapshot(eng, today - dt.timedelta(days=30 - i), 100_000.0)
        _add_snapshot(eng, today - dt.timedelta(days=5), 150_000.0)  # ピーク
        # 現在価額 = 112,500 → DD = (112500-150000)/150000 = -25%
        r = compute_risk_metrics(eng, current_total=112_500.0)
        assert r["status"] == "active"
        # 25% DD → critical
        assert r["alert_level"] == "致命的"

    def test_可変_DD閾値(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        today = dt.date.today()
        for i in range(25):
            _add_snapshot(eng, today - dt.timedelta(days=25 - i), 100_000.0 + i * 100)
        # 10% DD でも閾値を 5% にすれば警告に
        r = compute_risk_metrics(
            eng,
            current_total=90_000.0,
            dd_thresholds={
                "warning": 0.05,
                "danger": 0.10,
                "critical": 0.15,
            },
        )
        assert r["alert_level"] in ("警告", "危険", "致命的")
