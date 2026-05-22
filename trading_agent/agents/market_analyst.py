"""market-analyst エージェント（AGENT_SPECS.md §2）。

screening 候補を個別に深掘りし、5軸スコア + 3シナリオ + thesis_checklist で買いシグナルを生成。

- fundamental_score / technical_score：純粋関数（§2.5）。取得できたフィールドのみ採点。
- strategy_fit_score：screening_results.composite_score を採用。
- news_sentiment_score：Phase 1 は中立 50（LLM センチメントは後日。§2.8 ニュース無し=50 と整合）。
- ai_confidence / scenarios / thesis_checklist / reasons / risks：llm_call（Hot）の JSON。
  LLM 未登録/失敗時は graceful default（確信度 50・空シナリオ）。
- 推奨数量・指値：§2.7 のルール。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.agents.serialization import save_buy_signals
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, is_jp_ticker
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.mcp_tools.market_data import MarketDataInput
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.models.settings import Setting
from trading_agent.models.signals import BuySignal, ScreeningResult
from trading_agent.utils.logger import get_logger

# 総合スコアの重み（【たたき台】。settings 化は後日）
_WEIGHTS = {
    "fundamental": 0.30,
    "technical": 0.25,
    "news": 0.15,
    "strategy_fit": 0.20,
    "ai_confidence": 0.10,
}
_TARGET_DAYS = {"中期": 90, "中期-長期": 180, "長期": 365, "短期": 14}
_STOP_LOSS = {"中期": -0.08, "中期-長期": -0.09, "長期": -0.10, "短期": -0.05}


class MarketAnalystInput(AgentInput):
    tickers: list[str]
    parallel: bool = True
    deep_dive: bool = False


class MarketAnalystOutput(AgentOutput):
    signals: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


def calculate_fundamental_score(
    *,
    per: float | None = None,
    sector_avg_per: float | None = None,
    eps_growth_yoy: float | None = None,
    revenue_growth: float | None = None,
    roe: float | None = None,
    de_ratio: float | None = None,
    current_ratio: float | None = None,
) -> float:
    """ファンダ・スコア（§2.5）。取得できたフィールドのみ加点。"""
    score = 0.0
    if per is not None and sector_avg_per:
        if per < sector_avg_per * 0.8:
            score += 25
        elif per < sector_avg_per:
            score += 15
        elif per < sector_avg_per * 1.5:
            score += 5
    if eps_growth_yoy is not None:
        if eps_growth_yoy > 0.3:
            score += 30
        elif eps_growth_yoy > 0.15:
            score += 20
        elif eps_growth_yoy > 0.05:
            score += 10
    if revenue_growth is not None:
        if revenue_growth > 0.2:
            score += 20
        elif revenue_growth > 0.1:
            score += 10
    if roe is not None:
        if roe > 0.15:
            score += 15
        elif roe > 0.08:
            score += 8
    if de_ratio is not None and current_ratio is not None:
        if de_ratio < 0.5 and current_ratio > 1.5:
            score += 10
        elif de_ratio < 1.0:
            score += 5
    return min(score, 100.0)


def calculate_technical_score(
    *,
    price: float | None = None,
    sma_20: float | None = None,
    sma_60: float | None = None,
    sma_200: float | None = None,
    rsi: float | None = None,
    macd_cross_recent: bool = False,
    macd_value: float | None = None,
    volume_5d_avg: float | None = None,
    volume_30d_avg: float | None = None,
) -> float:
    """テクニカル・スコア（§2.5）。"""
    score = 0.0
    if price is not None and sma_20 is not None:
        if sma_60 is not None and sma_200 is not None and price > sma_20 > sma_60 > sma_200:
            score += 40
        elif sma_60 is not None and price > sma_20 > sma_60:
            score += 25
        elif price > sma_20:
            score += 10
    if rsi is not None:
        if 40 < rsi < 60:
            score += 20
        elif 30 < rsi < 70:
            score += 10
    if macd_cross_recent and (macd_value is None or macd_value > 0):
        score += 20
    if volume_5d_avg is not None and volume_30d_avg:
        if volume_5d_avg > volume_30d_avg * 1.2:
            score += 20
    return min(score, 100.0)


def overall_score(
    fundamental: float, technical: float, news: float, strategy_fit: float, ai_confidence: float
) -> int:
    return round(
        fundamental * _WEIGHTS["fundamental"]
        + technical * _WEIGHTS["technical"]
        + news * _WEIGHTS["news"]
        + strategy_fit * _WEIGHTS["strategy_fit"]
        + ai_confidence * _WEIGHTS["ai_confidence"]
    )


def determine_recommendation(
    *,
    score: int,
    market: str,
    current_price: float,
    strategy_category: str,
    stop_loss_pct: float,
    is_v_shape: bool,
    cash_jpy: float,
    total_assets_jpy: float,
    max_cash_pct: float,
    max_total_pct: float,
    usd_jpy: float,
) -> dict[str, float]:
    """推奨数量・指値（§2.7）。価格は元通貨、金額は JPY。"""
    max_amount = min(cash_jpy * max_cash_pct, total_assets_jpy * max_total_pct)
    factor = (
        1.0
        if score >= 90
        else 0.8 if score >= 80 else 0.6 if score >= 70 else 0.4 if score >= 65 else 0.2
    )
    amount_jpy = max_amount * factor

    rate = usd_jpy if market == "US" else 1.0
    price_jpy = current_price * rate
    if market == "US":
        qty = max(1, int(amount_jpy / price_jpy)) if price_jpy else 0
    else:
        qty = max(100, int(amount_jpy / price_jpy / 100) * 100) if price_jpy else 0

    if strategy_category == "中期" and is_v_shape:
        entry_price = current_price * (1 - 0.015)
    elif strategy_category == "中期":
        entry_price = current_price * (1 - 0.005)
    else:
        entry_price = current_price
    stop_loss_price = entry_price * (1 + stop_loss_pct)

    return {
        "qty": float(qty),
        "entry_price": round(entry_price, 4),
        "stop_loss_price": round(stop_loss_price, 4),
        "recommended_amount_jpy": float(int(qty * entry_price * rate)),
    }


class MarketAnalystAgent(Agent[MarketAnalystInput]):
    """候補銘柄の深掘り分析エージェント。"""

    name = "market_analyst"
    description = "候補を5軸スコア + 3シナリオで深掘りし、買いシグナルを生成する。"
    required_tools = ["market_data", "fundamentals", "technicals", "news", "llm_call"]
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: MarketAnalystInput) -> AgentOutput:
        ctx = self._load_portfolio_context(self._ctx.engine)
        signals: list[BuySignal] = []
        signal_views: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for ticker in agent_input.tickers:
            try:
                sig = await self._analyze(ticker, agent_input, ctx)
            except Exception as exc:  # 1銘柄の失敗で全体を止めない
                self._log.warning("market_analyst_skip", ticker=ticker, error=str(exc))
                skipped.append({"ticker": ticker, "reason": str(exc)})
                continue
            if sig is None:
                skipped.append({"ticker": ticker, "reason": "価格データ不足"})
                continue
            signals.append(sig)
            signal_views.append({"ticker": sig.ticker, "score": sig.score})

        if not agent_input.dry_run and signals:
            save_buy_signals(self._ctx.engine, signals)

        return MarketAnalystOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=f"{len(signals)} 件の買いシグナル生成（{len(skipped)} 件スキップ）",
            signals=signal_views,
            skipped=skipped,
        )

    async def _analyze(
        self, ticker: str, agent_input: MarketAnalystInput, pctx: dict[str, float]
    ) -> BuySignal | None:
        price = await self._current_price(ticker)
        if price is None:
            return None

        fund = await self._fundamentals(ticker)
        tech = await self._technicals(ticker)
        screen = self._screening_result(self._ctx.engine, ticker)

        fundamental_score = calculate_fundamental_score(
            per=fund.get("per"), revenue_growth=fund.get("revenue_growth"), roe=fund.get("roe")
        )
        technical_score = calculate_technical_score(
            price=price,
            rsi=tech.get("rsi"),
            macd_cross_recent=bool(tech.get("macd_cross_recent")),
        )
        news_sentiment = 50.0
        strategy_fit = screen.get("composite_score", 0.0)
        is_v_shape = screen.get("v_shape_score", 0.0) >= screen.get("theme_score", 0.0)

        judgment = await self._llm_judgment(ticker, agent_input.deep_dive)
        ai_confidence = float(judgment.get("ai_confidence", 50.0))

        score = overall_score(
            fundamental_score, technical_score, news_sentiment, strategy_fit, ai_confidence
        )

        strategy_category = "中期"
        stop_loss_pct = _STOP_LOSS.get(strategy_category, -0.08)
        rec = determine_recommendation(
            score=score,
            market="JP" if is_jp_ticker(ticker) else "US",
            current_price=price,
            strategy_category=strategy_category,
            stop_loss_pct=stop_loss_pct,
            is_v_shape=is_v_shape,
            cash_jpy=pctx["cash_jpy"],
            total_assets_jpy=pctx["total_assets_jpy"],
            max_cash_pct=pctx["max_cash_pct"],
            max_total_pct=pctx["max_total_pct"],
            usd_jpy=pctx["usd_jpy"],
        )

        scenarios = _normalize_scenarios(judgment.get("scenarios", []))
        base = next((s for s in scenarios if s.get("type") == "base"), None)
        target_price = float(base["target_price"]) if base and "target_price" in base else price
        expected_return = float(base.get("return_pct", 0.0)) if base else 0.0
        win_rate = float(base.get("prob", 0.5)) if base else 0.5

        return BuySignal(
            ticker=ticker,
            score=score,
            fundamental_score=fundamental_score,
            technical_score=technical_score,
            news_sentiment_score=news_sentiment,
            strategy_fit_score=strategy_fit,
            ai_confidence=ai_confidence,
            expected_return=expected_return,
            win_rate=win_rate,
            target_period_days=_TARGET_DAYS.get(strategy_category, 90),
            target_price=target_price,
            entry_price=rec["entry_price"],
            stop_loss_price=rec["stop_loss_price"],
            strategy_category=strategy_category,
            thesis_checklist=list(judgment.get("thesis_checklist", [])),
            reasons=list(judgment.get("reasons", [])),
            risks=list(judgment.get("risks", [])),
            scenarios=scenarios,
            recommended_amount_jpy=int(rec["recommended_amount_jpy"]),
        )

    # --- データ取得ヘルパー（取得失敗時は空/None で graceful） -----------------

    async def _current_price(self, ticker: str) -> float | None:
        try:
            out = await self._ctx.call_tool("market_data", MarketDataInput(tickers=[ticker]))
            payload = getattr(out, "data", None)
            if out.success and payload:
                value = payload.get(ticker, {}).get("current_price")
                return float(value) if isinstance(value, int | float) else None
        except Exception as exc:
            self._log.warning("market_analyst_price_failed", ticker=ticker, error=str(exc))
        return None

    async def _fundamentals(self, ticker: str) -> dict[str, Any]:
        try:
            out = await self._ctx.call_tool(
                "fundamentals",
                FundamentalsInput(ticker=ticker, fields=["per", "revenue_growth", "roe"]),
            )
            payload = getattr(out, "data", None)
            if out.success and payload:
                return dict(payload)
        except Exception as exc:
            self._log.warning("market_analyst_fund_failed", ticker=ticker, error=str(exc))
        return {}

    async def _technicals(self, ticker: str) -> dict[str, Any]:
        try:
            out = await self._ctx.call_tool("technicals", TechnicalsInput(ticker=ticker))
            payload = dict(getattr(out, "data", None) or {})
            signals = getattr(out, "signals", []) or []
            payload["macd_cross_recent"] = "golden_cross" in signals or "macd_bullish" in signals
            if out.success:
                return payload
        except Exception as exc:
            self._log.warning("market_analyst_tech_failed", ticker=ticker, error=str(exc))
        return {}

    def _screening_result(self, engine: Engine, ticker: str) -> dict[str, Any]:
        with Session(engine) as session:
            row = session.exec(
                select(ScreeningResult)
                .where(col(ScreeningResult.ticker) == ticker)
                .order_by(col(ScreeningResult.screened_at).desc())
            ).first()
        if row is None:
            return {}
        return {
            "composite_score": row.composite_score,
            "v_shape_score": row.v_shape_score,
            "theme_score": row.theme_score,
        }

    async def _llm_judgment(self, ticker: str, deep_dive: bool) -> dict[str, Any]:
        prompt = (
            f"銘柄 {ticker} の中期投資判断を JSON で返してください。"
            "キー: thesis_checklist[], reasons[], risks[], scenarios[]（bull/base/bear, "
            "target_price/return_pct/prob/desc）, ai_confidence(0-100)。"
        )
        try:
            out = await self._ctx.call_tool(
                "llm_call",
                LLMCallInput(
                    prompt=prompt,
                    purpose="deep_dive" if deep_dive else "analysis",
                    routing_hint="critical" if deep_dive else "hot",
                    agent=self.name,
                    invocation_id=self._ctx.invocation_id,
                ),
            )
            if out.success:
                return dict(json.loads(getattr(out, "response", "") or "{}"))
        except Exception as exc:
            self._log.warning("market_analyst_llm_failed", ticker=ticker, error=str(exc))
        return {}

    def _load_portfolio_context(self, engine: Engine) -> dict[str, float]:
        with Session(engine) as session:
            snap = session.exec(
                select(PortfolioSnapshot).order_by(col(PortfolioSnapshot.date).desc())
            ).first()
            cash = snap.cash_jpy if snap else 100000.0
            total = snap.total_assets_jpy if snap else 100000.0
            usd_jpy = snap.usd_jpy_rate if snap else 150.0
            max_cash = _float_setting(session, "max_position_pct_of_cash", 0.20)
            max_total = _float_setting(session, "max_position_pct_of_total", 0.10)
        return {
            "cash_jpy": cash,
            "total_assets_jpy": total,
            "usd_jpy": usd_jpy,
            "max_cash_pct": max_cash,
            "max_total_pct": max_total,
        }


def _float_setting(session: Session, key: str, default: float) -> float:
    row = session.get(Setting, key)
    if row is None:
        return default
    try:
        return float(json.loads(row.value))
    except (ValueError, TypeError):
        return default


def _normalize_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """確率の合計を 1.0 に正規化する（§2.8）。"""
    if not scenarios:
        return []
    total = sum(float(s.get("prob", 0.0)) for s in scenarios)
    if total <= 0:
        return scenarios
    for s in scenarios:
        s["prob"] = round(float(s.get("prob", 0.0)) / total, 3)
    return scenarios
