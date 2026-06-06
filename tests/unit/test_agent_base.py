"""エージェント基底クラスと実行ラッパーの単体テスト（Task 1.3.1/1.3.4/1.3.5）。"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput, execute_agent
from trading_agent.agents.context import AgentContext
from trading_agent.db import create_all, get_engine
from trading_agent.llm.budget import record_cost
from trading_agent.mcp_tools.base import MCPHost, MCPTool, MCPToolInput, MCPToolOutput
from trading_agent.models.analytics import AnalysisLog


class _In(AgentInput):
    pass


class _OkAgent(Agent[_In]):
    name = "ok_agent"
    default_routing = "hot"

    async def execute(self, agent_input: _In) -> AgentOutput:
        return AgentOutput(
            success=True, invocation_id=agent_input.invocation_id, summary="done", llm_cost_jpy=1.5
        )


class _BoomAgent(Agent[_In]):
    name = "boom_agent"
    default_routing = "hot"

    async def execute(self, agent_input: _In) -> AgentOutput:
        raise RuntimeError("kaboom")


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "agent.sqlite")
    create_all(eng)
    return eng


class TestExecuteAgent:
    async def test_success_logs_analysis(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        out = await execute_agent(
            _OkAgent(), _In(invocation_id="inv1"), engine, halt_file=tmp_path / "NOPE"
        )
        assert out.success is True
        assert out.invocation_id == "inv1"
        assert out.duration_ms >= 0
        with Session(engine) as s:
            row = s.exec(select(AnalysisLog).where(col(AnalysisLog.invocation_id) == "inv1")).one()
        assert row.status == "success"
        assert row.agent == "ok_agent"
        assert row.ended_at is not None

    async def test_shared_invocation_id_logs_each_agent(self, tmp_path: Path) -> None:
        """A-1 回帰: 朝バッチのように複数エージェントが同一 invocation_id を共有しても、
        各エージェントの AnalysisLog 行が自分の terminal status で更新される。
        旧実装は _log_end が first() で先頭1行のみ更新し、後続は永久 running に残った。
        """
        engine = _engine(tmp_path)
        shared = "morning_2026-06-06"
        out1 = await execute_agent(
            _OkAgent(), _In(invocation_id=shared), engine, halt_file=tmp_path / "NOPE"
        )
        out2 = await execute_agent(
            _BoomAgent(), _In(invocation_id=shared), engine, halt_file=tmp_path / "NOPE"
        )
        assert out1.success is True
        assert out2.success is False
        with Session(engine) as s:
            rows = list(
                s.exec(select(AnalysisLog).where(col(AnalysisLog.invocation_id) == shared))
            )
        # 2 エージェント = 2 行、どちらも running ではない（自分の行が更新されている）
        assert len(rows) == 2
        by_agent = {r.agent: r for r in rows}
        assert by_agent["ok_agent"].status == "success"
        assert by_agent["boom_agent"].status == "failure"
        assert all(r.ended_at is not None for r in rows)
        assert not any(r.status == "running" for r in rows)

    async def test_halt_aborts(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        halt = tmp_path / "HALT"
        halt.write_text("stop")
        out = await execute_agent(_OkAgent(), _In(invocation_id="inv2"), engine, halt_file=halt)
        assert out.success is False
        assert "HALT" in (out.error or "")

    async def test_budget_aborts(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        record_cost(  # 日次上限 500 を超過させる
            engine,
            model="claude-opus-4-7",
            agent="x",
            purpose="x",
            tokens_in=0,
            tokens_out=100000,  # 11.25/1k → 1125 円
        )
        out = await execute_agent(
            _OkAgent(), _In(invocation_id="inv3"), engine, halt_file=tmp_path / "NOPE"
        )
        assert out.success is False
        assert "Budget" in (out.error or "")

    async def test_exception_is_caught(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        out = await execute_agent(
            _BoomAgent(), _In(invocation_id="inv4"), engine, halt_file=tmp_path / "NOPE"
        )
        assert out.success is False
        assert "kaboom" in (out.error or "")
        with Session(engine) as s:
            row = s.exec(select(AnalysisLog).where(col(AnalysisLog.invocation_id) == "inv4")).one()
        assert row.status == "failure"


class TestAgentContext:
    async def test_call_tool(self, tmp_path: Path) -> None:
        class _ToolIn(MCPToolInput):
            value: int = 0

        class _Tool(MCPTool[_ToolIn]):
            name = "double"

            async def _execute(self, tool_input: _ToolIn) -> MCPToolOutput:
                return MCPToolOutput(success=True, data=tool_input.value * 2)

        host = MCPHost()
        host.register(_Tool())
        ctx = AgentContext(host=host, engine=_engine(tmp_path), invocation_id="inv")
        out = await ctx.call_tool("double", _ToolIn(value=21))
        assert out.data == 42
