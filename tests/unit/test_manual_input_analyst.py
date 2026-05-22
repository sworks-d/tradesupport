"""manual-input-analyst の単体テスト（Task 1.4.6）。LLM はモック注入。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlmodel import Session, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.manual_input_analyst import (
    ManualInputAnalystAgent,
    ManualInputAnalystInput,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.topics import ManualInput


class _LLM(MCPTool[LLMCallInput]):
    name = "llm_call"

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        payload = {
            "result_summary": "利下げ観測でハイテクに追い風",
            "affected_tickers": ["AAPL", "MSFT"],  # MSFT は未保有→フィルタされる
            "overall_direction": "positive",
            "overall_magnitude": "medium",
            "recommended_actions": ["AAPL の押し目を待つ"],
            "confidence": 0.7,
        }
        return LLMCallOutput(success=True, response=json.dumps(payload))


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "mi.sqlite")
    create_all(eng)
    with Session(eng) as s:
        s.add(
            Portfolio(
                ticker="AAPL",
                buy_date=dt.date(2026, 3, 1),
                buy_price=100.0,
                qty=10,
                currency="USD",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.2,
                stop_loss_pct=-0.08,
                target_date=dt.date(2026, 6, 1),
                thesis="t",
                status="active",
            )
        )
        s.commit()
    return eng


def _ctx(tmp_path: Path, with_llm: bool = True) -> AgentContext:
    host = MCPHost()
    if with_llm:
        host.register(_LLM())
    return AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")


class TestAgent:
    async def test_short_text_rejected(self, tmp_path: Path) -> None:
        out = await ManualInputAnalystAgent(_ctx(tmp_path)).execute(
            ManualInputAnalystInput(invocation_id="inv", text="短い")
        )
        assert out.success is False

    async def test_interprets_and_filters(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        out = await ManualInputAnalystAgent(ctx).execute(
            ManualInputAnalystInput(
                invocation_id="inv", text="Powell が利下げに前向き発言、6月FOMCで決定の可能性"
            )
        )
        assert out.success is True
        assert out.impact_direction == "positive"  # type: ignore[attr-defined]
        assert out.affected_tickers == ["AAPL"]  # type: ignore[attr-defined] # MSFT 除外
        with Session(ctx.engine) as s:
            rows = s.exec(select(ManualInput)).all()
        assert len(rows) == 1

    async def test_graceful_without_llm(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, with_llm=False)
        out = await ManualInputAnalystAgent(ctx).execute(
            ManualInputAnalystInput(invocation_id="inv", text="これは十分に長いテキストです")
        )
        assert out.success is True
        assert out.impact_direction == "neutral"  # type: ignore[attr-defined]
