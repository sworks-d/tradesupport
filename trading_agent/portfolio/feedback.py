"""判断精度に基づくフィードバックループ（v2.10 Phase 2 Mini Feedback）。

過去の判断精度（judgment_accuracy）から、次回 dispatch で使う機別の予算重みを計算する。
データ不足時 (evaluated < 5) は全機 1.0（中立）を返し、フィードバックは発動しない。

設計原則:
  - "学習する AI" の最小単位。集計 → 重み計算 → dispatch に注入 の完結ループ
  - データが薄い段階は安全側（全機中立）に倒し、勝手な調整をしない
  - サンプル 5 件以上の機にだけ調整がかかる（他は 1.0 維持）

multiplier の意味:
  - 1.2: 勝率 60%+ → 1 機上限を 20% 拡張（勝てる機に予算回す）
  - 1.0: 勝率 40-60% or データ不足 → 中立
  - 0.5: 勝率 40% 未満 → 1 機上限を半減（負け続ける機の傷を浅くする）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from trading_agent.reporting.judgment_accuracy import compute_judgment_accuracy
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.feedback")

# データ不足判定の最低サンプル（これ未満は multiplier=1.0 で発動しない）
_MIN_EVALUATED = 5


def _accuracy_to_multiplier(
    accuracy_pct: float | None, evaluated: int
) -> tuple[float, str]:
    """accuracy から multiplier と理由文字列を導出。"""
    if accuracy_pct is None or evaluated < _MIN_EVALUATED:
        return 1.0, "データ不足（中立）"
    if accuracy_pct >= 60.0:
        return 1.2, f"勝率 {accuracy_pct:.0f}% → 重み +20%"
    if accuracy_pct >= 40.0:
        return 1.0, f"勝率 {accuracy_pct:.0f}% → 中立"
    return 0.5, f"勝率 {accuracy_pct:.0f}% → 重み半減"


def compute_pilot_multipliers(
    engine: Engine, lookback_days: int = 30
) -> dict[str, Any]:
    """機別の予算重みを計算（judgment_accuracy ベース）。

    返り値:
      {
        "multipliers": {"REI": 1.0, "ASUKA": 1.2, ...},  # opportunity_fill に渡す用
        "details": {pilot: {multiplier, accuracy_pct, evaluated, reason}, ...},  # 透明性用
        "lookback_days": int,
        "status": "active" | "insufficient_data",
      }
    """
    ja = compute_judgment_accuracy(engine, lookback_days=lookback_days)
    multipliers: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for pilot in ("REI", "ASUKA", "SHINJI", "KAWORU"):
        sub = ja["by_pilot"].get(pilot, {})
        evaluated = int(sub.get("evaluated", 0) or 0)
        accuracy = sub.get("accuracy_pct")
        mul, reason = _accuracy_to_multiplier(accuracy, evaluated)
        multipliers[pilot] = mul
        details[pilot] = {
            "multiplier": mul,
            "accuracy_pct": accuracy,
            "evaluated": evaluated,
            "reason": reason,
        }

    # 1 機でも重みが 1.0 以外なら active、全部 1.0 なら insufficient_data
    has_signal = any(m != 1.0 for m in multipliers.values())
    status = "active" if has_signal else "insufficient_data"

    _log.info(
        "pilot_multipliers_computed",
        multipliers=multipliers,
        status=status,
        lookback_days=lookback_days,
    )

    return {
        "multipliers": multipliers,
        "details": details,
        "lookback_days": lookback_days,
        "min_evaluated_required": _MIN_EVALUATED,
        "status": status,
    }
