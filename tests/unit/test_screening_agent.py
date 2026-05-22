"""screening-agent の単体テスト（Task 1.4.2）。

データ系ツール（market_data/technicals/fundamentals）はモック、採点は実 ScreeningTool で検証。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.screening_agent import ScreeningAgent, ScreeningAgentInput
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, FundamentalsOutput
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataOutput
from trading_agent.mcp_tools.screening import ScreeningTool
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsOutput
from trading_agent.models.signals import ScreeningResult
from trading_agent.models.universe import Universe


class _MD(MCPTool[MarketDataInput]):
    name = "market_data"

    async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
        return MarketDataOutput(
            success=True, data={t: {"current_price": 100.0} for t in tool_input.tickers}
        )


class _TECH(MCPTool[TechnicalsInput]):
    name = "technicals"

    async def _execute(self, tool_input: TechnicalsInput) -> TechnicalsOutput:
        return TechnicalsOutput(success=True, data={"rsi": 40.0}, signals=["golden_cross"])


class _FUND(MCPTool[FundamentalsInput]):
    name = "fundamentals"

    async def _execute(self, tool_input: FundamentalsInput) -> FundamentalsOutput:
        return FundamentalsOutput(success=True, data={"revenue_growth": 0.15})


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "scr_agent.sqlite")
    create_all(eng)
    with Session(eng) as s:
        for ticker, cap in [("AAPL", 3.0e14), ("NVDA", 2.5e14)]:
            s.add(
                Universe(
                    ticker=ticker,
                    name=ticker,
                    market="US",
                    sector="Tech",
                    market_cap=cap,
                    market_cap_jpy=cap,
                    avg_volume_30d=1e7,
                )
            )
        s.commit()
    return eng


def _ctx(tmp_path: Path):
    engine = _engine(tmp_path)
    host = MCPHost()
    host.register(_MD())
    host.register(_TECH())
    host.register(_FUND())
    host.register(ScreeningTool(engine))  # 実採点ツール
    return AgentContext(host=host, engine=engine, invocation_id="inv")


class TestScreeningAgent:
    async def test_screens_and_ranks(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        out = await ScreeningAgent(ctx).execute(
            ScreeningAgentInput(invocation_id="inv", min_score=1.0, universe_size=10)
        )
        assert out.success is True
        assert out.total_screened == 2
        # rsi 40(+10) + golden_cross(+10) + 増収(+10) = v_shape 30 ≥ min_score 1
        assert len(out.candidates) == 2
        assert out.strategy_breakdown.get("v_shape") == 2

    async def test_persists_results(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        await ScreeningAgent(ctx).execute(
            ScreeningAgentInput(invocation_id="inv", min_score=1.0, universe_size=10)
        )
        with Session(ctx.engine) as s:
            rows = s.exec(select(ScreeningResult)).all()
        assert len(rows) == 2

    async def test_dry_run_no_persist(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        await ScreeningAgent(ctx).execute(
            ScreeningAgentInput(invocation_id="inv", min_score=1.0, dry_run=True)
        )
        with Session(ctx.engine) as s:
            rows = s.exec(select(ScreeningResult)).all()
        assert len(rows) == 0
