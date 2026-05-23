"""fundamentals ツールの単体テスト（Task 1.1.3）。

yfinance は注入 fetcher でモックし、ネットワーク非依存で検証する。
"""

from __future__ import annotations

from trading_agent.mcp_tools.base import DataNotFoundError, MCPErrorType, NetworkError
from trading_agent.mcp_tools.fundamentals import (
    _YF_FIELD_MAP,
    FundamentalsInput,
    FundamentalsTool,
    _default_fields,
    is_jp_ticker,
    primary_source_url,
)

_VALUES = {
    "eps": 6.1,
    "per": 28.0,
    "pbr": 45.0,
    "revenue_growth": 0.08,
    "operating_margin": 0.30,
    "revenue": 3.9e11,
}


class _CountingFetcher:
    def __init__(self, values: dict[str, float], fiscal_period: str = "2025-03-31") -> None:
        self.values = values
        self.fiscal_period = fiscal_period
        self.calls = 0
        self.fail = False
        self.notfound = False

    def __call__(self, ticker: str) -> tuple[dict[str, float], str]:
        self.calls += 1
        if self.fail:
            raise NetworkError("simulated outage")
        if self.notfound:
            raise DataNotFoundError("unknown ticker")
        return dict(self.values), self.fiscal_period


class TestFieldCoverage:
    """P1-5：MELCHIOR の多面評価に必要な指標が既定で取得されること。"""

    def test_defaults_cover_growth_profit_health(self) -> None:
        defaults = set(_default_fields())
        # 成長・収益性・健全性・CF を網羅
        for f in (
            "revenue_growth",
            "earnings_growth",
            "operating_margin",
            "profit_margin",
            "roe",
            "debt_to_equity",
            "current_ratio",
            "free_cashflow",
        ):
            assert f in defaults, f

    def test_field_map_has_yfinance_keys(self) -> None:
        assert _YF_FIELD_MAP["debt_to_equity"] == "debtToEquity"
        assert _YF_FIELD_MAP["free_cashflow"] == "freeCashflow"
        assert _YF_FIELD_MAP["earnings_growth"] == "earningsGrowth"

    async def test_new_fields_projected_when_present(self) -> None:
        values = {
            "revenue_growth": 0.2,
            "roe": 0.18,
            "debt_to_equity": 40.0,
            "free_cashflow": 1.0e9,
        }
        tool = FundamentalsTool(fetcher=_CountingFetcher(values))
        out = await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert set(out.data) == set(values)


class TestMarketDetection:
    def test_jp_suffix(self) -> None:
        assert is_jp_ticker("7203.T") is True

    def test_jp_digits(self) -> None:
        assert is_jp_ticker("7203") is True

    def test_us(self) -> None:
        assert is_jp_ticker("AAPL") is False

    def test_source_url_us_is_edgar(self) -> None:
        assert "sec.gov" in primary_source_url("AAPL")

    def test_source_url_jp_is_edinet(self) -> None:
        assert "edinet" in primary_source_url("7203.T")


class TestExecute:
    async def test_returns_requested_fields(self) -> None:
        tool = FundamentalsTool(fetcher=_CountingFetcher(_VALUES))
        out = await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert out.success is True
        assert set(out.data) == {"eps", "per", "pbr", "revenue_growth", "operating_margin"}
        assert out.data["per"] == 28.0

    async def test_source_url_and_fiscal_period(self) -> None:
        tool = FundamentalsTool(fetcher=_CountingFetcher(_VALUES, fiscal_period="2025-03-31"))
        out = await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert out.source_url is not None and "sec.gov" in out.source_url  # type: ignore[union-attr]
        assert out.fiscal_period == "2025-03-31"

    async def test_custom_fields(self) -> None:
        tool = FundamentalsTool(fetcher=_CountingFetcher(_VALUES))
        out = await tool.execute(FundamentalsInput(ticker="AAPL", fields=["eps", "revenue"]))
        assert set(out.data) == {"eps", "revenue"}

    async def test_jp_metadata(self) -> None:
        tool = FundamentalsTool(fetcher=_CountingFetcher(_VALUES))
        out = await tool.execute(FundamentalsInput(ticker="7203.T"))
        assert out.metadata["market"] == "JP"
        assert "edinet" in (out.source_url or "")


class TestMemoryCache:
    async def test_cache_hit_skips_fetch(self) -> None:
        fetcher = _CountingFetcher(_VALUES)
        tool = FundamentalsTool(fetcher=fetcher)
        await tool.execute(FundamentalsInput(ticker="AAPL"))
        out2 = await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert fetcher.calls == 1
        assert out2.metadata["source"] == "memory"

    async def test_ttl_zero_refetches(self) -> None:
        fetcher = _CountingFetcher(_VALUES)
        tool = FundamentalsTool(cache_ttl_seconds=0, fetcher=fetcher)
        await tool.execute(FundamentalsInput(ticker="AAPL"))
        await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert fetcher.calls == 2


class TestErrors:
    async def test_not_found_is_success_with_no_data(self) -> None:
        fetcher = _CountingFetcher(_VALUES)
        fetcher.notfound = True
        tool = FundamentalsTool(fetcher=fetcher)
        out = await tool.execute(FundamentalsInput(ticker="ZZZZ"))
        assert out.success is True
        assert out.error_type == MCPErrorType.DATA_NOT_FOUND

    async def test_network_failure_returns_error(self) -> None:
        fetcher = _CountingFetcher(_VALUES)
        fetcher.fail = True
        tool = FundamentalsTool(fetcher=fetcher)
        tool.backoff_base = 0.0
        out = await tool.execute(FundamentalsInput(ticker="AAPL"))
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
        assert fetcher.calls == 3  # max_attempts
