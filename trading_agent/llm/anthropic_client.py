"""Anthropic API クライアント（SYSTEM_DESIGN.md §5.6）。

Claude Sonnet / Opus を呼ぶ薄いラッパー。エラーは MCP の型付き例外に正規化する。
"""

from __future__ import annotations

from trading_agent.llm.types import RawLLMResponse
from trading_agent.mcp_tools.base import AuthError, NetworkError


class AnthropicClient:
    """Anthropic Messages API のラッパー。"""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def call(
        self,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> RawLLMResponse:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            AsyncAnthropic,
            AuthenticationError,
            RateLimitError,
        )

        client = AsyncAnthropic(api_key=self._api_key)
        kwargs: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            message = await client.messages.create(**kwargs)  # type: ignore[call-overload]
        except AuthenticationError as exc:
            raise AuthError(f"Anthropic auth failed: {exc}") from exc
        except RateLimitError as exc:
            from trading_agent.mcp_tools.base import RateLimitError as MCPRateLimit

            raise MCPRateLimit(f"Anthropic rate limited: {exc}") from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise NetworkError(f"Anthropic API error: {exc}") from exc

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        return RawLLMResponse(
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
            model=model,
        )
