"""判断精度の集計（Phase 2 Mini 実装・v2.10）。

Decision テーブルの hit_or_miss / actual_return から、過去 N 日の判断精度を集計する。
データが少ない段階は status="insufficient_data" を返し、ダッシュボード側で「データ蓄積中」を表示する。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision

# 「判断精度を語っていい」最低サンプル数。これ未満は accuracy=None (insufficient_data)
_MIN_FOR_ACCURACY = 5


def _bucket_source(d: Decision) -> str:
    """Decision の gendo_stance などから source を推定。"""
    stance = (d.gendo_stance or "").strip()
    if stance == "ZEELE":
        return "zeele"
    return "magi"  # 推し/要検討/利確/撤退/静観 はすべて MAGI 経由


def _empty_breakdown() -> dict[str, Any]:
    return {
        "total": 0,
        "evaluated": 0,
        "pending": 0,
        "hits": 0,
        "misses": 0,
        "neutrals": 0,
        "accuracy_pct": None,
        "avg_return_pct": None,
    }


def _summarize(decs: list[Decision]) -> dict[str, Any]:
    total = len(decs)
    evaluated = [d for d in decs if d.hit_or_miss in ("hit", "miss", "neutral")]
    hits = [d for d in evaluated if d.hit_or_miss == "hit"]
    misses = [d for d in evaluated if d.hit_or_miss == "miss"]
    neutrals = [d for d in evaluated if d.hit_or_miss == "neutral"]

    if len(evaluated) >= _MIN_FOR_ACCURACY:
        accuracy = len(hits) / len(evaluated) * 100.0
        returns = [d.actual_return for d in evaluated if d.actual_return is not None]
        avg_ret = (sum(returns) / len(returns) * 100.0) if returns else None
    else:
        accuracy = None
        avg_ret = None

    return {
        "total": total,
        "evaluated": len(evaluated),
        "pending": total - len(evaluated),
        "hits": len(hits),
        "misses": len(misses),
        "neutrals": len(neutrals),
        "accuracy_pct": accuracy,
        "avg_return_pct": avg_ret,
    }


def compute_judgment_accuracy(
    engine: Engine, lookback_days: int = 30, broker_mode: str | None = None
) -> dict[str, Any]:
    """過去 lookback_days 日の Decision から判断精度を集計。

    集計対象: action="buy" の Decision（売り判断は別途）。
    broker_mode（paper/live）を渡すと entry_broker_mode で絞る（dispatch 配分の重みを
    paper/live/legacy で混ぜない・codex High#6）。既定 None=全件（後方互換）。
    返り値の status:
      - "active": 評価済み件数 >= _MIN_FOR_ACCURACY、accuracy が意味を持つ
      - "insufficient_data": サンプル不足、accuracy=None
    """
    cutoff = dt.date.today() - dt.timedelta(days=lookback_days)
    with Session(engine) as s:
        stmt = select(Decision).where(
            col(Decision.date) >= cutoff,
            col(Decision.action) == "buy",
        )
        if broker_mode is not None:
            stmt = stmt.where(col(Decision.entry_broker_mode) == broker_mode)
        decs = list(s.exec(stmt).all())

    overall = _summarize(decs)

    # source 別（magi / zeele）
    by_source: dict[str, dict[str, Any]] = {}
    for src in ("magi", "zeele"):
        sub = [d for d in decs if _bucket_source(d) == src]
        by_source[src] = _summarize(sub) if sub else _empty_breakdown()

    # pilot 別（personalities_filled が空＝未約定はスキップ）
    by_pilot: dict[str, dict[str, Any]] = {}
    for pilot in ("REI", "ASUKA", "SHINJI", "KAWORU"):
        sub = [d for d in decs if pilot in (d.personalities_filled or [])]
        by_pilot[pilot] = _summarize(sub) if sub else _empty_breakdown()

    status = "active" if overall["evaluated"] >= _MIN_FOR_ACCURACY else "insufficient_data"

    return {
        "lookback_days": lookback_days,
        "status": status,
        "min_samples_required": _MIN_FOR_ACCURACY,
        "overall": overall,
        "by_source": by_source,
        "by_pilot": by_pilot,
    }
