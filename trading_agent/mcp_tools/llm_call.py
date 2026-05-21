"""llm_call MCP ツール（SYSTEM_DESIGN.md §3.2 / §5.6）。

LLM 呼び分け・予算チェック・コスト記録を統合する。

フロー（§5.6）：route → コスト見積り → 予算チェック → 実行 → cost_logs 記録。
- 予算超過：拒否（success=False、リトライしない）。critical は警告のみで通す。
- クライアント（Anthropic / Ollama）は注入可能（テストでモック）。
"""

from __future__ import annotations

import time

from sqlalchemy.engine import Engine

from trading_agent.llm.budget import BudgetGuard, record_cost
from trading_agent.llm.router import (
    MODEL_OLLAMA,
    estimate_cost_jpy,
    estimate_tokens,
    route_llm_call,
)
from trading_agent.llm.types import LLMClient
from trading_agent.mcp_tools.base import (
    AuthError,
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
)


class LLMCallInput(MCPToolInput):
    prompt: str
    system: str | None = None
    purpose: str = "analysis"
    routing_hint: str | None = None  # "hot" / "cold" / "critical"
    max_tokens: int = 4000
    temperature: float = 0.0
    response_format: str | None = None  # "json" / "text"
    agent: str = "unknown"  # cost_logs 用
    invocation_id: str | None = None


class LLMCallOutput(MCPToolOutput):
    response: str = ""
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_jpy: float = 0.0
    duration_ms: int = 0


class LLMCallTool(MCPTool[LLMCallInput]):
    """LLM 呼び分け + 予算 + コスト記録の統合ツール。"""

    name = "llm_call"
    description = "purpose/routing_hint で LLM を選び、予算内で実行してコストを記録する。"
    input_schema = LLMCallInput
    output_schema = LLMCallOutput

    def __init__(
        self,
        engine: Engine,
        *,
        anthropic_client: LLMClient | None = None,
        ollama_client: LLMClient | None = None,
    ) -> None:
        self._engine = engine
        self._anthropic = anthropic_client
        self._ollama = ollama_client
        self._budget = BudgetGuard(engine)

    async def _execute(self, tool_input: LLMCallInput) -> MCPToolOutput:
        model = route_llm_call(tool_input.routing_hint, tool_input.purpose, tool_input.prompt)

        # 予算チェック（事前見積り）
        est_in = estimate_tokens(tool_input.prompt + (tool_input.system or ""))
        est_cost = estimate_cost_jpy(model, est_in, tool_input.max_tokens)
        ok, reason = self._budget.can_proceed(est_cost, tool_input.routing_hint)
        if not ok:
            return LLMCallOutput(
                success=False,
                error=f"budget exceeded: {reason}",
                model_used=model,
                metadata={"budget_exceeded": True, "reason": reason},
            )

        # 実行（NetworkError/RateLimitError/AuthError は base が処理）
        client = self._select_client(model)
        start = time.time()
        raw = await client.call(
            model,
            tool_input.prompt,
            tool_input.system,
            tool_input.max_tokens,
            tool_input.temperature,
        )
        duration_ms = int((time.time() - start) * 1000)

        # コスト記録（実トークンで）
        cost_jpy = record_cost(
            self._engine,
            model=raw.model,
            agent=tool_input.agent,
            purpose=tool_input.purpose,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            invocation_id=tool_input.invocation_id,
        )

        return LLMCallOutput(
            success=True,
            response=raw.text,
            model_used=raw.model,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            cost_jpy=cost_jpy,
            duration_ms=duration_ms,
        )

    def _select_client(self, model: str) -> LLMClient:
        client = self._ollama if model == MODEL_OLLAMA else self._anthropic
        if client is None:
            raise AuthError(f"LLM client for '{model}' is not configured")
        return client
