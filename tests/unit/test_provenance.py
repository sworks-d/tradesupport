"""B1: 出典(source_refs)・時点(data_asof) の最小検証。

防御層（B3）が機械照合するための土台が、後方互換を保ったまま全ツール基底に入り、
market_data に通っていることを確認する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPToolOutput, SourceRef
from trading_agent.mcp_tools.disclosure import DisclosureInput, DisclosureTool
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, FundamentalsTool
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataTool
from trading_agent.mcp_tools.news import NewsInput, NewsTool
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsTool


def test_base_output_provenance_defaults_backward_compatible() -> None:
    out = MCPToolOutput(success=True)
    assert out.data_asof is None
    assert out.source_refs == []


def test_source_ref_fields() -> None:
    ref = SourceRef(source="yfinance", ref="AAPL", as_of=datetime(2026, 5, 22))
    assert ref.source == "yfinance"
    assert ref.ref == "AAPL"
    assert ref.note is None


def _quote(current: float, prev: float) -> dict[str, float]:
    return {
        "current_price": current,
        "open_price": prev,
        "high_today": current + 1,
        "low_today": prev - 1,
        "prev_close": prev,
        "volume_today": 1000.0,
        "price_change_today": current - prev,
        "price_change_pct_today": (current - prev) / prev if prev else 0.0,
    }


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "prov.sqlite")
    create_all(eng)
    return eng


async def test_market_data_live_sets_provenance(engine) -> None:
    tool = MarketDataTool(engine, fetcher=lambda ts: {t: _quote(110, 100) for t in ts})
    out = await tool.execute(MarketDataInput(tickers=["AAPL"]))
    assert out.success is True
    assert out.data_asof is not None
    assert any(r.source == "yfinance" and r.ref == "AAPL" for r in out.source_refs)


async def test_fundamentals_sets_provenance_with_fiscal_period() -> None:
    tool = FundamentalsTool(fetcher=lambda t: ({"per": 15.0, "eps": 2.0}, "2026-03-31"))
    out = await tool.execute(FundamentalsInput(ticker="AAPL", fields=["per", "eps"]))
    assert out.data_asof is not None
    assert out.source_refs
    ref = out.source_refs[0]
    assert ref.as_of == datetime(2026, 3, 31)  # fiscal_period(ISO)を時点として保持
    assert "fiscal_period=2026-03-31" in (ref.note or "")


async def test_technicals_provenance_is_computed() -> None:
    closes = [float(i) for i in range(1, 80)]  # 指標計算に十分な系列
    tool = TechnicalsTool(history_provider=lambda t, d: closes)
    out = await tool.execute(TechnicalsInput(ticker="AAPL"))
    assert out.data_asof is not None
    assert any(r.source == "computed" and r.ref == "AAPL" for r in out.source_refs)


async def test_market_data_two_source_reconcile(engine) -> None:
    primary = lambda ts: {t: _quote(100.0, 99.0) for t in ts}  # noqa: E731
    # 2ソース目が ±0.5% 内 → ok
    sec_ok = lambda ts: {t: _quote(100.3, 99.0) for t in ts}  # noqa: E731
    out_ok = await MarketDataTool(engine, fetcher=primary, reconcile_fetcher=sec_ok).execute(
        MarketDataInput(tickers=["AAPL"])
    )
    assert out_ok.reconciliation["AAPL"] == "ok"

    # 2ソース目が大きくズレ → mismatch ＋ メタにフラグ
    sec_bad = lambda ts: {t: _quote(105.0, 99.0) for t in ts}  # noqa: E731
    out_bad = await MarketDataTool(engine, fetcher=primary, reconcile_fetcher=sec_bad).execute(
        MarketDataInput(tickers=["MSFT"])
    )
    assert out_bad.reconciliation["MSFT"] == "mismatch"
    assert "MSFT" in out_bad.metadata.get("price_mismatch", [])


async def test_market_data_single_source_when_no_reconciler(engine) -> None:
    tool = MarketDataTool(engine, fetcher=lambda ts: {t: _quote(100.0, 99.0) for t in ts})
    out = await tool.execute(MarketDataInput(tickers=["AAPL"]))
    assert out.reconciliation["AAPL"] == "single"


async def test_news_provenance_per_article() -> None:
    def fake(_inp: NewsInput) -> list[dict]:
        return [
            {
                "title": "t",
                "summary": "",
                "source": "Bloomberg",
                "url": "https://x/1",
                "published_at": "2026-05-20T00:00:00",
            }
        ]

    tool = NewsTool(fetchers=[fake])
    out = await tool.execute(NewsInput(since=datetime(2026, 5, 1)))
    assert out.data_asof == datetime(2026, 5, 20)
    assert any(r.ref == "https://x/1" and r.source == "Bloomberg" for r in out.source_refs)


async def test_disclosure_provenance_per_item() -> None:
    def fake(_inp: DisclosureInput) -> list[dict]:
        return [
            {
                "ticker": "7203",
                "title": "x",
                "url": "https://e/1",
                "published_at": "2026-05-19T00:00:00",
                "source": "TDnet",
            }
        ]

    tool = DisclosureTool(fetchers=[fake])
    out = await tool.execute(DisclosureInput(since=datetime(2026, 5, 1)))
    assert out.data_asof == datetime(2026, 5, 19)
    assert any(r.ref == "https://e/1" and r.source == "TDnet" for r in out.source_refs)
