"""市場 posture 計算（Track A：マクロ×ミクロ配線・record-only）.

朝バッチで **1 回** 市場全体の地合いを集約し、exposure_coach に通して
「今日新規エントリーを許容するか」のラベル + 想定サイジング係数を出す。
**売買は変えない**（record-only）。entry 時点で Decision に固定し、後で「マクロが効いたか」を
shadow 計測する（null を超えたら将来 sizing/閾値の小幅調整へ昇格・codex 提言）。

入力源（全て entry 時点以前のデータ＝PIT 正）:
- regime_score : detect_market_cycle（TOPIX trailing 局面 bull/bear/sideways）を 0-100 に写像
- breadth_score: 活性 universe の 30 日リターンで「上昇銘柄比率」（exposure_coach 最大重み 0.30）
- uptrend_score: 同 returns で「+5%超の強い上昇参加率」
top_risk / institutional は未配線（None＝欠落として保守側に効く）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.discipline.exposure_coach import (
    ExposureDecision,
    ExposureInputs,
    decide_exposure,
)
from trading_agent.models.universe import Universe

ReturnsFetcher = Callable[[list[str]], dict[str, float]]

# breadth/uptrend の閾値（30 日リターン %）。
_UPTREND_STRONG_PCT = 5.0  # +5% 超を「上昇トレンド参加」とみなす

# regime 文字列 → 0-100 スコア（落ち着き度）。unknown は None（欠落扱い）。
_REGIME_SCORE = {"bull": 75.0, "sideways": 50.0, "bear": 25.0}

# exposure recommendation → 想定サイジング係数（**未適用**・record のみ）。
# NEW_ENTRY_ALLOWED=中立 / REDUCE_ONLY=3割縮小 / CASH_PRIORITY=6割縮小（将来の調整幅の目安）。
_MACRO_ADJUSTMENT = {
    "NEW_ENTRY_ALLOWED": 0.0,
    "REDUCE_ONLY": -0.3,
    "CASH_PRIORITY": -0.6,
}

_SAMPLE_CAP = 400  # breadth fetch の上限（yfinance 1 バッチ・大きすぎる universe を間引く）


def macro_adjustment_for(recommendation: str | None) -> float | None:
    """exposure recommendation から想定サイジング係数を引く（未適用・record 用）。"""
    if recommendation is None:
        return None
    return _MACRO_ADJUSTMENT.get(recommendation)


def _active_jp_universe_sample(engine: Engine, cap: int = _SAMPLE_CAP) -> list[str]:
    """活性 JP universe を breadth 用にサンプル（大きければ stride で間引く）。"""
    with Session(engine) as s:
        rows = list(
            s.exec(
                select(Universe)
                .where(col(Universe.is_active))
                .where(col(Universe.market) == "JP")
            )
        )
    tickers = [r.ticker for r in rows]
    if len(tickers) <= cap:
        return tickers
    stride = len(tickers) / cap
    return [tickers[min(int(i * stride), len(tickers) - 1)] for i in range(cap)]


def _default_regime() -> str:
    try:
        from trading_agent.wille.ritsuko import detect_market_cycle

        return str(detect_market_cycle().get("cycle") or "unknown")
    except Exception:
        return "unknown"


def compute_market_posture(
    engine: Engine,
    *,
    returns_fetcher: ReturnsFetcher | None = None,
    regime: str | None = None,
    portfolio_dd_pct: float | None = None,
) -> tuple[ExposureDecision, dict[str, Any]]:
    """市場 posture を 1 回集約して exposure_coach の判定を返す（決定論・LLM 非関与）。

    Returns: (ExposureDecision, meta)。meta は監査用（regime / sample_n / breadth / uptrend）。
    breadth fetch 失敗時は breadth/uptrend=None（exposure_coach は欠落を保守側に扱う・破綻しない）。
    """
    reg = regime if regime is not None else _default_regime()
    regime_score = _REGIME_SCORE.get(reg)

    if returns_fetcher is None:
        from trading_agent.wille.ritsuko import _fetch_30d_returns_bulk

        returns_fetcher = _fetch_30d_returns_bulk

    tickers = _active_jp_universe_sample(engine)
    returns = returns_fetcher(tickers) if tickers else {}

    breadth_score: float | None = None
    uptrend_score: float | None = None
    if returns:
        vals = list(returns.values())
        n = len(vals)
        breadth_score = 100.0 * sum(1 for v in vals if v > 0) / n
        uptrend_score = 100.0 * sum(1 for v in vals if v > _UPTREND_STRONG_PCT) / n

    decision = decide_exposure(
        ExposureInputs(
            breadth_score=breadth_score,
            uptrend_score=uptrend_score,
            regime_score=regime_score,
            portfolio_dd_pct=portfolio_dd_pct,
        )
    )
    meta = {
        "regime": reg,
        "sample_n": len(returns),
        "breadth_score": breadth_score,
        "uptrend_score": uptrend_score,
        "macro_adjustment": macro_adjustment_for(decision.recommendation),
    }
    return decision, meta


__all__ = [
    "compute_market_posture",
    "macro_adjustment_for",
]
