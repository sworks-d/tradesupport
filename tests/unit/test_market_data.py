"""market_data ツールの単体テスト（Task 1.1.2）。

yfinance はモック（注入した fetcher）に置き換え、ネットワークなしで検証する。
DB は一時 SQLite。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPErrorType, NetworkError
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataTool
from trading_agent.models.market_data import MarketDataCache


def _quote(current: float, prev: float, volume: float = 1000.0) -> dict[str, float]:
    return {
        "current_price": current,
        "open_price": prev,
        "high_today": current + 1,
        "low_today": prev - 1,
        "prev_close": prev,
        "volume_today": volume,
        "price_change_today": current - prev,
        "price_change_pct_today": (current - prev) / prev if prev else 0.0,
    }


class _CountingFetcher:
    """呼び出し回数を数えるモック fetcher。fail=True で NetworkError。"""

    def __init__(self, quotes: dict[str, dict[str, float]]) -> None:
        self.quotes = quotes
        self.calls = 0
        self.fail = False

    def __call__(self, tickers: list[str]) -> dict[str, dict[str, float]]:
        self.calls += 1
        if self.fail:
            raise NetworkError("simulated outage")
        return {t: self.quotes[t] for t in tickers if t in self.quotes}


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "md.sqlite")
    create_all(eng)
    return eng


class TestFetch:
    async def test_fetch_returns_price(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        out = await tool.execute(MarketDataInput(tickers=["AAPL"]))
        assert out.success is True
        assert out.data["AAPL"]["current_price"] == 110
        assert out.sources["AAPL"] == "yfinance"
        assert fetcher.calls == 1

    async def test_fields_projection(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        out = await tool.execute(MarketDataInput(tickers=["AAPL"], fields=["current_price"]))
        assert set(out.data["AAPL"]) == {"current_price"}

    async def test_db_cache_written(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        with Session(engine) as session:
            row = session.get(MarketDataCache, "AAPL")
        assert row is not None
        assert row.current_price == 110
        assert row.source == "yfinance"


class TestMemoryCache:
    async def test_cache_hit_skips_fetch(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        out2 = await tool.execute(MarketDataInput(tickers=["AAPL"]))
        assert fetcher.calls == 1  # 2回目はメモリから
        assert out2.sources["AAPL"] == "memory"

    async def test_use_cache_false_refetches(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        await tool.execute(MarketDataInput(tickers=["AAPL"], use_cache=False))
        assert fetcher.calls == 2

    async def test_ttl_zero_always_refetches(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, cache_ttl_seconds=0, fetcher=fetcher)
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        assert fetcher.calls == 2


class TestFallback:
    async def test_db_fallback_on_failure(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        tool.backoff_base = 0.0  # リトライ待ちを無くす
        # まず成功させて DB キャッシュを作る
        await tool.execute(MarketDataInput(tickers=["AAPL"]))
        # 以降は live 失敗 → DB フォールバック（use_cache=False でメモリを使わせない）
        fetcher.fail = True
        out = await tool.execute(MarketDataInput(tickers=["AAPL"], use_cache=False))
        assert out.success is True
        assert out.sources["AAPL"] == "db_cache"
        assert out.data["AAPL"]["current_price"] == 110
        assert out.metadata.get("degraded") is True

    async def test_failure_without_cache_returns_error(self, engine) -> None:
        fetcher = _CountingFetcher({"AAPL": _quote(110, 100)})
        tool = MarketDataTool(engine, fetcher=fetcher)
        tool.backoff_base = 0.0
        fetcher.fail = True
        out = await tool.execute(MarketDataInput(tickers=["AAPL"]))
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR


class TestHealthCheck:
    async def test_health_check_ok(self, engine) -> None:
        tool = MarketDataTool(engine, fetcher=_CountingFetcher({}))
        assert await tool.health_check() is True
