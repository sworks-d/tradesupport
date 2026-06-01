"""MAGI/ZEELE フィードバック用のデータ集約。

評価ジョブが採点した decision（hit/miss + R-multiple）を性格・MAGI 判定とクロスして
`autoreport/feedback_log.json` に追記する。月次で集約して MAGI ルール調整の材料に。

集計軸：
- gendo_stance（推し/要検討/静観）× 性格（REI/ASUKA/SHINJI/KAWORU）× 結果（hit/miss）
- exit_reason（stop_loss / time_exit）× 性格
- 平均 R-multiple・命中率・days_held
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio


def collect_feedback_records(
    engine: Engine, *, since: dt.date | None = None
) -> list[dict[str, Any]]:
    """評価期日到来済の decision を性格別に展開してフィードバック records を返す。

    1 つの decision を複数性格が fill していた場合は、それぞれを別 record として展開。
    """
    records: list[dict[str, Any]] = []
    with Session(engine) as s:
        decs = s.exec(
            select(Decision)
            .where(col(Decision.evaluated_at).is_not(None))
            .where(col(Decision.hit_or_miss).in_(("hit", "miss", "neutral")))
        ).all()
        # 各 decision に紐づく closed portfolio を性格別に
        ports = s.exec(
            select(Portfolio).where(col(Portfolio.status) == "closed")
        ).all()

    port_by_ticker_personality: dict[tuple[str, str | None], Portfolio] = {}
    for p in ports:
        key = (p.ticker, p.personality)
        port_by_ticker_personality[key] = p

    for d in decs:
        if since and d.evaluation_date and d.evaluation_date < since:
            continue
        filled = list(d.personalities_filled or []) or [None]
        for personality_name in filled:
            port = port_by_ticker_personality.get((d.ticker, personality_name))
            records.append(
                {
                    "decision_id": d.id,
                    "ticker": d.ticker,
                    "evaluation_date": (
                        d.evaluation_date.isoformat() if d.evaluation_date else None
                    ),
                    "gendo_stance": d.gendo_stance or "—",
                    "personality": personality_name,
                    "entry_price": d.entry_price,
                    "exit_price": port.closed_price if port else None,
                    "exit_reason": port.closed_reason if port else None,
                    "hit_or_miss": d.hit_or_miss,
                    "actual_return": d.actual_return,
                    "stop_pct": d.stop_pct,
                    "r_multiple": (
                        (d.actual_return / d.stop_pct)
                        if (d.actual_return is not None and d.stop_pct)
                        else None
                    ),
                    "days_held": (
                        (port.closed_at - port.created_at).days
                        if port and port.closed_at and port.created_at
                        else None
                    ),
                }
            )
    return records


def append_feedback_log(
    records: list[dict[str, Any]], *, out_path: Path | None = None
) -> Path:
    """records を feedback_log.json に冪等で追記する（decision_id × personality で dedupe）。"""
    out = out_path or Path("autoreport") / "feedback_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    seen = {(r.get("decision_id"), r.get("personality")) for r in existing}
    for r in records:
        key = (r.get("decision_id"), r.get("personality"))
        if key not in seen:
            existing.append(r)
            seen.add(key)
    out.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def summarize_feedback(records: list[dict[str, Any]]) -> dict[str, Any]:
    """性格 × stance × 結果のクロス集計を返す。MAGI 調整提案の原データ。"""
    by_personality: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"hit": 0, "miss": 0, "neutral": 0, "r_sum": 0.0, "n": 0}
    )
    by_stance: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"hit": 0, "miss": 0, "neutral": 0}
    )
    by_exit: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"hit": 0, "miss": 0, "neutral": 0}
    )
    for r in records:
        outcome = r.get("hit_or_miss") or "neutral"
        p = r.get("personality") or "—"
        st = r.get("gendo_stance") or "—"
        ex = r.get("exit_reason") or "—"
        by_personality[p][outcome] += 1
        by_personality[p]["n"] += 1
        if r.get("r_multiple") is not None:
            by_personality[p]["r_sum"] += float(r["r_multiple"])
        by_stance[st][outcome] += 1
        by_exit[ex][outcome] += 1

    # 命中率・平均 R を計算
    summary_personality = {}
    for p, d in by_personality.items():
        n = d["n"] or 1
        summary_personality[p] = {
            "n": d["n"],
            "hit_rate": d["hit"] / n if n else 0.0,
            "avg_r": d["r_sum"] / n if n else 0.0,
            "hit": d["hit"],
            "miss": d["miss"],
        }

    return {
        "by_personality": summary_personality,
        "by_stance": dict(by_stance),
        "by_exit": dict(by_exit),
        "total_records": len(records),
    }
