"""portfolio-level リスク指標（v2.10 Phase 2A）。

VaR (Value at Risk) / CVaR / 最大ドローダウン (DD) を計算し、
ポートフォリオ全体のリスクを定量化・アラート化する。

指標:
  - VaR (95%):    過去リターン分布の 5% 点。「95% の場合これより悪くない」損失額。
  - CVaR (95%):   VaR を超える損失の期待値（テールリスク）。
  - 最大 DD:     過去 lookback_days のピークからの最大下落率。
  - アラート段階: 15% / 20% / 25% （正常 / 警告 / 危険 / 致命的）

データソース:
  - 既存 portfolio_snapshots テーブル（_compute_portfolio_dd 既存ロジックを統合）

ハルシネーション対策:
  - サンプル数 < 20 なら status="insufficient_data"
  - NaN を含む分布は計算しない（推測しない）
  - VaR/CVaR の信頼区間は明示
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.risk_metrics")

# 最低必要サンプル数
_MIN_SAMPLES = 20

# DD アラート閾値（モノトニック増加）
DEFAULT_DD_THRESHOLDS: dict[str, float] = {
    "warning": 0.15,    # 15% 下落で警告
    "danger": 0.20,     # 20% で危険
    "critical": 0.25,   # 25% で致命的
}


def compute_max_drawdown(
    *, current_total: float, lookback_days: int, engine: Engine
) -> float | None:
    """過去 lookback_days のピークからの最大ドローダウンを計算。

    既存の build_snapshot._compute_portfolio_dd と同じロジック（v2.2 TASK-EX3）。

    Returns:
        DD（負値、例: -0.15 = -15%）。snapshot が無い時は None。
    """
    if current_total <= 0:
        return None
    cutoff_date = dt.date.today() - dt.timedelta(days=lookback_days)
    with Session(engine) as s:
        snaps = list(
            s.exec(
                select(PortfolioSnapshot).where(
                    col(PortfolioSnapshot.date) >= cutoff_date
                )
            ).all()
        )
    if not snaps:
        return None
    peak = max(snap.total_assets_jpy for snap in snaps)
    peak = max(peak, current_total)
    if peak <= 0:
        return None
    return (current_total - peak) / peak


def compute_var_cvar(
    returns: list[float], *, confidence: float = 0.95
) -> tuple[float | None, float | None]:
    """過去リターン分布から VaR / CVaR を計算。

    Args:
        returns: 日次リターン list（小数表記）
        confidence: 信頼区間（デフォルト 0.95 = 95%）

    Returns:
        (VaR, CVaR) のタプル。サンプル不足 / NaN 含む場合は (None, None)。
        共に負値で表現（例: -0.03 = -3%）。
    """
    if not returns or len(returns) < _MIN_SAMPLES:
        return None, None
    # NaN を含む場合は計算しない（推測しない）
    for r in returns:
        if r != r:  # NaN チェック
            return None, None
    sorted_rets = sorted(returns)
    # VaR = 分布の (1-confidence) 分位点
    idx = int((1.0 - confidence) * len(sorted_rets))
    var = sorted_rets[idx]
    # CVaR = VaR より悪い側のリターン平均
    tail = sorted_rets[: idx + 1]
    cvar = sum(tail) / len(tail) if tail else None
    return var, cvar


def compute_risk_metrics(
    engine: Engine,
    *,
    lookback_days: int = 60,
    current_total: float | None = None,
    dd_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """ポートフォリオ全体のリスク指標を計算する。

    Args:
        engine: DB エンジン
        lookback_days: 計算対象期間
        current_total: 現在の総資産（None なら最新 snapshot から取得）
        dd_thresholds: DD アラート閾値（None なら DEFAULT）

    Returns:
        VaR / CVaR / DD / アラートレベル を含む dict。
    """
    if dd_thresholds is None:
        dd_thresholds = dict(DEFAULT_DD_THRESHOLDS)

    cutoff = dt.date.today() - dt.timedelta(days=lookback_days)
    with Session(engine) as s:
        snaps = list(
            s.exec(
                select(PortfolioSnapshot)
                .where(col(PortfolioSnapshot.date) >= cutoff)
                .order_by(col(PortfolioSnapshot.date).asc())
            ).all()
        )

    if len(snaps) < _MIN_SAMPLES:
        return {
            "status": "insufficient_data",
            "lookback_days": lookback_days,
            "samples": len(snaps),
            "min_samples_required": _MIN_SAMPLES,
            "reason": f"only_{len(snaps)}_snapshots",
        }

    # 日次リターン
    totals = [snap.total_assets_jpy for snap in snaps]
    returns: list[float] = []
    for i in range(1, len(totals)):
        if totals[i - 1] > 0:
            returns.append((totals[i] - totals[i - 1]) / totals[i - 1])

    if current_total is None:
        current_total = totals[-1]

    # VaR / CVaR
    var, cvar = compute_var_cvar(returns, confidence=0.95)
    # 最大 DD
    dd = compute_max_drawdown(
        current_total=current_total, lookback_days=lookback_days, engine=engine
    )

    # DD アラートレベル
    dd_abs = abs(dd) if dd is not None else None
    alert_level = "正常"
    if dd_abs is not None:
        if dd_abs >= dd_thresholds["critical"]:
            alert_level = "致命的"
        elif dd_abs >= dd_thresholds["danger"]:
            alert_level = "危険"
        elif dd_abs >= dd_thresholds["warning"]:
            alert_level = "警告"

    return {
        "status": "active",
        "lookback_days": lookback_days,
        "samples": len(snaps),
        "current_total_jpy": int(current_total),
        "var_95": round(var, 4) if var is not None else None,
        "cvar_95": round(cvar, 4) if cvar is not None else None,
        "var_95_jpy": int(current_total * var) if var is not None else None,
        "cvar_95_jpy": int(current_total * cvar) if cvar is not None else None,
        "max_drawdown": round(dd, 4) if dd is not None else None,
        "max_drawdown_jpy": (
            int(current_total * dd / (1 + dd)) if dd is not None and dd > -1 else None
        ),
        "alert_level": alert_level,
        "dd_thresholds": dd_thresholds,
        "interpretation": (
            "VaR は「95% の確率でこれより悪くない」日次損失率。CVaR はテールリスク "
            "(VaR を超える損失の期待値)。サンプル小・分布の正規性仮定なし。"
        ),
    }
