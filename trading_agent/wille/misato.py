"""🎖 MISATO（葛城ミサト）— WILLE 組織内の作戦指示担当（v2.8 再設計）。

責務（再定義）:
  - **戦略パラメータの保持**（MisatoStrategy）
    どのカテゴリスコアをどれだけ重視するか（戦略プリセット）
  - **優先度計算**:
    DS 自発 proposal の base_confidence × 1.0 + RITSUKO Brief の boost × 0.5
  - **重み付け** で 5 中立スコアを 1 つの boost にまとめる
  - **配分調停**（既存の portfolio/misato.py が担当）

責任分界:
  - RITSUKO = 客観的事実 + 中立スコア 5 個を提供
  - MISATO  = 戦略パラメータで重み付け → 優先度 → 配分

旧 strategy_orders（銘柄→1機 指名）は廃止扱い（後方互換で関数は残す）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from trading_agent.utils.logger import get_logger
from trading_agent.wille.ritsuko import (
    SITUATION_PILOT_AFFINITY,
    MarketContext,
    SituationReport,
    TickerBrief,
)

_log = get_logger("wille.misato")


# ============================================================
# 戦略パラメータ
# ============================================================

StrategyPreset = Literal["balanced", "news_focused", "trend_focused", "peer_focused"]


@dataclass(frozen=True)
class MisatoStrategy:
    """MISATO が保持する戦略パラメータ。

    RITSUKO の 5 中立スコアをどう重み付けして boost にまとめるか。
    プリセット切替で戦略意思を表現する。

    重み構造:
      news     : 0.0-1.0  個別ニュースの重み（即時性 = 短期）
      industry : 0.0-1.0  業界トレンドの重み（中期）
      peer     : 0.0-1.0  ピア比較の重み（相対競争力）
      event    : 0.0-1.0  イベント要因（決算前 等）
      deep     : 0.0-1.0  Sonnet 深掘り（節目時のみ）

    boost_factor: 最終的に proposal.base_confidence に加算する時の倍率。
                  0.5 だと boost が proposal 基本値の 1/2 影響度になる。
    """

    preset: StrategyPreset = "balanced"
    weight_news: float = 0.30
    weight_industry: float = 0.20
    weight_peer: float = 0.30
    weight_event: float = 1.00
    weight_deep: float = 0.40
    boost_factor: float = 0.5

    @classmethod
    def from_preset(cls, preset: StrategyPreset) -> "MisatoStrategy":
        """プリセット名から戦略を生成。"""
        presets = {
            "balanced": dict(
                weight_news=0.30, weight_industry=0.20,
                weight_peer=0.30, weight_event=1.00, weight_deep=0.40,
            ),
            "news_focused": dict(
                weight_news=0.50, weight_industry=0.15,
                weight_peer=0.20, weight_event=1.00, weight_deep=0.40,
            ),
            "trend_focused": dict(
                weight_news=0.20, weight_industry=0.50,
                weight_peer=0.20, weight_event=1.00, weight_deep=0.40,
            ),
            "peer_focused": dict(
                weight_news=0.20, weight_industry=0.15,
                weight_peer=0.50, weight_event=1.00, weight_deep=0.40,
            ),
        }
        params = presets.get(preset, presets["balanced"])
        return cls(preset=preset, **params)

    def as_dict(self) -> dict[str, Any]:
        """UI 表示・ログ用にシリアライズ。"""
        return {
            "preset": self.preset,
            "weights": {
                "news": self.weight_news,
                "industry": self.weight_industry,
                "peer": self.weight_peer,
                "event": self.weight_event,
                "deep": self.weight_deep,
            },
            "boost_factor": self.boost_factor,
        }


# ============================================================
# Boost 計算（RITSUKO Brief から）
# ============================================================


def compute_boost_from_brief(brief: TickerBrief, strategy: MisatoStrategy) -> float:
    """RITSUKO Brief の 5 中立スコアを戦略パラメータで重み付けして boost に集約。

    Returns:
        ritsuko_boost: 概ね -1.0 ~ +1.2 の範囲

    内訳:
        news_sentiment_score * weight_news +
        industry_score       * weight_industry +
        peer_score           * weight_peer +
        event_score          * weight_event +
        deep_brief_score     * weight_deep
    """
    return (
        brief.news_sentiment_score * strategy.weight_news
        + brief.industry_score * strategy.weight_industry
        + brief.peer_score * strategy.weight_peer
        + brief.event_score * strategy.weight_event
        + brief.deep_brief_score * strategy.weight_deep
    )


def compute_priority(
    base_confidence: float,
    brief: TickerBrief | None,
    strategy: MisatoStrategy,
) -> tuple[float, dict[str, float]]:
    """DS proposal の優先度を計算（boost 加算）。

    Returns:
        priority_score: 最終優先度（高いほど採用優先）
        breakdown: 計算内訳（UI 表示・デバッグ用）
            {
                "base_confidence": 0.70,
                "ritsuko_boost": +0.31,
                "boost_weighted": +0.16,
                "priority": 0.86,
                "news_score": +0.20,
                "industry_score": +0.60,
                ...
            }
    """
    if brief is None:
        return base_confidence, {
            "base_confidence": base_confidence,
            "ritsuko_boost": 0.0,
            "boost_weighted": 0.0,
            "priority": base_confidence,
        }

    boost = compute_boost_from_brief(brief, strategy)
    boost_weighted = boost * strategy.boost_factor
    priority = base_confidence + boost_weighted

    return priority, {
        "base_confidence": base_confidence,
        "ritsuko_boost": boost,
        "boost_weighted": boost_weighted,
        "priority": priority,
        "news_score": brief.news_sentiment_score,
        "industry_score": brief.industry_score,
        "peer_score": brief.peer_score,
        "event_score": brief.event_score,
        "deep_brief_score": brief.deep_brief_score,
    }


# ============================================================
# 例外判定（Brief の生データを参照する数値ルール）
# ============================================================


@dataclass(frozen=True)
class ExceptionVerdict:
    """例外判定の結果。"""

    blocked: bool
    reason: str = ""


def check_proposal_exceptions(
    ticker: str,
    brief: TickerBrief | None,
    *,
    block_earnings_within_days: int = 7,
    block_peer_worst: bool = True,
) -> ExceptionVerdict:
    """proposal 採用前の例外チェック（決定論）。

    Brief の生データを使って、優先度計算とは別軸でブロックすべき条件を判定。
    """
    if brief is None:
        return ExceptionVerdict(blocked=False)

    # 直近決算ブロック
    if brief.upcoming_events:
        from datetime import date, timedelta

        cutoff = date.today() + timedelta(days=block_earnings_within_days)
        for ev in brief.upcoming_events:
            if ev.type == "earnings":
                try:
                    ev_date = date.fromisoformat(ev.date[:10])
                except (ValueError, TypeError):
                    continue
                if date.today() <= ev_date <= cutoff:
                    return ExceptionVerdict(
                        blocked=True,
                        reason=f"{ev_date} 決算前 {block_earnings_within_days} 日以内 → 見送り",
                    )

    # ピア劣位ブロック（数値が解析できる場合のみ。"—/—" 等のプレースホルダはスキップ）
    if block_peer_worst and brief.peer.sector_rank and "/" in brief.peer.sector_rank:
        try:
            pos_s, total_s = brief.peer.sector_rank.split("/")
            pos_s, total_s = pos_s.strip(), total_s.strip()
            if pos_s.isdigit() and total_s.isdigit() and int(pos_s) == int(total_s) > 1:
                return ExceptionVerdict(
                    blocked=True,
                    reason=f"業界内最下位（{brief.peer.sector_rank}）→ 見送り",
                )
        except ValueError:
            pass

    # Sonnet 強い売り判定
    if brief.deep_brief and brief.deep_brief.recommendation_score < -0.5:
        return ExceptionVerdict(
            blocked=True,
            reason=f"Sonnet 強い売り判定（score={brief.deep_brief.recommendation_score:+.2f}）",
        )

    return ExceptionVerdict(blocked=False)


# ============================================================
# 後方互換: 旧 StrategyOrder / determine_assignment
# （ハードコード指名は廃止扱い・既存呼び出しのため残す）
# ============================================================


@dataclass(frozen=True)
class StrategyOrder:
    """[DEPRECATED] 旧 MISATO の銘柄→1機指名。

    新設計では DS の自発 proposal + MISATO 優先度補正が正解。
    既存テスト・既存 build_snapshot 互換のため残置。
    """

    ticker: str
    assigned_to: str
    situation: str
    rationale: str
    confidence: float
    priority: int = 0


_PILOT_PROFILE: dict[str, dict[str, Any]] = {
    "REI": {"label": "🔵 REI", "horizon_days": 180, "stop_pct": 0.15},
    "ASUKA": {"label": "🔴 ASUKA", "horizon_days": 60, "stop_pct": 0.08},
    "SHINJI": {"label": "🟣 SHINJI", "horizon_days": 90, "stop_pct": 0.10},
    "KAWORU": {"label": "🌒 KAWORU", "horizon_days": 21, "stop_pct": 0.05},
}


def determine_assignment(
    situation_report: SituationReport,
    *,
    market_ctx: MarketContext | None = None,
    consensus_tickers: set[str] | None = None,
) -> StrategyOrder | None:
    """[DEPRECATED] 旧ロジック。新設計では使われない（後方互換のため残置）。"""
    ticker = situation_report.ticker
    if consensus_tickers and ticker in consensus_tickers:
        return StrategyOrder(
            ticker=ticker,
            assigned_to="KAWORU",
            situation=situation_report.situation,
            rationale="3 機合議銘柄 → KAWORU",
            confidence=0.9,
            priority=10,
        )
    if situation_report.situation == "unknown":
        return None
    candidates = SITUATION_PILOT_AFFINITY.get(situation_report.situation, [])
    if not candidates:
        return None
    if market_ctx is not None:
        if market_ctx.regime == "risk_off":
            defensive = [p for p in candidates if p in ("REI", "SHINJI")]
            if defensive:
                candidates = defensive
        elif market_ctx.regime == "risk_on":
            offensive = [p for p in candidates if p in ("ASUKA", "KAWORU")]
            if offensive:
                candidates = offensive
    assigned = candidates[0]
    profile = _PILOT_PROFILE.get(assigned, {})
    rationale = (
        f"状況={situation_report.situation} → {profile.get('label', assigned)} "
        f"(horizon={profile.get('horizon_days')}日)"
    )
    return StrategyOrder(
        ticker=ticker,
        assigned_to=assigned,
        situation=situation_report.situation,
        rationale=rationale,
        confidence=situation_report.confidence,
        priority=int(situation_report.confidence * 10),
    )


def build_strategy_orders(
    situation_reports: dict[str, SituationReport],
    *,
    market_ctx: MarketContext | None = None,
    consensus_tickers: set[str] | None = None,
) -> list[StrategyOrder]:
    """[DEPRECATED] 旧 API。後方互換のため残置。"""
    orders: list[StrategyOrder] = []
    for _ticker, report in situation_reports.items():
        order = determine_assignment(
            report, market_ctx=market_ctx, consensus_tickers=consensus_tickers
        )
        if order is not None:
            orders.append(order)
    orders.sort(key=lambda o: (-o.priority, -o.confidence, o.ticker))
    return orders
