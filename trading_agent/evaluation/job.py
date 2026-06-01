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
from trading_agent.utils.time_utils import today_jst, utcnow

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
    shares: float = 1.0,
    on_date: dt.date | None = None,
) -> bool:
    """発注時：entry/stop/target/評価期日を decision に刻む（評価の前提）。

    v2.1 TASK-E2: 複数 fill の場合、shares で加重平均する。
    最初の fill: そのまま記録 / 2 回目以降: (既存価格×既存株数 + 新価格×新株数) / 合計株数
    """
    base = on_date or today_jst()
    with Session(engine, expire_on_commit=False) as session:
        d = session.get(Decision, decision_id)
        if d is None:
            return False
        # entry_price は加重平均（v2.1 TASK-E2）
        existing_shares = float(d.shares_filled or 0.0)
        if d.entry_price is not None and existing_shares > 0:
            total = existing_shares + shares
            d.entry_price = (
                d.entry_price * existing_shares + entry_price * shares
            ) / total
            d.shares_filled = total
        else:
            d.entry_price = entry_price
            d.shares_filled = shares
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
    day = today or today_jst()
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
            # v2.1 TASK-E1: stop_pct/expected_return の fallback を撤去
            # データ不足は採点せず "skipped" として残す（hit/miss を捏造しない）
            if d.stop_pct is None or d.expected_return is None:
                d.hit_or_miss = "skipped"
                d.evaluated_at = utcnow()
                session.add(d)
                continue
            stop = d.stop_pct
            target = d.expected_return
            # v2.5 TASK-E5: benchmark 取得失敗を明示（旧版は None を黙殺）
            bench = None
            if benchmark_lookup is not None:
                try:
                    bench = benchmark_lookup(d.ticker)
                    if bench is None:
                        from trading_agent.utils.logger import get_logger
                        get_logger("evaluation").info(
                            "benchmark_unavailable", ticker=d.ticker,
                            note="benchmark_lookup returned None",
                        )
                except Exception as exc:
                    from trading_agent.utils.logger import get_logger
                    get_logger("evaluation").warning(
                        "benchmark_lookup_failed", ticker=d.ticker, error=str(exc),
                    )
            res = evaluate_position(
                entry_price=d.entry_price, exit_price=exit_price,
                target_return=target, stop_pct=stop, benchmark_return=bench,
            )
            d.actual_return = res.actual_return
            d.benchmark_return = bench
            # v2.5 TASK-E4: 計算上の near_hit/near_miss は DB には neutral として保存
            # （hit_rate の母集団は明確な hit/miss だけに維持する設計）
            # 観察用には EvalResult.outcome が near_* を保持。
            outcome_persisted = {
                "near_hit": "neutral",
                "near_miss": "neutral",
            }.get(res.outcome, res.outcome)
            d.hit_or_miss = outcome_persisted
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
    # v2.2 TASK-E3: r_multiple=0 を「stop 不明」と「実際にゼロ」で区別できないため、
    # stop_pct/actual_return が無い decision は集計から除外する（None マーキング）
    results: list[EvalResult] = []
    for d in done:
        if d.actual_return is None or not d.stop_pct:
            continue  # 評価不能データは集計から除外
        results.append(
            EvalResult(
                actual_return=d.actual_return,
                r_multiple=d.actual_return / d.stop_pct,
                outcome=d.hit_or_miss,
                benchmark_return=d.benchmark_return,
            )
        )
    return build_track_record(results)
