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


_OFFICIAL_SOURCES = ("ds_dispatch", "manual")


def collect_feedback_records(
    engine: Engine, *, since: dt.date | None = None, broker_mode: str | None = None,
    official_only: bool = False,
) -> list[dict[str, Any]]:
    """評価期日到来済の decision を性格別に展開してフィードバック records を返す。

    1 つの decision を複数性格が fill していた場合は、それぞれを別 record として展開。
    `broker_mode`（paper/live）を渡すと、その broker_mode の record だけに絞る
    （昇格/配分を paper=edge検証 と live=実運用 で分離するため・既定 None=全件）。
    `official_only`=True で gate⑥ 公式集合述語と完全同値に絞る（codex P1）:
    filled_via∈(ds_dispatch,manual) ∧ entry_market_regime あり ∧ actual_return あり ∧
    stop_pct ∧ entry_broker_mode==broker_mode（broker_mode の authority も Decision 側）。
    ※ 戻り値は personalities_filled 展開後の **fill record 粒度**（1 Decision を複数機体が fill
    すると複数 record）。gate n は Decision 粒度なので、件数一致を要するなら decision_id で uniq する。
    各 record には broker_mode（Portfolio 優先、無ければ Decision.entry_broker_mode）を含める。
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

    # codex P0: 同一 (ticker, personality) を複数 decision で取引すると、(ticker, personality)
    # 引きでは別 decision の Portfolio（broker_mode/exit_reason/days_held）が混ざる。
    # decision_id 優先で紐付け、legacy（decision_id 無し）だけ (ticker, personality) fallback。
    port_by_decision_personality: dict[tuple[int, str | None], Portfolio] = {}
    port_by_ticker_personality: dict[tuple[str, str | None], Portfolio] = {}
    for p in ports:
        did = getattr(p, "decision_id", None)
        if did is not None:
            port_by_decision_personality[(did, p.personality)] = p
        port_by_ticker_personality[(p.ticker, p.personality)] = p

    for d in decs:
        if since and d.evaluation_date and d.evaluation_date < since:
            continue
        filled = list(d.personalities_filled or []) or [None]
        for personality_name in filled:
            # decision_id 優先 → 無ければ legacy の (ticker, personality)。
            port = port_by_decision_personality.get((d.id, personality_name))
            if port is None:
                port = port_by_ticker_personality.get((d.ticker, personality_name))
            # broker_mode は Portfolio 優先（紐付けが正確）、無ければ Decision の entry_broker_mode。
            rec_broker_mode = (
                getattr(port, "broker_mode", None) if port else None
            ) or d.entry_broker_mode
            # broker_mode フィルタの authority（codex P1）:
            #  - official_only=True: gate.py と同じく Decision.entry_broker_mode を authority にする
            #    （下の official_only 条件で判定）。Portfolio.broker_mode 不整合データで gate n とズレない。
            #  - official_only=False: 従来どおり Portfolio 優先の rec_broker_mode で絞る。
            if not official_only and broker_mode is not None and rec_broker_mode != broker_mode:
                continue
            if official_only and not (
                d.filled_via in _OFFICIAL_SOURCES
                and d.entry_market_regime is not None
                and d.actual_return is not None
                and d.stop_pct
                and (broker_mode is None or d.entry_broker_mode == broker_mode)
            ):
                # gate.py の公式集合述語と完全同値（filled_via 公式 ∧ regime ∧ actual_return ∧
                # stop_pct ∧ entry_broker_mode==broker_mode）。
                continue
            records.append(
                {
                    "decision_id": d.id,
                    "broker_mode": rec_broker_mode,
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
                    # Review Report v2 用の明細フィールド（exit/stance/局面別 集計 + 透明性）
                    "action": d.action,
                    "filled_via": d.filled_via,
                    "entry_date": d.entry_date.isoformat() if d.entry_date else None,
                    "entry_market_regime": d.entry_market_regime,
                    "benchmark_return": d.benchmark_return,
                    "target_period_days": d.target_period_days,
                    "thesis": (d.thesis_at_decision or "")[:120],
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
