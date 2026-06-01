"""screening MCP ツール（SYSTEM_DESIGN.md §3.2 / AGENT_SPECS.md §1.5・§1.6）。

V字回復スコア・テーマスコア（各4軸）を計算し、composite_score = max(v字, テーマ) で
ランク付けして screening_results に保存する。

設計上の責務分担（SYSTEM_DESIGN §3.2）:「ツールは実行 + 結果保存。ロジックは別」。
universe 選定とデータ収集（fundamentals/technicals/news 等の集約）は screening-agent
（Task 1.4.2）の責務。本ツールは **収集済みの per-ticker データを受けて採点・保存** する。

スコア式は AGENT_SPECS の【たたき台】に一致。重みは settings 化を後続で検討。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session

from trading_agent.mcp_tools.base import (
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    ToolValidationError,
)
from trading_agent.models.signals import ScreeningResult
from trading_agent.utils.time_utils import utcnow

# 全銘柄が min_score 未満のときに返す件数（AGENT_SPECS §1.8）
_FALLBACK_TOP_N = 5


class ScreeningTickerData(MCPToolInput):
    """1銘柄ぶんの採点入力（agent が各ツールから集約して渡す）。欠損は None。"""

    ticker: str
    name: str = ""
    market: str = ""
    sector: str = ""
    current_price: float | None = None
    market_cap: float | None = None

    # --- V字回復スコア用 ---
    eps_latest_q: float | None = None
    eps_prev_prev_q: float | None = None
    eps_growth_latest_q: float | None = None
    eps_growth_prev_prev_q: float | None = None
    revenue_growth_latest_q: float | None = None
    min_price_90d: float | None = None
    max_price_90d: float | None = None
    rsi: float | None = None
    macd_cross_recent: bool = False
    volume_5d_avg: float | None = None
    volume_30d_avg: float | None = None

    # --- テーマスコア用 ---
    keyword_match_count: int | None = None
    sector_return_30d: float | None = None
    market_return_30d: float | None = None
    institutional_activity_score: float | None = None  # 0-20
    llm_theme_alignment_score: float | None = None  # 0-10


class ScreeningInput(MCPToolInput):
    tickers_data: list[ScreeningTickerData]
    strategies: list[str] = Field(default_factory=lambda: ["v_shape", "theme"])
    min_score: float = 50.0
    max_results: int = 30
    persist: bool = True


class ScreeningOutput(MCPToolOutput):
    results: list[dict[str, Any]] = Field(default_factory=list)
    total_screened: int = 0
    passed_count: int = 0


# v2.4 TASK-S3/S5/S6: screening の magic number を定数化（外部から見直し可能に）
# 根拠コメント: 各値は AGENT_SPECS §1.5/§1.6 で暫定設定。実証データ蓄積後に校正予定。
_V_PT_EARNINGS_TURNAROUND = 25  # 赤字 → 黒字（最強反転）
_V_PT_EARNINGS_ACCELERATION = 20  # 減益 → 大幅増益（点火）
_V_PT_REVENUE_GROWTH = 10  # 単なる増収（点火弱い）
_V_PT_PRICE_BOTTOM_WITH_IGNITION = 30  # 底打ち + 点火
_V_PT_PRICE_BOTTOM_WITHOUT_IGNITION = 10  # 底だが点火なし（value trap 警戒）
_V_PT_RSI_REVERSAL = 10  # RSI 反転圏
_V_PT_MACD_CROSS = 10  # MACD クロス
_V_PT_VOLUME_SURGE = 10  # 出来高サージ
_V_DRAWDOWN_MIN = 0.10  # 「底」判定の最小ドローダウン
_V_PRICE_BOTTOM_RATIO = 0.85  # max_price * 0.85 を下回ったら「底圏」
_V_RSI_RANGE = (30, 50)  # 反転圏の RSI レンジ
_V_VOLUME_SURGE_RATIO = 1.5  # 5d 平均が 30d 平均の 1.5x で出来高サージ
_V_REVENUE_GROWTH_MIN = 0.10  # 「増収」と認める下限
_V_EARNINGS_ACCEL_MIN = 0.20  # 「大幅増益」と認める下限

# theme_score 系
_T_PT_PER_KEYWORD = 5  # キーワード一致 1 件あたり
_T_PT_KEYWORD_CAP = 40  # キーワード加点の上限
_T_PT_SECTOR_OUTPERF_PER_PCT = 100  # セクター対市場 1% = 100pt（30pt cap）
_T_PT_SECTOR_CAP = 30
_T_PT_INSTITUTIONAL_CAP = 20  # 機関投資家フロー上限
_T_PT_LLM_ALIGNMENT_CAP = 10  # LLM テーマ一致上限


def calculate_v_shape_score(d: ScreeningTickerData) -> tuple[float, dict[str, Any]]:
    """V字回復スコア（AGENT_SPECS §1.5）。0-100 と詳細を返す。"""
    score = 0.0
    details: dict[str, Any] = {}

    # 1. 業績反転 (最大40pt、排他)。ignition＝「反転の点火」（強い反転のみ。単なる増収は点火でない）
    ignition = False
    if (
        d.eps_latest_q is not None
        and d.eps_prev_prev_q is not None
        and d.eps_latest_q > 0 > d.eps_prev_prev_q
    ):
        score += _V_PT_EARNINGS_TURNAROUND
        ignition = True
        details["earnings_turnaround"] = "赤字→黒字"
    elif (
        d.eps_growth_latest_q is not None
        and d.eps_growth_prev_prev_q is not None
        and d.eps_growth_latest_q > _V_EARNINGS_ACCEL_MIN
        and d.eps_growth_prev_prev_q < 0
    ):
        score += _V_PT_EARNINGS_ACCELERATION
        ignition = True
        details["earnings_turnaround"] = "減益→大幅増益（点火）"
    elif d.revenue_growth_latest_q is not None and d.revenue_growth_latest_q > _V_REVENUE_GROWTH_MIN:
        score += _V_PT_REVENUE_GROWTH
        details["earnings_turnaround"] = "増収（点火は弱い）"

    # 2. 株価底打ち。**Value×Momentum両立(Asness)**：点火がある時のみ満額。
    if d.current_price is not None and d.min_price_90d and d.max_price_90d:
        drawdown = (d.current_price - d.min_price_90d) / d.min_price_90d
        if drawdown > _V_DRAWDOWN_MIN and d.current_price < d.max_price_90d * _V_PRICE_BOTTOM_RATIO:
            if ignition:
                score += _V_PT_PRICE_BOTTOM_WITH_IGNITION
                details["price_bottom"] = True
            else:
                score += _V_PT_PRICE_BOTTOM_WITHOUT_IGNITION
                details["price_bottom"] = "底だが点火なし"
                details["value_trap"] = True

    # 3. テクニカル (20pt)
    if d.rsi is not None and _V_RSI_RANGE[0] < d.rsi < _V_RSI_RANGE[1]:
        score += _V_PT_RSI_REVERSAL
        details["rsi_reversal"] = d.rsi
    if d.macd_cross_recent:
        score += _V_PT_MACD_CROSS
        details["macd_cross"] = True

    # 4. 出来高 (10pt)
    if (
        d.volume_5d_avg is not None
        and d.volume_30d_avg
        and d.volume_5d_avg > d.volume_30d_avg * _V_VOLUME_SURGE_RATIO
    ):
        score += _V_PT_VOLUME_SURGE
        details["volume_surge"] = True

    return _clamp(score), details


def calculate_theme_score(d: ScreeningTickerData) -> tuple[float, dict[str, Any]]:
    """テーマスコア（AGENT_SPECS §1.6・v2.4 TASK-S6 で定数化）。0-100 と詳細を返す。"""
    score = 0.0
    details: dict[str, Any] = {}

    # 1. テーマキーワード一致
    if d.keyword_match_count is not None:
        pts = float(min(d.keyword_match_count * _T_PT_PER_KEYWORD, _T_PT_KEYWORD_CAP))
        score += pts
        details["keyword_matches"] = d.keyword_match_count

    # 2. セクター強度 — アンダーパフォームでマイナスにはしない
    if d.sector_return_30d is not None and d.market_return_30d is not None:
        outperformance = d.sector_return_30d - d.market_return_30d
        pts = max(0.0, min(outperformance * _T_PT_SECTOR_OUTPERF_PER_PCT, _T_PT_SECTOR_CAP))
        score += pts
        details["sector_outperformance"] = outperformance

    # 3. 機関投資家の動き
    if d.institutional_activity_score is not None:
        score += max(0.0, min(d.institutional_activity_score, _T_PT_INSTITUTIONAL_CAP))
        details["institutional"] = d.institutional_activity_score

    # 4. LLM 評価
    if d.llm_theme_alignment_score is not None:
        score += max(0.0, min(d.llm_theme_alignment_score, _T_PT_LLM_ALIGNMENT_CAP))
        details["llm_alignment"] = d.llm_theme_alignment_score

    return _clamp(score), details


def _clamp(score: float) -> float:
    return max(0.0, min(score, 100.0))


class ScreeningTool(MCPTool[ScreeningInput]):
    """V字 / テーマスコアの採点・保存ツール。"""

    name = "screening"
    description = "収集済みデータから V字 / テーマスコアを算出し、上位候補を返す。"
    input_schema = ScreeningInput
    output_schema = ScreeningOutput

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine

    async def _execute(self, tool_input: ScreeningInput) -> MCPToolOutput:
        if not tool_input.tickers_data:
            raise ToolValidationError("tickers_data が空です")

        screened_at = utcnow()
        scored: list[dict[str, Any]] = []
        for d in tool_input.tickers_data:
            v_score, v_details = (
                calculate_v_shape_score(d) if "v_shape" in tool_input.strategies else (0.0, {})
            )
            t_score, t_details = (
                calculate_theme_score(d) if "theme" in tool_input.strategies else (0.0, {})
            )
            composite = max(v_score, t_score)
            # 浮動小数点の境界値（例: composite=49.9999... が min_score=50.0 を下回って判定漏れする）
            # を防ぐため、微小な許容誤差を入れる。境界値（A=B）は通すという意図を明示。
            # v2.5 TASK-Z10: min_score=50.0 はソフトな最低限（本来は分布の上位 N% で動的決定推奨）
            eps = 1e-9
            threshold = tool_input.min_score - eps
            matched = []
            if "v_shape" in tool_input.strategies and v_score >= threshold:
                matched.append("v_shape")
            if "theme" in tool_input.strategies and t_score >= threshold:
                matched.append("theme")
            scored.append(
                {
                    "ticker": d.ticker,
                    "name": d.name,
                    "market": d.market,
                    "sector": d.sector,
                    "current_price": d.current_price,
                    "market_cap": d.market_cap,
                    "v_shape_score": v_score,
                    "theme_score": t_score,
                    "composite_score": composite,
                    "matched_strategies": matched,
                    "v_shape_details": v_details,
                    "theme_details": t_details,
                    "screening_passed": composite >= threshold,
                }
            )

        # 同点は時価総額の大きい順（AGENT_SPECS §1.8）
        scored.sort(key=lambda r: (r["composite_score"], r["market_cap"] or 0.0), reverse=True)

        passed = [r for r in scored if r["screening_passed"]]
        if passed:
            results = passed[: tool_input.max_results]
        else:
            results = scored[:_FALLBACK_TOP_N]  # 全銘柄低スコア → 上位5件

        if tool_input.persist and self._engine is not None:
            self._persist(scored, screened_at)

        return ScreeningOutput(
            success=True,
            results=results,
            total_screened=len(scored),
            passed_count=len(passed),
        )

    def _persist(self, scored: list[dict[str, Any]], screened_at: Any) -> None:
        with Session(self._engine) as session:
            for r in scored:
                session.add(
                    ScreeningResult(
                        ticker=r["ticker"],
                        screened_at=screened_at,
                        v_shape_score=r["v_shape_score"],
                        theme_score=r["theme_score"],
                        composite_score=r["composite_score"],
                        screening_passed=r["screening_passed"],
                        v_shape_details=r["v_shape_details"],
                        theme_details=r["theme_details"],
                    )
                )
            session.commit()
