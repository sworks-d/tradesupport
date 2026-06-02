"""P6-b：評価ジョブ（record_entry / evaluate_due_decisions）の単体テスト。価格lookup注入。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.job import (
    evaluate_due_decisions,
    record_entry,
    stamp_evaluation_fields,
)
from trading_agent.models.decisions import Decision


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "eval.sqlite")
    create_all(eng)
    return eng


def _seed(engine, ticker: str = "NVDA", status: str = "approved") -> int:
    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=dt.date(2026, 1, 1), ticker=ticker, action="buy", status=status)
        s.add(d)
        s.commit()
        s.refresh(d)
        return d.id


class TestRecordEntry:
    def test_stamps_entry_and_eval_date(self, engine) -> None:
        did = _seed(engine)
        ok = record_entry(
            engine, did, entry_price=100.0, stop_pct=0.12, target_return=0.2,
            target_period_days=90, on_date=dt.date(2026, 1, 1),
        )
        assert ok
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.entry_price == 100.0
            assert d.stop_pct == 0.12
            assert d.evaluation_date == dt.date(2026, 4, 1)  # +90日
            assert d.status == "ordered"


class TestEvaluateDue:
    def _prep(self, engine, *, entry, evaldate, status="ordered", ticker="NVDA") -> int:
        did = _seed(engine, ticker, status)
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            d.entry_price = entry
            d.stop_pct = 0.12
            d.expected_return = 0.2
            d.evaluation_date = evaldate
            s.add(d)
            s.commit()
        return did

    def test_evaluates_when_due(self, engine) -> None:
        did = self._prep(engine, entry=100.0, evaldate=dt.date(2026, 4, 1))
        n, tr = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 130.0, today=dt.date(2026, 4, 2)
        )
        assert n == 1
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.hit_or_miss == "hit"  # +30% ≥ target 20%
            assert d.actual_return == 0.3
            assert d.evaluated_at is not None
        assert tr.n == 1

    def test_not_evaluated_before_due(self, engine) -> None:
        # 評価期日前は据え置き（先読みしない）
        self._prep(engine, entry=100.0, evaldate=dt.date(2026, 4, 1))
        n, _ = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 130.0, today=dt.date(2026, 3, 1)
        )
        assert n == 0

    def test_miss_when_stopped(self, engine) -> None:
        did = self._prep(engine, entry=100.0, evaldate=dt.date(2026, 4, 1))
        evaluate_due_decisions(engine, price_lookup=lambda _t: 85.0, today=dt.date(2026, 4, 2))
        with Session(engine) as s:
            assert s.get(Decision, did).hit_or_miss == "miss"  # -15% ≤ -12%

    def test_skips_without_entry(self, engine) -> None:
        # entry未記録は評価対象外
        did = _seed(engine, status="ordered")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            d.evaluation_date = dt.date(2026, 4, 1)
            s.add(d)
            s.commit()
        n, _ = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 130.0, today=dt.date(2026, 4, 2)
        )
        assert n == 0

    def test_idempotent_not_reevaluated(self, engine) -> None:
        self._prep(engine, entry=100.0, evaldate=dt.date(2026, 4, 1))
        evaluate_due_decisions(engine, price_lookup=lambda _t: 130.0, today=dt.date(2026, 4, 2))
        n2, _ = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 200.0, today=dt.date(2026, 5, 2)
        )
        assert n2 == 0  # 既に評価済（pendingでない）は再評価しない

    def test_track_record_excess_with_benchmark(self, engine) -> None:
        self._prep(engine, entry=100.0, evaldate=dt.date(2026, 4, 1))
        _n, tr = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 120.0,
            benchmark_lookup=lambda _t: 0.05, today=dt.date(2026, 4, 2),
        )
        assert tr.avg_excess == pytest.approx(0.15)  # +20% − 5%


class TestStampEvaluationFields:
    """P0: status='filled' 直書き経路が評価に乗るための前提フィールド補完。"""

    def test_stamps_defaults(self, engine) -> None:
        did = _seed(engine, status="filled")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            d.entry_price = 100.0  # fill 経路で先にセットされる想定
            stamp_evaluation_fields(d, on_date=dt.date(2026, 1, 1))
            s.add(d)
            s.commit()
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.stop_pct == 0.10
            assert d.expected_return == 0.20
            assert d.target_period_days == 90
            assert d.evaluation_date == dt.date(2026, 4, 1)  # +90日
            assert d.status == "filled"  # status は触らない

    def test_respects_existing_values(self, engine) -> None:
        # katsuragi_dispatch 等で設定済みの値は上書きしない
        did = _seed(engine, status="filled")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            d.stop_pct = 0.08
            d.target_period_days = 45
            stamp_evaluation_fields(d, on_date=dt.date(2026, 1, 1))
            s.add(d)
            s.commit()
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.stop_pct == 0.08  # 既存値尊重
            assert d.target_period_days == 45
            assert d.evaluation_date == dt.date(2026, 2, 15)  # +45日

    def test_stamps_entry_market_regime(self, engine) -> None:
        # A3: エントリ時点 regime を固定保存（ゲート⑥両局面判定）。既存値は尊重。
        did = _seed(engine, status="filled")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            stamp_evaluation_fields(d, on_date=dt.date(2026, 1, 1), market_regime="risk_off")
            s.add(d)
            s.commit()
        with Session(engine) as s:
            assert s.get(Decision, did).entry_market_regime == "risk_off"
        # 2 回目（別 regime）は上書きしない
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            stamp_evaluation_fields(d, on_date=dt.date(2026, 1, 1), market_regime="risk_on")
            s.add(d)
            s.commit()
        with Session(engine) as s:
            assert s.get(Decision, did).entry_market_regime == "risk_off"

    def test_stamps_filled_via_and_entry_date(self, engine) -> None:
        # A7: 約定経路と実約定日を刻む（既存値は尊重）
        did = _seed(engine, status="filled")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            stamp_evaluation_fields(
                d, on_date=dt.date(2026, 3, 10), filled_via="paper_auto"
            )
            s.add(d)
            s.commit()
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.filled_via == "paper_auto"
            assert d.entry_date == dt.date(2026, 3, 10)

    def test_filled_decision_flows_into_evaluation(self, engine) -> None:
        """P0 の本丸: filled が stamp 後に評価ジョブで採点される（断絶解消の回帰）。"""
        did = _seed(engine, status="filled")
        with Session(engine, expire_on_commit=False) as s:
            d = s.get(Decision, did)
            d.entry_price = 100.0
            stamp_evaluation_fields(d, on_date=dt.date(2026, 1, 1))
            s.add(d)
            s.commit()
        n, tr = evaluate_due_decisions(
            engine, price_lookup=lambda _t: 130.0, today=dt.date(2026, 4, 2)
        )
        assert n == 1  # filled が評価対象に乗った
        assert tr.n == 1
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.hit_or_miss == "hit"  # +30% ≥ target 20%
            assert d.actual_return == 0.3
