"""llm_call ツールと LLM 層の単体テスト（Task 1.1.8）。

LLM クライアントはモック注入。DB は一時 SQLite。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from trading_agent.db import create_all, get_engine
from trading_agent.llm.budget import BudgetGuard, record_cost
from trading_agent.llm.router import (
    MODEL_OLLAMA,
    MODEL_OPUS,
    MODEL_SONNET,
    estimate_cost_jpy,
    route_llm_call,
)
from trading_agent.llm.types import RawLLMResponse
from trading_agent.mcp_tools.base import MCPErrorType, NetworkError
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallTool
from trading_agent.models.analytics import CostLog


class _MockClient:
    def __init__(
        self,
        text: str = "ok",
        tokens_in: int = 100,
        tokens_out: int = 50,
        model: str = MODEL_SONNET,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.model = model
        self.error = error
        self.calls = 0

    async def call(
        self, model: str, prompt: str, system: str | None, max_tokens: int, temperature: float
    ) -> RawLLMResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RawLLMResponse(self.text, self.tokens_in, self.tokens_out, self.model)


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "llm.sqlite")
    create_all(eng)
    return eng


class TestRouter:
    def test_hints(self) -> None:
        assert route_llm_call("cold", "analysis", "x") == MODEL_OLLAMA
        assert route_llm_call("critical", "analysis", "x") == MODEL_OPUS
        assert route_llm_call("hot", "analysis", "x") == MODEL_SONNET

    def test_purpose_defaults(self) -> None:
        assert route_llm_call(None, "summarization", "short") == MODEL_OLLAMA
        assert route_llm_call(None, "deep_dive", "x") == MODEL_OPUS
        assert route_llm_call(None, "analysis", "x") == MODEL_SONNET

    def test_cold_escalates_on_long_prompt(self) -> None:
        long_prompt = "x" * 20000  # > 4000 tokens 相当
        assert route_llm_call(None, "summarization", long_prompt) == MODEL_SONNET

    def test_cost_estimation(self) -> None:
        assert estimate_cost_jpy(MODEL_SONNET, 1000, 1000) == pytest.approx(2.7)
        assert estimate_cost_jpy(MODEL_OPUS, 1000, 1000) == pytest.approx(13.5)
        assert estimate_cost_jpy(MODEL_OLLAMA, 1000, 1000) == 0.0


class TestBudget:
    def test_record_and_today_cost(self, engine) -> None:
        record_cost(
            engine,
            model=MODEL_SONNET,
            agent="a",
            purpose="analysis",
            tokens_in=1000,
            tokens_out=1000,
        )
        guard = BudgetGuard(engine)
        assert guard.today_cost_jpy() == pytest.approx(2.7)

    def test_can_proceed_under_budget(self, engine) -> None:
        guard = BudgetGuard(engine)
        ok, _ = guard.can_proceed(10.0, None)
        assert ok is True

    def test_can_proceed_over_daily(self, engine) -> None:
        _insert_cost(engine, 600.0)  # 既定日次上限 500 を超過
        guard = BudgetGuard(engine)
        ok, reason = guard.can_proceed(10.0, None)
        assert ok is False
        assert "daily" in reason

    def test_critical_bypasses_budget(self, engine) -> None:
        _insert_cost(engine, 600.0)
        guard = BudgetGuard(engine)
        ok, reason = guard.can_proceed(10.0, "critical")
        assert ok is True
        assert reason == "critical_bypass"


class TestTool:
    async def test_success_records_cost(self, engine) -> None:
        client = _MockClient(tokens_in=1000, tokens_out=1000)
        tool = LLMCallTool(engine, anthropic_client=client)
        out = await tool.execute(LLMCallInput(prompt="hi", purpose="analysis", agent="screening"))
        assert out.success is True
        assert out.model_used == MODEL_SONNET
        assert out.cost_jpy == pytest.approx(2.7)
        with Session(engine) as session:
            rows = session.exec(select(CostLog)).all()
        assert len(rows) == 1
        assert rows[0].agent == "screening"

    async def test_routes_to_ollama(self, engine) -> None:
        anthropic = _MockClient(model=MODEL_SONNET)
        ollama = _MockClient(model="ollama:llama3.1")
        tool = LLMCallTool(engine, anthropic_client=anthropic, ollama_client=ollama)
        out = await tool.execute(LLMCallInput(prompt="hi", routing_hint="cold"))
        assert ollama.calls == 1
        assert anthropic.calls == 0
        assert out.model_used == "ollama:llama3.1"

    async def test_budget_exceeded_rejected(self, engine) -> None:
        _insert_cost(engine, 600.0)
        tool = LLMCallTool(engine, anthropic_client=_MockClient())
        out = await tool.execute(LLMCallInput(prompt="hi"))
        assert out.success is False
        assert out.metadata["budget_exceeded"] is True

    async def test_unconfigured_client_is_auth_error(self, engine) -> None:
        tool = LLMCallTool(engine)  # クライアント未設定
        out = await tool.execute(LLMCallInput(prompt="hi", routing_hint="hot"))
        assert out.success is False
        assert out.error_type == MCPErrorType.AUTH_ERROR

    async def test_network_error_retries(self, engine) -> None:
        client = _MockClient(error=NetworkError("down"))
        tool = LLMCallTool(engine, anthropic_client=client)
        tool.backoff_base = 0.0
        out = await tool.execute(LLMCallInput(prompt="hi"))
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
        assert client.calls == 3


def _insert_cost(engine, cost_jpy: float) -> None:
    from trading_agent.utils.time_utils import today_jst, utcnow

    # JST 統一: BudgetGuard.today_cost_jpy() が today_jst() でクエリするため
    now = utcnow()
    with Session(engine) as session:
        session.add(
            CostLog(
                timestamp=now,
                date=today_jst(),
                model=MODEL_OPUS,
                agent="test",
                purpose="test",
                tokens_in=0,
                tokens_out=0,
                cost_usd=cost_jpy / 150.0,
                cost_jpy=cost_jpy,
            )
        )
        session.commit()
