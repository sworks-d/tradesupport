"""LLM ルーティングとコスト見積り（SYSTEM_DESIGN.md §5.1〜5.4）。

routing_hint / purpose からモデルを選び、トークン数からコスト（JPY）を見積もる。
純粋関数なので単体テストしやすい。
"""

from __future__ import annotations

# モデル ID（環境のモデル一覧に準拠）
MODEL_SONNET = "claude-sonnet-4-6"  # Hot Path
MODEL_OPUS = "claude-opus-4-7"  # Critical
MODEL_HAIKU = "claude-haiku-4-5"  # Cold Path（要約・分類・NER）。Anthropic Haiku
# 旧 MODEL_OLLAMA は廃止（Cold Path は Haiku 恒久）。route は cold → MODEL_HAIKU を返す。

# purpose ベースの分類（SYSTEM_DESIGN §5.2/5.3）
COLD_PURPOSES = frozenset({"summarization", "classification", "ner"})
CRITICAL_PURPOSES = frozenset({"deep_dive"})

# Cold を Hot に格上げするトークン閾値
_COLD_ESCALATION_TOKENS = 4000

# per-1K トークンの円単価（2026-05 時点、概算）
# Haiku が登録されていないと未知モデル扱いで Sonnet 単価にフォールバックされ、
# BudgetGuard が実コストの 3〜4 倍で予算消費を記録して誤発動する。
PRICING_JPY: dict[str, tuple[float, float]] = {
    MODEL_SONNET: (0.45, 2.25),
    MODEL_OPUS: (2.25, 11.25),
    MODEL_HAIKU: (0.15, 0.75),  # 約 1/3 単価（Haiku 4.5）。Cold Path の実コスト
}


def estimate_tokens(text: str) -> int:
    """ざっくりしたトークン数見積り（約4文字=1トークン）。"""
    return max(1, len(text) // 4)


def route_llm_call(routing_hint: str | None, purpose: str, prompt: str) -> str:
    """使用モデルを決定する（SYSTEM_DESIGN §5.3）。

    明示の routing_hint を最優先。なければ purpose ベース。Cold でも入力が長大なら Hot に格上げ。
    """
    if routing_hint:
        if routing_hint == "cold":
            return MODEL_HAIKU
        if routing_hint == "critical":
            return MODEL_OPUS
        return MODEL_SONNET  # "hot" など

    if purpose in COLD_PURPOSES:
        if estimate_tokens(prompt) > _COLD_ESCALATION_TOKENS:
            return MODEL_SONNET
        return MODEL_HAIKU
    if purpose in CRITICAL_PURPOSES:
        return MODEL_OPUS
    return MODEL_SONNET


def estimate_cost_jpy(model: str, tokens_in: int, tokens_out: int) -> float:
    """トークン数からコスト（円）を見積もる。未知モデルは Sonnet 単価で代替。"""
    rate_in, rate_out = PRICING_JPY.get(model, PRICING_JPY[MODEL_SONNET])
    return tokens_in / 1000 * rate_in + tokens_out / 1000 * rate_out
