"""増額ゲート⑥ 単一判定（official_gate_evaluation）の単体テスト。LLM/ネット非依存。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.gate import _max_drawdown, official_gate_evaluation
from trading_agent.models.decisions import Decision
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "gate.sqlite")
    create_all(eng)
    return eng


def _add_evaluated(
    eng, *, ticker: str, actual: float, outcome: str, regime: str,
    bench: float = 0.0, stop: float = 0.10, official: bool = True,
    filled_via: str = "ds_dispatch", broker_mode: str = "paper",
) -> None:
    with Session(eng, expire_on_commit=False) as s:
        s.add(Decision(
            date=dt.date(2026, 5, 25), ticker=ticker, action="buy", status="filled",
            entry_price=1000.0, stop_pct=stop, expected_return=0.20,
            actual_return=actual, benchmark_return=bench, hit_or_miss=outcome,
            evaluated_at=utcnow(),
            entry_market_regime=regime if official else None,
            filled_via=filled_via if official else None,
            entry_broker_mode=broker_mode if official else None,
        ))
        s.commit()


class TestMaxDrawdown:
    def test_no_drawdown_when_monotonic_up(self) -> None:
        assert _max_drawdown([0.1, 0.1, 0.1]) == 0.0

    def test_drawdown_detected(self) -> None:
        # +20% → -30% で peak 1.2 → 0.84、DD = (1.2-0.84)/1.2 = 0.30
        dd = _max_drawdown([0.2, -0.3])
        assert abs(dd - 0.30) < 1e-9

    def test_empty(self) -> None:
        assert _max_drawdown([]) == 0.0


class TestOfficialGate:
    def test_empty_db_not_passed(self, tmp_path: Path) -> None:
        res = official_gate_evaluation(_engine(tmp_path), broker_mode="paper")
        assert res.passed is False
        assert res.n == 0

    def test_excludes_legacy_without_regime(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # entry_market_regime=None（legacy）は公式カウントから除外
        _add_evaluated(eng, ticker="0001", actual=0.2, outcome="hit", regime="x", official=False)
        res = official_gate_evaluation(eng, broker_mode="paper")
        assert res.n == 0

    def test_excludes_paper_auto_from_official(self, tmp_path: Path) -> None:
        # A7: paper_auto（notify sim）は公式集合から除外（ds_dispatch/manual のみ）
        eng = _engine(tmp_path)
        _add_evaluated(eng, ticker="0001", actual=0.2, outcome="hit", regime="bull",
                       filled_via="paper_auto")
        _add_evaluated(eng, ticker="0002", actual=0.2, outcome="hit", regime="bull",
                       filled_via="ds_dispatch")
        res = official_gate_evaluation(eng, broker_mode="paper")
        assert res.n == 1  # ds_dispatch のみ。paper_auto は除外

    def test_insufficient_n_not_passed(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_evaluated(eng, ticker="7203", actual=0.2, outcome="hit", regime="risk_on")
        res = official_gate_evaluation(eng, broker_mode="paper")
        assert res.passed is False
        # n 要件が落ちている
        n_crit = next(c for c in res.criteria if c.name == "評価件数")
        assert n_crit.passed is False

    def test_full_pass(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # 30 件・命中率>50%・avg_R>0.5・両局面・コスト後α>0・DD小
        # actual=+0.10, stop=0.10 → R=+1.0、bench=0 → net_excess ≈ +0.0956
        for i in range(20):
            _add_evaluated(eng, ticker=f"A{i:04d}", actual=0.10, outcome="hit",
                           regime="risk_on", bench=0.0)
        for i in range(10):
            _add_evaluated(eng, ticker=f"B{i:04d}", actual=0.08, outcome="hit",
                           regime="risk_off", bench=0.0)
        res = official_gate_evaluation(eng, broker_mode="paper")
        assert res.n == 30
        assert res.passed is True, res.summary()

    def test_full_pass_with_trailing_cycle(self, tmp_path: Path) -> None:
        # A8: trailing cycle bull/bear でも両局面が成立する
        eng = _engine(tmp_path)
        for i in range(20):
            _add_evaluated(eng, ticker=f"A{i:04d}", actual=0.10, outcome="hit",
                           regime="bull", bench=0.0)
        for i in range(10):
            _add_evaluated(eng, ticker=f"B{i:04d}", actual=0.08, outcome="hit",
                           regime="bear", bench=0.0)
        res = official_gate_evaluation(eng, broker_mode="paper")
        regime_crit = next(c for c in res.criteria if c.name == "両局面通過")
        assert regime_crit.passed is True  # bull(順境)+bear(逆境)
        assert res.passed is True, res.summary()

    def test_sideways_only_not_both_regimes(self, tmp_path: Path) -> None:
        # A8: sideways のみは両局面未達（順境でも逆境でもない）
        eng = _engine(tmp_path)
        for i in range(30):
            _add_evaluated(eng, ticker=f"A{i:04d}", actual=0.10, outcome="hit",
                           regime="sideways", bench=0.0)
        res = official_gate_evaluation(eng, broker_mode="paper")
        regime_crit = next(c for c in res.criteria if c.name == "両局面通過")
        assert regime_crit.passed is False

    def test_both_regimes_required(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # 全て risk_on（順境のみ）→ 両局面未達で不通過
        for i in range(30):
            _add_evaluated(eng, ticker=f"A{i:04d}", actual=0.10, outcome="hit",
                           regime="risk_on", bench=0.0)
        res = official_gate_evaluation(eng, broker_mode="paper")
        regime_crit = next(c for c in res.criteria if c.name == "両局面通過")
        assert regime_crit.passed is False
        assert res.passed is False

    def test_benchmark_must_be_complete(self, tmp_path: Path) -> None:
        # codex 指摘1: 30件中1件だけ benchmark 有でその α が正でも、欠損があれば
        # コスト後α criterion は fail（部分集合での誤通過防止）。
        eng = _engine(tmp_path)
        # 1 件だけ benchmark 有(正の α)、残り 29 件は benchmark なし
        _add_evaluated(eng, ticker="A0000", actual=0.10, outcome="hit",
                       regime="risk_on", bench=0.0)
        for i in range(19):
            _add_evaluated(eng, ticker=f"A{i+1:04d}", actual=0.10, outcome="hit",
                           regime="risk_on")  # bench 既定 0.0 → benchmark_return=0.0 で「有」
        # benchmark を None にしたいので別途 None で投入
        with Session(eng, expire_on_commit=False) as s:
            for i in range(10):
                s.add(Decision(
                    date=dt.date(2026, 5, 25), ticker=f"B{i:04d}", action="buy",
                    status="filled", entry_price=1000.0, stop_pct=0.10, expected_return=0.20,
                    actual_return=0.08, benchmark_return=None, hit_or_miss="hit",
                    evaluated_at=utcnow(), entry_market_regime="risk_off",
                    filled_via="ds_dispatch", entry_broker_mode="paper",
                ))
            s.commit()
        res = official_gate_evaluation(eng, broker_mode="paper")
        alpha_crit = next(c for c in res.criteria if c.name == "コスト後α")
        assert alpha_crit.passed is False  # benchmark 欠損 10 件 → fail
        assert res.passed is False

    def test_cost_after_alpha_required(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # リターンは出るが benchmark が同等以上 → コスト後α ≤ 0 で不通過
        for i in range(20):
            _add_evaluated(eng, ticker=f"A{i:04d}", actual=0.10, outcome="hit",
                           regime="risk_on", bench=0.12)
        for i in range(10):
            _add_evaluated(eng, ticker=f"B{i:04d}", actual=0.10, outcome="hit",
                           regime="risk_off", bench=0.12)
        res = official_gate_evaluation(eng, broker_mode="paper")
        alpha_crit = next(c for c in res.criteria if c.name == "コスト後α")
        assert alpha_crit.passed is False
        assert res.passed is False


class TestBrokerModeSeparation:
    """M3: paper(edge検証) と live(実運用) を gate⑥ で混ぜない（codex 指摘の汚染防止）。"""

    def test_paper_and_live_counted_separately(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # paper 3 件 / live 2 件（どちらも公式条件は満たす）
        for i in range(3):
            _add_evaluated(eng, ticker=f"P{i:04d}", actual=0.10, outcome="hit",
                           regime="bull", broker_mode="paper")
        for i in range(2):
            _add_evaluated(eng, ticker=f"L{i:04d}", actual=0.10, outcome="hit",
                           regime="bull", filled_via="manual", broker_mode="live")
        assert official_gate_evaluation(eng, broker_mode="paper").n == 3
        assert official_gate_evaluation(eng, broker_mode="live").n == 2

    def test_combined_reference_sums_both_but_not_actionable(self, tmp_path: Path) -> None:
        from trading_agent.evaluation.gate import combined_gate_reference
        eng = _engine(tmp_path)
        _add_evaluated(eng, ticker="P0001", actual=0.10, outcome="hit",
                       regime="bull", broker_mode="paper")
        _add_evaluated(eng, ticker="L0001", actual=0.10, outcome="hit",
                       regime="bull", filled_via="manual", broker_mode="live")
        ref = combined_gate_reference(eng)
        assert ref.n == 2  # paper+live 合算
        assert ref.actionable is False  # 増額根拠にしない

    def test_manual_paper_not_leaked_into_live(self, tmp_path: Path) -> None:
        # filled_via=manual でも broker_mode=paper なら live 集合に入らない
        # （codex 指摘: filled_via だけで live 判定してはいけない）。
        eng = _engine(tmp_path)
        _add_evaluated(eng, ticker="X0001", actual=0.10, outcome="hit",
                       regime="bull", filled_via="manual", broker_mode="paper")
        assert official_gate_evaluation(eng, broker_mode="live").n == 0
        assert official_gate_evaluation(eng, broker_mode="paper").n == 1

    def test_invalid_broker_mode_raises(self, tmp_path: Path) -> None:
        import pytest
        with pytest.raises(ValueError):
            official_gate_evaluation(_engine(tmp_path), broker_mode="both")
