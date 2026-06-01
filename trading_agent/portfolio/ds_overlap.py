"""DS 4 機間の銘柄選定重複度分析（v2.10 Phase 1C）。

各機（REI/ASUKA/SHINJI/KAWORU）が過去 N 日に fill した銘柄リスト間の
重複度（ジャッカード係数）を計算し、「機の独立性」を測定する。

問題意識:
  4 機が独立に動いているように見えて、実は同じ銘柄を選んでいる場合、
  ポートフォリオは「分散しているように見えて実は同じベットを 4 つ」
  になる。これを定量化する。

指標:
  - jaccard(A, B) = |A ∩ B| / |A ∪ B|（0=完全独立、1=完全一致）
  - high_overlap_pairs: ジャッカード > 0.5 のペア（要警戒）
  - mean_jaccard: ペア平均（機全体の独立性）

ハルシネーション対策:
  - 過去 N 日に fill 0 件の機は計算から除外（推測しない）
  - 「同じ銘柄を別タイミングで fill」も同一銘柄として扱う（時間軸は分離しない）
  - サンプル不足時は status="insufficient_data"
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.ds_overlap")

# 重複度の警戒しきい値
_HIGH_OVERLAP_THRESHOLD = 0.5
_DS_PILOTS = ("REI", "ASUKA", "SHINJI", "KAWORU")


def _jaccard(a: set[str], b: set[str]) -> float:
    """ジャッカード係数（0-1、両方空なら 0）。"""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def compute_ds_overlap(
    engine: Engine,
    *,
    lookback_days: int = 30,
    broker_mode: str = "paper",
    pilots: tuple[str, ...] = _DS_PILOTS,
    high_overlap_threshold: float = _HIGH_OVERLAP_THRESHOLD,
) -> dict[str, Any]:
    """機別 fill 銘柄の重複度を計算する。

    Args:
        engine: DB エンジン
        lookback_days: 過去何日分を見るか
        broker_mode: "paper" or "live"
        pilots: 対象機（デフォルト 4 機）
        high_overlap_threshold: 警戒しきい値（デフォルト 0.5）

    Returns:
        機別 fill 件数 + ジャッカード行列 + 重複ペア + 集中度判定。
    """
    cutoff = dt.date.today() - dt.timedelta(days=lookback_days)

    # 機別の fill 銘柄集合
    fills_by_pilot: dict[str, set[str]] = {p: set() for p in pilots}
    with Session(engine) as s:
        ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.broker_mode) == broker_mode)
                .where(col(Portfolio.buy_date) >= cutoff)
            ).all()
        )
    for p in ports:
        if p.personality in fills_by_pilot:
            fills_by_pilot[p.personality].add(p.ticker)

    # 機別の fill 件数
    fill_counts = {pilot: len(s) for pilot, s in fills_by_pilot.items()}
    active_pilots = [p for p in pilots if fill_counts[p] > 0]

    if len(active_pilots) < 2:
        return {
            "status": "insufficient_data",
            "lookback_days": lookback_days,
            "fill_counts": fill_counts,
            "matrix": [],
            "high_overlap_pairs": [],
            "mean_jaccard": None,
            "concentration_label": "n/a",
            "reason": "less_than_2_active_pilots",
        }

    # ジャッカード行列（active のみ）
    matrix: list[list[float]] = []
    for pa in active_pilots:
        row = []
        for pb in active_pilots:
            if pa == pb:
                row.append(1.0)
            else:
                row.append(round(_jaccard(fills_by_pilot[pa], fills_by_pilot[pb]), 3))
        matrix.append(row)

    # 強重複ペア
    high_pairs: list[dict[str, Any]] = []
    all_jaccards: list[float] = []
    for i, pa in enumerate(active_pilots):
        for j in range(i + 1, len(active_pilots)):
            pb = active_pilots[j]
            j_score = matrix[i][j]
            all_jaccards.append(j_score)
            if j_score >= high_overlap_threshold:
                shared = sorted(fills_by_pilot[pa] & fills_by_pilot[pb])
                high_pairs.append(
                    {
                        "a": pa,
                        "b": pb,
                        "jaccard": j_score,
                        "shared_tickers": shared,
                    }
                )

    high_pairs.sort(key=lambda x: x["jaccard"], reverse=True)
    mean_jaccard = (
        round(sum(all_jaccards) / len(all_jaccards), 3) if all_jaccards else None
    )

    # 集中度ラベル
    if mean_jaccard is None:
        concentration_label = "n/a"
    elif mean_jaccard < 0.2:
        concentration_label = "独立性高"
    elif mean_jaccard < 0.4:
        concentration_label = "中程度"
    else:
        concentration_label = "重複多（4 機の差別化が機能していない）"

    return {
        "status": "active",
        "lookback_days": lookback_days,
        "fill_counts": fill_counts,
        "active_pilots": active_pilots,
        "matrix": matrix,
        "high_overlap_pairs": high_pairs,
        "high_overlap_threshold": high_overlap_threshold,
        "mean_jaccard": mean_jaccard,
        "concentration_label": concentration_label,
    }
