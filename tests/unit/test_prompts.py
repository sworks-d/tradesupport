"""プロンプト管理の単体テスト（Task 1.3.2）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.agents.prompts import PromptLibrary


class TestPromptLibrary:
    def test_version_extracted(self) -> None:
        lib = PromptLibrary()
        assert lib.version("screening/theme_match.xml") == "1.0.0"

    def test_render_fills_variables(self) -> None:
        lib = PromptLibrary()
        out = lib.render(
            "screening/theme_match.xml",
            ticker="NVDA",
            name="NVIDIA",
            sector="半導体",
            themes="AI, 半導体",
            business_summary="データセンター向け GPU",
        )
        assert "NVDA" in out
        assert "NVIDIA" in out
        assert "{{" not in out  # 未展開のプレースホルダが残っていない

    def test_custom_base_dir(self, tmp_path: Path) -> None:
        (tmp_path / "a.xml").write_text("<!-- version: 9.9 -->\nHello {{ who }}", encoding="utf-8")
        lib = PromptLibrary(base_dir=tmp_path)
        assert lib.version("a.xml") == "9.9"
        assert lib.render("a.xml", who="world") == "<!-- version: 9.9 -->\nHello world"

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            PromptLibrary().load("nope/missing.xml")
