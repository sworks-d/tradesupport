"""P6-a：評価メトリクス（R-multiple / hit-miss / Track Record）の単体テスト。"""

from __future__ import annotations

import pytest

from trading_agent.evaluation.metrics import (
    MIN_SAMPLE,
    EvalResult,
    build_track_record,
    evaluate_position,
)


class TestEvaluatePosition:
    def test_hit_at_target(self) -> None:
        r = evaluate_position(entry_price=100, exit_price=120, target_return=0.15, stop_pct=0.10)
        assert r.outcome == "hit"
        assert r.actual_return == 0.2
        assert r.r_multiple == 2.0  # +20% ÷ 10%stop = 2R

    def test_miss_at_stop(self) -> None:
        r = evaluate_position(entry_price=100, exit_price=88, target_return=0.15, stop_pct=0.10)
        assert r.outcome == "miss"  # -12% ≤ -10%
        assert r.r_multiple == -1.2

    def test_neutral_between(self) -> None:
        r = evaluate_position(entry_price=100, exit_price=105, target_return=0.15, stop_pct=0.10)
        assert r.outcome == "neutral"

    def test_excess_return(self) -> None:
        r = evaluate_position(
            entry_price=100, exit_price=110, target_return=0.15, stop_pct=0.10,
            benchmark_return=0.04,
        )
        assert r.excess_return() == pytest.approx(0.06)

    def test_invalid_entry_raises(self) -> None:
        with pytest.raises(ValueError):
            evaluate_position(entry_price=0, exit_price=100, target_return=0.1, stop_pct=0.1)


class TestTrackRecord:
    def _r(self, outcome: str, ret: float, rm: float, bench: float | None = None) -> EvalResult:
        return EvalResult(actual_return=ret, r_multiple=rm, outcome=outcome, benchmark_return=bench)

    def test_hit_rate_excludes_neutral(self) -> None:
        tr = build_track_record([
            self._r("hit", 0.2, 2.0), self._r("miss", -0.1, -1.0),
            self._r("hit", 0.15, 1.5), self._r("neutral", 0.02, 0.2),
        ])
        assert tr.n == 4
        assert tr.hit_rate == round(2 / 3, 3)  # neutral は分母に入れない

    def test_avg_metrics(self) -> None:
        tr = build_track_record([self._r("hit", 0.2, 2.0), self._r("miss", -0.1, -1.0)])
        assert tr.avg_return == 0.05
        assert tr.avg_r == 0.5

    def test_excess_when_benchmark_present(self) -> None:
        tr = build_track_record([
            self._r("hit", 0.2, 2.0, 0.05), self._r("miss", -0.1, -1.0, 0.0),
        ])
        assert tr.avg_excess == round((0.15 + -0.1) / 2, 4)

    def test_provisional_below_min_sample(self) -> None:
        tr = build_track_record([self._r("hit", 0.1, 1.0)])
        assert tr.provisional is True

    def test_not_provisional_at_min_sample(self) -> None:
        tr = build_track_record([self._r("hit", 0.1, 1.0)] * MIN_SAMPLE)
        assert tr.provisional is False

    def test_empty(self) -> None:
        tr = build_track_record([])
        assert tr.n == 0 and tr.hit_rate is None and tr.provisional is True
