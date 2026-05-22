"""sell-recommender の単体テスト（Task 1.4.4）。スコアは固定値、エージェントはモック注入。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.sell_recommender import (
    SellRecommenderAgent,
    SellRecommenderInput,
    determine_sell_recommendation,
    discipline_reasons,
    loss_magnitude,
    profit_taking_score,
    status_from_health,
    stop_loss_score,
    target_achievement,
    technical_warning,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataOutput
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsOutput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import Scenario, SellSignal


class TestComponents:
    def test_target_achievement(self) -> None:
        assert target_achievement(110, 100, 0.2) == pytest.approx(0.5)

    def test_technical_warning(self) -> None:
        assert technical_warning(75) == 1.0
        assert technical_warning(65) == 0.5
        assert technical_warning(50) == 0.0

    def test_loss_magnitude(self) -> None:
        assert loss_magnitude(92, 100, -0.08) == pytest.approx(1.0)
        assert loss_magnitude(96, 100, -0.08) == pytest.approx(0.5)

    def test_profit_score(self) -> None:
        assert profit_taking_score(0.5, 0.5, 0.5, 0.5) == pytest.approx(50.0)

    def test_stop_score(self) -> None:
        assert stop_loss_score(0.5, 0.5, 0.5, 0.5) == pytest.approx(50.0)

    def test_status(self) -> None:
        assert status_from_health(0.8) == "intact"
        assert status_from_health(0.5) == "weakening"
        assert status_from_health(0.3) == "broken"

    def test_recommendation_half_full(self) -> None:
        assert determine_sell_recommendation("profit_taking", 95, 10, 100)["qty"] == 10
        assert determine_sell_recommendation("profit_taking", 75, 10, 100)["qty"] == 5
        assert determine_sell_recommendation("stop_loss", 80, 10, 100)["type"] == "market"

    def test_discipline_reasons(self) -> None:
        rs = discipline_reasons(100, -0.08, 90)
        assert len(rs) == 2
        assert all(r["priority"] == "規律" for r in rs)


class _MD(MCPTool[MarketDataInput]):
    name = "market_data"

    def __init__(self, price: float) -> None:
        self._price = price

    async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
        return MarketDataOutput(
            success=True, data={t: {"current_price": self._price} for t in tool_input.tickers}
        )


class _TECH(MCPTool[TechnicalsInput]):
    name = "technicals"

    async def _execute(self, tool_input: TechnicalsInput) -> TechnicalsOutput:
        return TechnicalsOutput(success=True, data={"rsi": 50.0}, signals=[])


class _LLM(MCPTool[LLMCallInput]):
    name = "llm_call"

    def __init__(self, health: float) -> None:
        self._health = health

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        return LLMCallOutput(
            success=True,
            response=json.dumps({"overall_health": self._health, "ai_confidence": 0.5}),
        )


def _engine(tmp_path: Path, buy_date: dt.date):
    eng = get_engine(tmp_path / "sell.sqlite")
    create_all(eng)
    with Session(eng) as s:
        s.add(
            Portfolio(
                ticker="AAPL",
                buy_date=buy_date,
                buy_price=100.0,
                qty=10,
                currency="USD",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.2,
                stop_loss_pct=-0.08,
                target_date=dt.date(2026, 8, 1),
                thesis="t",
                status="active",
            )
        )
        s.commit()
    return eng


def _ctx(tmp_path: Path, price: float, health: float, buy_date: dt.date, with_llm: bool = True):
    engine = _engine(tmp_path, buy_date)
    host = MCPHost()
    host.register(_MD(price))
    host.register(_TECH())
    if with_llm:
        host.register(_LLM(health))
    return AgentContext(host=host, engine=engine, invocation_id="inv")


class TestAgent:
    async def test_profit_taking(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, price=130.0, health=0.6, buy_date=dt.date(2026, 3, 1))
        out = await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        assert out.success is True
        with Session(ctx.engine) as s:
            sig = s.exec(select(SellSignal).where(col(SellSignal.is_active))).one()
        assert sig.signal_type == "profit_taking"
        assert sig.recommended_action["qty_label"] in {"全量", "半量"}

    async def test_stop_loss_discipline(self, tmp_path: Path) -> None:
        # 含み損 + シナリオ崩壊 → 高スコア → 規律メッセージ
        ctx = _ctx(tmp_path, price=90.0, health=0.05, buy_date=dt.date(2026, 3, 1))
        await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        with Session(ctx.engine) as s:
            sig = s.exec(select(SellSignal).where(col(SellSignal.is_active))).one()
        assert sig.signal_type == "stop_loss"
        assert sig.score >= 70
        assert any(r.get("priority") == "規律" for r in sig.reasons)

    async def test_scenario_saved(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, price=130.0, health=0.6, buy_date=dt.date(2026, 3, 1))
        await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        with Session(ctx.engine) as s:
            scn = s.exec(select(Scenario).where(col(Scenario.ticker) == "AAPL")).one()
        assert scn.scenario_status == "weakening"  # health 0.6

    async def test_recently_bought_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, price=130.0, health=0.6, buy_date=dt.date(2026, 5, 21))
        out = await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        assert out.sell_signals == []
        assert out.scenario_updates == []
