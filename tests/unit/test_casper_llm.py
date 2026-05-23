"""CASPER LLM解釈（P3-7）の単体テスト。LLMはモック注入・DBは一時SQLite。

検証の柱：
- 正常：JSON応答を verdict/confidence/reason に解釈し JudgeVerdict を返す。
- フォールバック：材料0 / パース失敗 / 接続失敗 / 予算超過 → 決定論版 casper() に落ちる。
- R5：source_refs は入力ニュース由来を維持（LLMが出典を捏造しない土台）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trading_agent.db import create_all, get_engine
from trading_agent.llm.router import MODEL_SONNET
from trading_agent.llm.types import RawLLMResponse
from trading_agent.magi.casper_llm import _parse, casper_llm
from trading_agent.mcp_tools.base import NetworkError, SourceRef
from trading_agent.mcp_tools.llm_call import LLMCallTool
from trading_agent.mcp_tools.news import NewsOutput


class _MockClient:
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def call(
        self, model: str, prompt: str, system: str | None, max_tokens: int, temperature: float
    ) -> RawLLMResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RawLLMResponse(self.text, tokens_in=120, tokens_out=40, model=MODEL_SONNET)


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "casper.sqlite")
    create_all(eng)
    return eng


def _news(*titles: str) -> NewsOutput:
    arts = [
        {"title": t, "summary": "", "source": "Test", "url": f"http://x/{i}"}
        for i, t in enumerate(titles)
    ]
    refs = [
        SourceRef(source="Test", ref=f"http://x/{i}", as_of=datetime(2026, 5, 23))
        for i in range(len(titles))
    ]
    return NewsOutput(
        success=True, articles=arts, source_refs=refs, data_asof=datetime(2026, 5, 23)
    )


class TestParse:
    def test_clean_json(self) -> None:
        out = _parse('{"verdict":"buy","confidence":"高","reason":"好材料"}')
        assert out == ("buy", "高", "好材料")

    def test_code_fence_stripped(self) -> None:
        text = '```json\n{"verdict":"warn","confidence":"中","reason":"訴訟リスク"}\n```'
        assert _parse(text) == ("warn", "中", "訴訟リスク")

    def test_prose_around_json(self) -> None:
        text = 'はい。{"verdict":"hold","confidence":"低","reason":"拮抗"} です'
        assert _parse(text) == ("hold", "低", "拮抗")

    def test_invalid_verdict_rejected(self) -> None:
        assert _parse('{"verdict":"strong_buy","confidence":"高","reason":"x"}') is None

    def test_invalid_confidence_rejected(self) -> None:
        assert _parse('{"verdict":"buy","confidence":"high","reason":"x"}') is None

    def test_empty_reason_rejected(self) -> None:
        assert _parse('{"verdict":"buy","confidence":"高","reason":""}') is None

    def test_garbage_rejected(self) -> None:
        assert _parse("not json at all") is None


class TestCasperLlm:
    async def test_happy_path(self, engine) -> None:
        client = _MockClient('{"verdict":"buy","confidence":"高","reason":"受注増が優勢。"}')
        tool = LLMCallTool(engine, anthropic_client=client)
        v = await casper_llm("NVDA", news=_news("NVDA 受注増", "AI需要拡大"), llm_tool=tool)
        assert v.judge == "CASPER"
        assert v.verdict == "buy"
        assert v.confidence == "高"
        assert "受注" in v.reason
        assert client.calls == 1
        # R5：出典は入力ニュース由来を維持
        assert len(v.source_refs) == 2
        assert v.data_asof == datetime(2026, 5, 23)

    async def test_no_articles_falls_back_to_na(self, engine) -> None:
        client = _MockClient('{"verdict":"buy","confidence":"高","reason":"x"}')
        tool = LLMCallTool(engine, anthropic_client=client)
        v = await casper_llm("NVDA", news=NewsOutput(success=True, articles=[]), llm_tool=tool)
        assert v.verdict == "na"
        assert client.calls == 0  # 材料0ならLLMを呼ばない（コスト0）

    async def test_unparseable_falls_back_to_deterministic(self, engine) -> None:
        client = _MockClient("ごめんJSONで返せません")
        tool = LLMCallTool(engine, anthropic_client=client)
        # 決定論版は "最高益"(POS) を拾って buy になる
        v = await casper_llm("NVDA", news=_news("NVDA 最高益更新"), llm_tool=tool)
        assert v.verdict == "buy"
        assert "LLM" in v.reason or "材料" in v.reason  # 決定論版の文面

    async def test_network_error_falls_back(self, engine) -> None:
        client = _MockClient(error=NetworkError("down"))
        tool = LLMCallTool(engine, anthropic_client=client)
        v = await casper_llm("NVDA", news=_news("NVDA 下方修正"), llm_tool=tool)
        assert v.judge == "CASPER"
        assert v.verdict == "warn"  # 決定論版が "下方修正"(NEG) を拾う

    async def test_budget_exceeded_falls_back(self, engine, monkeypatch) -> None:
        client = _MockClient('{"verdict":"buy","confidence":"高","reason":"x"}')
        tool = LLMCallTool(engine, anthropic_client=client)
        monkeypatch.setattr(tool._budget, "can_proceed", lambda *a, **k: (False, "daily cap"))
        v = await casper_llm("NVDA", news=_news("NVDA 最高益"), llm_tool=tool)
        assert v.verdict == "buy"  # 決定論版（"最高益"=POS）
        assert client.calls == 0  # 予算で弾かれLLM未実行
