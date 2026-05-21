"""Ollama クライアント（SYSTEM_DESIGN.md §5.6）。

ローカル Ollama（Cold Path）を HTTP で呼ぶ薄いラッパー。
"""

from __future__ import annotations

from trading_agent.llm.types import RawLLMResponse
from trading_agent.mcp_tools.base import NetworkError


class OllamaClient:
    """Ollama /api/generate のラッパー。"""

    def __init__(self, host: str, model: str) -> None:
        self._host = host.rstrip("/")
        self._model = model

    async def call(
        self,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> RawLLMResponse:
        import httpx

        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system

        try:
            resp = httpx.post(f"{self._host}/api/generate", json=payload, timeout=120.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NetworkError(f"Ollama error: {exc}") from exc

        data = resp.json()
        return RawLLMResponse(
            text=data.get("response", ""),
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
            model=f"ollama:{self._model}",
        )
