"""Ollama 未インストール環境用フォールバック：Cold Path を Anthropic Haiku で代用する。

`OllamaClient` と同じ `LLMClient` プロトコル（async def call(...)）を満たし、
`build_host` で `ollama_client` の差し替えとしてそのまま使える。

実モデルは `claude-haiku-4-5` を使用（最新の Haiku モデル）。コスト記録は
`record_cost` 側で Haiku の実モデル名がそのまま入る（未登録モデルは router.PRICING_JPY
のフォールバックで Sonnet 単価扱い＝安全側に多めに見積もる）。

理想的には Haiku 単価を `PRICING_JPY` に追加すべきだが、運用上は安全側の見積もりで OK。
"""

from __future__ import annotations

from trading_agent.llm.anthropic_client import AnthropicClient
from trading_agent.llm.types import RawLLMResponse

DEFAULT_HAIKU_MODEL = "claude-haiku-4-5"


class HaikuFallbackClient:
    """Ollama の代わりに Anthropic Haiku を呼ぶ Cold Path 用クライアント。"""

    def __init__(self, api_key: str, model: str = DEFAULT_HAIKU_MODEL) -> None:
        self._anthropic = AnthropicClient(api_key)
        self._model = model

    async def call(
        self,
        model: str,  # noqa: ARG002  # router から渡される "ollama" は無視（実モデルは self._model）
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> RawLLMResponse:
        return await self._anthropic.call(
            self._model, prompt, system, max_tokens, temperature
        )
