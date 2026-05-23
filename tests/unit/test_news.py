"""news ツールの単体テスト（Task 1.1.4）。fetcher を注入しネットワーク非依存で検証。"""

from __future__ import annotations

from datetime import datetime

from trading_agent.magi.judges import casper
from trading_agent.mcp_tools.base import MCPErrorType, NetworkError
from trading_agent.mcp_tools.news import (
    NewsInput,
    NewsOutput,
    NewsTool,
    _default_fetchers,
    _fetch_gnews_rss,
    _fetch_yf_news,
    _gnews_query,
    _normalize_gnews_entry,
    _normalize_yf_item,
    dedupe_articles,
    detect_language,
)
from trading_agent.utils.time_utils import utcnow


def _article(
    title: str, url: str, published: str | None = None, summary: str = ""
) -> dict:
    # 既定の公開時刻は「今」（既定 since=now-24h を通る／日付ロールオーバーに強い）
    published = published or utcnow().isoformat()
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


class TestYfNormalize:
    def test_old_flat_shape(self) -> None:
        item = {
            "title": "NVDA beats earnings",
            "publisher": "Reuters",
            "link": "http://x/nvda",
            "providerPublishTime": 1716336000,  # 2024-05-22 epoch
        }
        art = _normalize_yf_item(item)
        assert art is not None
        assert art["title"] == "NVDA beats earnings"
        assert art["source"] == "Reuters"
        assert art["url"] == "http://x/nvda"
        assert art["published_at"].startswith("2024-05-22")

    def test_new_nested_shape(self) -> None:
        item = {
            "id": "abc",
            "content": {
                "title": "NVDA surges",
                "summary": "AI demand strong",
                "pubDate": "2026-05-22T12:00:00Z",
                "provider": {"displayName": "Bloomberg"},
                "canonicalUrl": {"url": "http://x/surge"},
            },
        }
        art = _normalize_yf_item(item)
        assert art is not None
        assert art["title"] == "NVDA surges"
        assert art["summary"] == "AI demand strong"
        assert art["source"] == "Bloomberg"
        assert art["url"] == "http://x/surge"
        assert art["published_at"] == "2026-05-22T12:00:00Z"

    def test_empty_dropped(self) -> None:
        assert _normalize_yf_item({"content": {}}) is None

    def test_fetch_skips_when_no_tickers(self) -> None:
        assert _fetch_yf_news(NewsInput()) == []


class TestGnewsNormalize:
    def test_entry_normalized(self) -> None:
        entry = {
            "title": "トヨタ 増収",
            "summary": "決算好調",
            "link": "http://g/toyota",
            "published": "2026-05-22T00:00:00",
            "source": {"title": "日経"},
        }
        art = _normalize_gnews_entry(entry)
        assert art["title"] == "トヨタ 増収"
        assert art["source"] == "日経"
        assert art["url"] == "http://g/toyota"

    def test_source_fallback(self) -> None:
        art = _normalize_gnews_entry({"title": "x", "link": "http://g/x"})
        assert art["source"] == "GoogleNews"

    def test_query_with_company(self) -> None:
        assert _gnews_query("7203", "トヨタ") == "7203 トヨタ 株 OR stock"

    def test_query_ticker_only(self) -> None:
        assert _gnews_query("NVDA", None) == "NVDA 株 OR stock"

    def test_fetch_skips_when_no_tickers(self) -> None:
        assert _fetch_gnews_rss(NewsInput()) == []


class TestDefaultFetchers:
    def test_includes_free_sources_first(self) -> None:
        fetchers = _default_fetchers()
        assert fetchers[0] is _fetch_yf_news
        assert fetchers[1] is _fetch_gnews_rss
        assert len(fetchers) == 4

    async def test_cross_source_dedupe(self) -> None:
        """yf と gnews が同URLを返しても1件に集約される（横断重複除去）。"""
        yf_like = lambda _inp: [_article("NVDA up", "http://x/1", summary="from yf")]  # noqa: E731
        gn_like = lambda _inp: [_article("NVDA up!", "http://x/1", summary="from gnews")]  # noqa: E731
        tool = NewsTool(fetchers=[yf_like, gn_like])
        out = await tool.execute(NewsInput(since=datetime(2020, 1, 1)))
        assert len(out.articles) == 1


class TestCasperIntegration:
    async def test_zero_articles_keeps_na(self) -> None:
        tool = NewsTool(fetchers=[lambda _inp: []])
        out = await tool.execute(NewsInput(tickers=["NVDA"]))
        assert out.articles == []
        verdict = casper("NVDA", news=out)
        assert verdict.verdict == "na"

    async def test_articles_unblock_casper(self) -> None:
        articles = [_article("NVDA 最高益 record", "http://x/1", summary="beat surge")]
        tool = NewsTool(fetchers=[lambda _inp: articles])
        out = await tool.execute(NewsInput(tickers=["NVDA"], since=datetime(2020, 1, 1)))
        assert isinstance(out, NewsOutput)
        assert len(out.articles) >= 1
        verdict = casper("NVDA", news=out)
        assert verdict.verdict != "na"
        assert verdict.verdict == "buy"
