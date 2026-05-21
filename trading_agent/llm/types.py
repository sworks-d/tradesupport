"""LLM クライアント共通の型（SYSTEM_DESIGN.md §5）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RawLLMResponse:
    """LLM クライアントの生レスポンス。"""

    text: str
    tokens_in: int
    tokens_out: int
    model: str  # 実際に使われたモデル名（cost_logs 用）


class LLMClient(Protocol):
    """LLM クライアントの共通インターフェース（テストでモック注入）。"""

    async def call(
        self,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> RawLLMResponse: ...
