"""Cold Path（要約・分類・NER）用の Anthropic Haiku クライアント。

`LLMClient` プロトコル（async def call(...)）を満たし、`build_host` で `cold_client` として
注入する。router は cold ルートに `MODEL_HAIKU` を返し、コストは router.PRICING_JPY の
Haiku 単価（0.15/0.75）で正しく記録される。

実モデルは `claude-haiku-4-5`（最新 Haiku）。
（旧称 OllamaFallback。Ollama は廃止し Cold Path は Haiku 恒久。）
"""

from __future__ import annotations

from trading_agent.llm.anthropic_client import AnthropicClient
from trading_agent.llm.types import RawLLMResponse

DEFAULT_HAIKU_MODEL = "claude-haiku-4-5"


class HaikuFallbackClient:
    """Cold Path 用 Anthropic Haiku クライアント。"""

    def __init__(self, api_key: str, model: str = DEFAULT_HAIKU_MODEL) -> None:
        self._anthropic = AnthropicClient(api_key)
        self._model = model

    async def call(
        self,
        model: str,  # noqa: ARG002  # router から渡される MODEL_HAIKU は無視（実モデルは self._model）
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> RawLLMResponse:
        return await self._anthropic.call(
            self._model, prompt, system, max_tokens, temperature
        )
