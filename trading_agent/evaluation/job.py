"""P6-b：評価ジョブ（評価期日到来分を実価格で採点）。RESEARCH_METHODS 領域4。

発注時に `record_entry` で entry_price / stop_pct / target / evaluation_date を decision に刻み、
評価期日が来た decision を `evaluate_due_decisions` が実価格で採点（hit/miss）し永続化する。
**先読みしない**：評価は評価期日以降にのみ行う（評価期日前は pending のまま＝前倒し評価しない）。
価格lookupは注入（テスト可能・ネット非依存）。全てコード（LLM非関与）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.evaluation.metrics import (
    EvalResult,
    TrackRecord,
    build_track_record,
    evaluate_position,
)
from trading_agent.models.decisions import Decision
from trading_agent.utils.time_utils import utcnow

PriceLookup = Callable[[str], float | None]
BenchmarkLookup = Callable[[str], float | None]  # ticker→同期間ベンチマークリターン（任意）

# 評価対象の status（保有/発注済/承認＝ポジションを持ち得る段階）
_EVALUABLE = ("approved", "order_listed", "ordered", "holding")


def record_entry(
    engine: Engine,
    decision_id: int,
    *,
    entry_price: float,
    stop_pct: float,
    target_return: float,
    target_period_days: int,
    on_date: dt.date | None = None,
) -> bool:
    """発注時：entry/stop/target/評価期日を decision に刻む（評価の前提）。"""
    base = on_date or utcnow().date()
    with Session(engine, expire_on_commit=False) as session:
        d = session.get(Decision, decision_id)
        if d is None:
            return False
        d.entry_price = entry_price
        d.stop_pct = stop_pct
        d.expected_return = target_return
        d.target_period_days = target_period_days
        d.evaluation_date = base + dt.timedelta(days=target_period_days)
        if d.status in ("approved", "order_listed"):
            d.status = "ordered"
        session.add(d)
        session.commit()
    return True


def evaluate_due_decisions(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    benchmark_lookup: BenchmarkLookup | None = None,
    today: dt.date | None = None,
) -> tuple[int, TrackRecord]:
    """評価期日が到来した未評価 decision を実価格で採点する。

    Returns: (今回評価した件数, 全評価済みの Track Record)。
    """
    day = today or utcnow().date()
    evaluated_now = 0
    with Session(engine, expire_on_commit=False) as session:
        due = session.exec(
            select(Decision)
            .where(col(Decision.hit_or_miss) == "pending")
            .where(col(Decision.status).in_(_EVALUABLE))
            .where(col(Decision.evaluation_date).is_not(None))
            .where(col(Decision.entry_price).is_not(None))
        ).all()
        for d in due:
            if d.evaluation_date is None or d.evaluation_date > day:
                continue  # 先読みしない（期日前は据え置き）
            exit_price = price_lookup(d.ticker)
            if exit_price is None or d.entry_price is None:
                continue
            stop = d.stop_pct if d.stop_pct is not None else 0.12
            target = d.expected_return if d.expected_return is not None else 0.15
            bench = benchmark_lookup(d.ticker) if benchmark_lookup is not None else None
            res = evaluate_position(
                entry_price=d.entry_price, exit_price=exit_price,
                target_return=target, stop_pct=stop, benchmark_return=bench,
            )
            d.actual_return = res.actual_return
            d.benchmark_return = bench
            d.hit_or_miss = res.outcome
            d.evaluated_at = utcnow()
            session.add(d)
            evaluated_now += 1
        session.commit()

    return evaluated_now, _track_record(engine)


def _track_record(engine: Engine) -> TrackRecord:
    """評価済み（hit/miss/neutral）decision から Track Record を集計。"""
    with Session(engine) as session:
        done = session.exec(
            select(Decision).where(col(Decision.hit_or_miss).in_(("hit", "miss", "neutral")))
        ).all()
    results = [
        EvalResult(
            actual_return=d.actual_return if d.actual_return is not None else 0.0,
            r_multiple=(d.actual_return / d.stop_pct)
            if (d.actual_return is not None and d.stop_pct)
            else 0.0,
            outcome=d.hit_or_miss,
            benchmark_return=d.benchmark_return,
        )
        for d in done
    ]
    return build_track_record(results)
