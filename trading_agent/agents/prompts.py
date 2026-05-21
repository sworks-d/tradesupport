"""プロンプト管理（AGENT_SPECS.md §9）。

``agents/prompts/`` 配下の XML プロンプトを読み込み、Jinja2 でテンプレート展開する。
ファイル冒頭の ``<!-- version: X.Y.Z -->`` を版数として取得できる（精度の逆引き用）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Template

_VERSION_RE = re.compile(r"version:\s*(\S+)")


class PromptLibrary:
    """XML プロンプトのローダ + レンダラ。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else Path(__file__).parent / "prompts"

    def path(self, rel_path: str) -> Path:
        return self._dir / rel_path

    def load(self, rel_path: str) -> str:
        """生のプロンプト文字列を読み込む。"""
        return self.path(rel_path).read_text(encoding="utf-8")

    def version(self, rel_path: str) -> str:
        """冒頭コメントの版数を返す（無ければ "unknown"）。"""
        head = self.load(rel_path)[:200]
        match = _VERSION_RE.search(head)
        return match.group(1) if match else "unknown"

    def render(self, rel_path: str, **variables: Any) -> str:
        """Jinja2 でテンプレート展開した文字列を返す。"""
        return Template(self.load(rel_path)).render(**variables)
