"""disclosure ツールの単体テスト（Task 1.1.5）。fetcher 注入でネットワーク非依存。"""

from __future__ import annotations

from datetime import datetime

from trading_agent.mcp_tools.base import MCPErrorType, NetworkError
from trading_agent.mcp_tools.disclosure import DisclosureInput, DisclosureTool


def _disc(ticker: str, title: str, url: str, published: str = "2026-05-22T09:00:00") -> dict:
    return {
        "ticker": ticker,
        "title": title,
        "url": url,
        "published_at": published,
        "source": "TDnet",
    }


class TestExecute:
    async def test_returns_all_when_no_filter(self) -> None:
        items = [_disc("7203", "決算速報", "http://t/1"), _disc("6758", "業績修正", "http://t/2")]
        tool = DisclosureTool(fetchers=[lambda _i: items])
        out = await tool.execute(DisclosureInput(since=datetime(2020, 1, 1)))
        assert len(out.disclosures) == 2

    async def test_ticker_filter_by_field(self) -> None:
        items = [_disc("7203", "決算速報", "http://t/1"), _disc("6758", "業績修正", "http://t/2")]
        tool = DisclosureTool(fetchers=[lambda _i: items])
        out = await tool.execute(DisclosureInput(tickers=["7203"], since=datetime(2020, 1, 1)))
        assert {d["ticker"] for d in out.disclosures} == {"7203"}

    async def test_ticker_filter_by_title(self) -> None:
        items = [_disc("", "トヨタ(7203) 配当", "http://t/1"), _disc("", "他社", "http://t/2")]
        tool = DisclosureTool(fetchers=[lambda _i: items])
        out = await tool.execute(DisclosureInput(tickers=["7203"], since=datetime(2020, 1, 1)))
        assert {d["url"] for d in out.disclosures} == {"http://t/1"}

    async def test_since_filter(self) -> None:
        old = _disc("7203", "旧", "http://t/old", published="2020-01-01T00:00:00")
        new = _disc("7203", "新", "http://t/new", published="2026-05-22T00:00:00")
        tool = DisclosureTool(fetchers=[lambda _i: [old, new]])
        out = await tool.execute(DisclosureInput(since=datetime(2026, 1, 1)))
        assert {d["url"] for d in out.disclosures} == {"http://t/new"}

    async def test_url_dedupe(self) -> None:
        items = [_disc("7203", "A", "http://t/1"), _disc("7203", "B", "http://t/1")]
        tool = DisclosureTool(fetchers=[lambda _i: items])
        out = await tool.execute(DisclosureInput(since=datetime(2020, 1, 1)))
        assert len(out.disclosures) == 1

    async def test_graceful_degradation(self) -> None:
        def boom(_i: DisclosureInput) -> list[dict]:
            raise NetworkError("tdnet down")

        def ok(_i: DisclosureInput) -> list[dict]:
            return [_disc("7203", "決算", "http://t/1")]

        tool = DisclosureTool(fetchers=[boom, ok])
        out = await tool.execute(DisclosureInput(since=datetime(2020, 1, 1)))
        assert out.success is True
        assert out.metadata["source_failures"] == 1
        assert len(out.disclosures) == 1

    async def test_all_fail(self) -> None:
        def boom(_i: DisclosureInput) -> list[dict]:
            raise NetworkError("down")

        tool = DisclosureTool(fetchers=[boom])
        tool.backoff_base = 0.0
        out = await tool.execute(DisclosureInput())
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
