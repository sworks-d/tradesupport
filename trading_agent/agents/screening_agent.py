"""screening-agent（AGENT_SPECS.md §1）。

universe から候補を絞り込む。各銘柄について MCP ツールでデータを集約し、screening ツール
（採点ロジック）に委譲してランク付けする。

Phase 1 の制約：90日高安・四半期 EPS・銘柄別ニュース件数はツール側の追加配線が必要なため、
本エージェントは現状取得できるフィールド（現在値 / RSI / MACD / 増収率 / PER・PBR）を渡す。
不足フィールドの採点コンポーネントは screening 側でスキップ（部分スコア）。配線拡充は後日。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.mcp_tools.fundamentals import FundamentalsInput
from trading_agent.mcp_tools.market_data import MarketDataInput
from trading_agent.mcp_tools.screening import ScreeningInput, ScreeningTickerData
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger


class ScreeningAgentInput(AgentInput):
    universe_size: int = 500
    strategies: list[str] = Field(default_factory=lambda: ["v_shape", "theme"])
    min_score: float = 50.0
    max_results: int = 30


class ScreeningAgentOutput(AgentOutput):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    total_screened: int = 0
    strategy_breakdown: dict[str, int] = Field(default_factory=dict)


class ScreeningAgent(Agent[ScreeningAgentInput]):
    """V字 / テーマ候補のスクリーニングエージェント。"""

    name = "screening_agent"
    description = "universe からデータを集約し、screening ツールで候補を絞り込む。"
    required_tools = ["screening", "market_data", "technicals", "fundamentals"]
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: ScreeningAgentInput) -> AgentOutput:
        universe = self._load_universe(self._ctx.engine, agent_input.universe_size)
        tickers_data = [await self._gather(u) for u in universe]

        sout = await self._ctx.call_tool(
            "screening",
            ScreeningInput(
                tickers_data=tickers_data,
                strategies=agent_input.strategies,
                min_score=agent_input.min_score,
                max_results=agent_input.max_results,
                persist=not agent_input.dry_run,
            ),
        )
        results: list[dict[str, Any]] = getattr(sout, "results", []) or []
        total: int = getattr(sout, "total_screened", 0)

        breakdown: Counter[str] = Counter()
        for r in results:
            for strat in r.get("matched_strategies", []):
                breakdown[strat] += 1

        return ScreeningAgentOutput(
            success=sout.success,
            invocation_id=agent_input.invocation_id,
            summary=f"{len(results)} 候補を抽出（{total} 銘柄スクリーニング）",
            candidates=results,
            total_screened=total,
            strategy_breakdown=dict(breakdown),
        )

    def _load_universe(self, engine: Engine, limit: int) -> list[Universe]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(Universe)
                    .where(col(Universe.is_active))
                    .order_by(col(Universe.market_cap_jpy).desc())
                    .limit(limit)
                )
            )

    async def _gather(self, u: Universe) -> ScreeningTickerData:
        data = ScreeningTickerData(
            ticker=u.ticker,
            name=u.name,
            market=u.market,
            sector=u.sector,
            market_cap=u.market_cap_jpy,
        )
        await self._fill_market_data(u.ticker, data)
        await self._fill_technicals(u.ticker, data)
        await self._fill_fundamentals(u.ticker, data)
        return data

    async def _fill_market_data(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool("market_data", MarketDataInput(tickers=[ticker]))
            payload = getattr(out, "data", None)
            if out.success and payload:
                data.current_price = payload.get(ticker, {}).get("current_price")
        except Exception as exc:
            self._log.warning("screening_market_data_failed", ticker=ticker, error=str(exc))

    async def _fill_technicals(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool("technicals", TechnicalsInput(ticker=ticker))
            payload = getattr(out, "data", None)
            signals = getattr(out, "signals", []) or []
            if out.success and payload:
                rsi = payload.get("rsi")
                if isinstance(rsi, int | float):
                    data.rsi = float(rsi)
                data.macd_cross_recent = "golden_cross" in signals or "macd_bullish" in signals
        except Exception as exc:
            self._log.warning("screening_technicals_failed", ticker=ticker, error=str(exc))

    async def _fill_fundamentals(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool(
                "fundamentals",
                FundamentalsInput(ticker=ticker, fields=["revenue_growth", "per", "pbr"]),
            )
            payload = getattr(out, "data", None)
            if out.success and payload:
                growth = payload.get("revenue_growth")
                if isinstance(growth, int | float):
                    data.revenue_growth_latest_q = float(growth)
        except Exception as exc:
            self._log.warning("screening_fundamentals_failed", ticker=ticker, error=str(exc))
