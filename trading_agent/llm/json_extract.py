"""LLM の応答から JSON を頑健に取り出すユーティリティ。

Claude（および多くの LLM）は JSON を求められても ` ```json ... ``` ` の markdown
コードフェンスで包んで返すことがある。`json.loads()` を生で当てるとパース失敗。
このモジュールは以下を順に試して dict を返す：

1. そのまま `json.loads()` を試す
2. ``` で始まる markdown code fence を剥がして再試行
3. 文中から最初の ``{ ... }`` 部分文字列を切り出して再試行

失敗時は空 dict（呼出側で「LLM 補強なし → ルール結果維持」のフォールバックが効く）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# ` ``` ` または ` ```json ` で始まり ` ``` ` で終わる fenced block を抽出。
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)\n?```", re.DOTALL)

# 中括弧バランスを取りつつ最初の JSON オブジェクトを切り出す（文中混在に強い）。
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str | None) -> dict[str, Any]:
    """LLM 応答文字列から JSON オブジェクトを抽出する。

    失敗時は空 dict を返す（呼出側で `.get(...)` がそのまま None を返せるよう）。
    入力が None / 空文字なら即空 dict。
    """
    if not text:
        return {}

    # 1) 素のパース
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, ValueError):
        pass

    # 2) ```json ... ``` を剥がす
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        inner = fence_match.group(1).strip()
        try:
            loaded = json.loads(inner)
            return loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass

    # 3) { ... } を貪欲に拾う（前後にコメント文がある場合）
    brace_match = _BRACE_RE.search(text)
    if brace_match:
        try:
            loaded = json.loads(brace_match.group(0))
            return loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass

    return {}
