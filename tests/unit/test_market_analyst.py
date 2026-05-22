"""market-analyst の単体テスト（Task 1.4.3）。スコアは固定値検証、エージェントはモック注入。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.market_analyst import (
    MarketAnalystAgent,
    MarketAnalystInput,
    calculate_fundamental_score,
    calculate_technical_score,
    determine_recommendation,
    overall_score,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, FundamentalsOutput
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataOutput
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsOutput
from trading_agent.models.portfolio import PortfolioSnapshot
from trading_agent.models.signals import BuySignal, ScreeningResult


class TestScores:
    def test_fundamental(self) -> None:
        s = calculate_fundamental_score(per=10, sector_avg_per=20, revenue_growth=0.25, roe=0.16)
        assert s == 25 + 20 + 15  # 割安25 + 増収20 + ROE15

    def test_technical_full(self) -> None:
        s = calculate_technical_score(
            price=110,
            sma_20=105,
            sma_60=100,
            sma_200=95,
            rsi=50,
            macd_cross_recent=True,
            macd_value=1.0,
            volume_5d_avg=130,
            volume_30d_avg=100,
        )
        assert s == 100.0

    def test_overall_weighted(self) -> None:
        assert overall_score(100, 100, 100, 100, 100) == 100

    def test_recommendation(self) -> None:
        rec = determine_recommendation(
            score=85,
            market="US",
            current_price=100.0,
            strategy_category="中期",
            stop_loss_pct=-0.08,
            is_v_shape=True,
            cash_jpy=100000,
            total_assets_jpy=100000,
            max_cash_pct=0.2,
            max_total_pct=0.1,
            usd_jpy=150.0,
        )
        assert rec["entry_price"] == 98.5  # -1.5%（V字・中期）
        assert rec["qty"] == 1.0


class _MD(MCPTool[MarketDataInput]):
    name = "market_data"

    async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
        return MarketDataOutput(
            success=True, data={t: {"current_price": 100.0} for t in tool_input.tickers}
        )


class _FUND(MCPTool[FundamentalsInput]):
    name = "fundamentals"

    async def _execute(self, tool_input: FundamentalsInput) -> FundamentalsOutput:
        return FundamentalsOutput(
            success=True, data={"per": 15.0, "revenue_growth": 0.2, "roe": 0.16}
        )


class _TECH(MCPTool[TechnicalsInput]):
    name = "technicals"

    async def _execute(self, tool_input: TechnicalsInput) -> TechnicalsOutput:
        return TechnicalsOutput(success=True, data={"rsi": 50.0}, signals=["macd_bullish"])


class _LLM(MCPTool[LLMCallInput]):
    name = "llm_call"

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        payload = {
            "thesis_checklist": [{"item": "売上+20%", "rationale": "..."}],
            "reasons": [{"title": "成長", "detail": "...", "evidence": "..."}],
            "risks": [{"title": "競合", "detail": "...", "magnitude": "medium"}],
            "scenarios": [
                {"type": "bull", "target_price": 130, "return_pct": 0.3, "prob": 0.3},
                {"type": "base", "target_price": 120, "return_pct": 0.2, "prob": 0.5},
                {"type": "bear", "target_price": 90, "return_pct": -0.1, "prob": 0.2},
            ],
            "ai_confidence": 80,
        }
        return LLMCallOutput(success=True, response=json.dumps(payload))


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ma.sqlite")
    create_all(eng)
    with Session(eng) as s:
        s.add(
            PortfolioSnapshot(
                date=dt.date(2026, 5, 22),
                total_assets_jpy=100000.0,
                cash_jpy=100000.0,
                us_stocks_value_jpy=0.0,
                jp_stocks_value_jpy=0.0,
                satellite_value_jpy=0.0,
                core_value_jpy=0.0,
                usd_jpy_rate=150.0,
                holding_count=0,
                daily_pnl_jpy=0.0,
            )
        )
        s.add(
            ScreeningResult(
                ticker="AAPL",
                screened_at=dt.datetime(2026, 5, 22, 5),
                v_shape_score=70.0,
                theme_score=30.0,
                composite_score=70.0,
                screening_passed=True,
            )
        )
        s.commit()
    return eng


def _ctx(tmp_path: Path, with_llm: bool = True):
    engine = _engine(tmp_path)
    host = MCPHost()
    host.register(_MD())
    host.register(_FUND())
    host.register(_TECH())
    if with_llm:
        host.register(_LLM())
    return AgentContext(host=host, engine=engine, invocation_id="inv")


class TestAgent:
    async def test_generates_signal(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, with_llm=True)
        out = await MarketAnalystAgent(ctx).execute(
            MarketAnalystInput(invocation_id="inv", tickers=["AAPL"])
        )
        assert out.success is True
        with Session(ctx.engine) as s:
            sig = s.exec(select(BuySignal).where(col(BuySignal.is_active))).one()
        assert sig.ticker == "AAPL"
        assert sig.ai_confidence == 80.0
        assert sig.target_price == 120.0  # base シナリオ
        assert len(sig.scenarios) == 3
        # 確率正規化（0.3+0.5+0.2=1.0 のまま）
        assert abs(sum(s["prob"] for s in sig.scenarios) - 1.0) < 1e-6

    async def test_graceful_without_llm(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, with_llm=False)  # llm_call 未登録
        out = await MarketAnalystAgent(ctx).execute(
            MarketAnalystInput(invocation_id="inv", tickers=["AAPL"])
        )
        assert out.success is True
        with Session(ctx.engine) as s:
            sig = s.exec(select(BuySignal).where(col(BuySignal.is_active))).one()
        assert sig.ai_confidence == 50.0  # LLM 無し → 既定
        assert sig.scenarios == []

    async def test_missing_price_skips(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        host = MCPHost()

        class _NoPrice(MCPTool[MarketDataInput]):
            name = "market_data"

            async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
                return MarketDataOutput(success=True, data={})

        host.register(_NoPrice())
        host.register(_FUND())
        host.register(_TECH())
        ctx = AgentContext(host=host, engine=engine, invocation_id="inv")
        out = await MarketAnalystAgent(ctx).execute(
            MarketAnalystInput(invocation_id="inv", tickers=["AAPL"])
        )
        assert out.success is True
        assert len(out.skipped) == 1
