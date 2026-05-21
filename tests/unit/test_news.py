"""news ツールの単体テスト（Task 1.1.4）。fetcher を注入しネットワーク非依存で検証。"""

from __future__ import annotations

from datetime import datetime

from trading_agent.mcp_tools.base import MCPErrorType, NetworkError
from trading_agent.mcp_tools.news import (
    NewsInput,
    NewsTool,
    dedupe_articles,
    detect_language,
)


def _article(
    title: str, url: str, published: str = "2026-05-22T00:00:00", summary: str = ""
) -> dict:
    return {
        "title": title,
        "summary": summary,
        "source": "Test",
        "url": url,
        "published_at": published,
    }


class TestLanguage:
    def test_japanese(self) -> None:
        assert detect_language("日銀が利上げ") == "ja"

    def test_english(self) -> None:
        assert detect_language("Fed holds rates") == "en"


class TestDedupe:
    def test_exact_url(self) -> None:
        items = [_article("A", "http://x/1"), _article("B different", "http://x/1")]
        assert len(dedupe_articles(items)) == 1

    def test_same_headline(self) -> None:
        items = [_article("Same Title", "http://x/1"), _article("Same Title", "http://x/2")]
        assert len(dedupe_articles(items)) == 1

    def test_fuzzy_similar(self) -> None:
        items = [
            _article("Apple beats earnings expectations today", "http://x/1"),
            _article("Apple beats earnings expectations today!", "http://x/2"),
        ]
        assert len(dedupe_articles(items)) == 1

    def test_distinct_kept(self) -> None:
        items = [_article("Apple up", "http://x/1"), _article("Tesla down", "http://x/2")]
        assert len(dedupe_articles(items)) == 2


class TestExecute:
    async def test_merges_and_dedupes(self) -> None:
        def f1(_inp: NewsInput) -> list[dict]:
            return [_article("Same", "http://x/1")]

        def f2(_inp: NewsInput) -> list[dict]:
            return [_article("Same", "http://x/2"), _article("Other", "http://x/3")]

        tool = NewsTool(fetchers=[f1, f2])
        out = await tool.execute(NewsInput())
        titles = {a["title"] for a in out.articles}
        assert titles == {"Same", "Other"}

    async def test_language_annotated(self) -> None:
        tool = NewsTool(fetchers=[lambda _inp: [_article("日銀利上げ", "http://x/1")]])
        out = await tool.execute(NewsInput())
        assert out.articles[0]["language"] == "ja"

    async def test_since_filter(self) -> None:
        old = _article("Old", "http://x/old", published="2020-01-01T00:00:00")
        new = _article("New", "http://x/new", published="2026-05-22T00:00:00")
        tool = NewsTool(fetchers=[lambda _inp: [old, new]])
        out = await tool.execute(NewsInput(since=datetime(2026, 1, 1)))
        urls = {a["url"] for a in out.articles}
        assert urls == {"http://x/new"}

    async def test_ticker_filter(self) -> None:
        articles = [
            _article("AAPL rallies", "http://x/1"),
            _article("Random macro story", "http://x/2"),
        ]
        tool = NewsTool(fetchers=[lambda _inp: articles])
        out = await tool.execute(NewsInput(tickers=["AAPL"], since=datetime(2020, 1, 1)))
        assert {a["url"] for a in out.articles} == {"http://x/1"}

    async def test_graceful_degradation(self) -> None:
        def boom(_inp: NewsInput) -> list[dict]:
            raise NetworkError("feed down")

        ok = lambda _inp: [_article("OK", "http://x/1")]  # noqa: E731
        tool = NewsTool(fetchers=[boom, ok])
        out = await tool.execute(NewsInput(since=datetime(2020, 1, 1)))
        assert out.success is True
        assert out.metadata["source_failures"] == 1
        assert len(out.articles) == 1

    async def test_all_sources_fail(self) -> None:
        def boom(_inp: NewsInput) -> list[dict]:
            raise NetworkError("down")

        tool = NewsTool(fetchers=[boom])
        tool.backoff_base = 0.0
        out = await tool.execute(NewsInput())
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
