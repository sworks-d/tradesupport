"""theme_context（screening のテーマスコア用素材集め）の単体テスト。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.agents.theme_context import (
    SectorReturnCache,
    fetch_30d_return_yfinance,
    keyword_match_counts,
    market_etf,
    sector_etf,
)
from trading_agent.db import create_all, get_engine
from trading_agent.models.topics import Topic


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "theme.sqlite")
    create_all(eng)
    return eng


def _topic(
    *,
    days_ago: int,
    affected_tickers: list[str],
    headline: str = "h",
) -> Topic:
    return Topic(
        collected_at=dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days_ago),
        source="src",
        source_url="https://example.com",
        category="market",
        importance="medium",
        headline=headline,
        summary="s",
        original_text_hash=f"h-{headline}-{days_ago}",
        affected_tickers=affected_tickers,
        impact_text="x",
        linked_decisions=[],
        fetched_by="news",
        importance_judged_by="rule",
        is_archived=False,
    )


def test_sector_etf_maps_jp_and_us() -> None:
    assert sector_etf("JP", "Technology") == "1626.T"
    assert sector_etf("US", "Technology") == "XLK"
    # マッピング無しは None（screening 側でスキップ）
    assert sector_etf("JP", "Unknown") is None


def test_market_etf_distinguishes_jp_us() -> None:
    assert market_etf("JP") == "1306.T"
    assert market_etf("US") == "SPY"


def test_sector_return_cache_dedupes_calls() -> None:
    calls: list[str] = []

    def fake_fetcher(sym: str) -> float:
        calls.append(sym)
        return 0.05  # 5%

    cache = SectorReturnCache(fetcher=fake_fetcher)
    # 同じシンボルを複数回引いても fetcher は1回しか呼ばれない
    assert cache.get("XLK") == 0.05
    assert cache.get("XLK") == 0.05
    assert calls == ["XLK"]


def test_market_and_sector_return_route_correctly() -> None:
    def fake(sym: str) -> float | None:
        return {"1306.T": 0.02, "1626.T": 0.08, "SPY": 0.01, "XLK": 0.10}.get(sym)

    cache = SectorReturnCache(fetcher=fake)
    assert cache.market_return("JP") == 0.02
    assert cache.market_return("US") == 0.01
    assert cache.sector_return("JP", "Technology") == 0.08
    assert cache.sector_return("US", "Technology") == 0.10
    # 未マップセクター → None
    assert cache.sector_return("JP", "Aerospace") is None


def test_keyword_match_counts_counts_recent_mentions(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as s:
        s.add(_topic(days_ago=1, affected_tickers=["8035", "6857"], headline="a"))
        s.add(_topic(days_ago=2, affected_tickers=["8035"], headline="b"))
        s.add(_topic(days_ago=3, affected_tickers=["6857"], headline="c"))
        # 8日前 → window 外（7日 lookback）
        s.add(_topic(days_ago=8, affected_tickers=["8035"], headline="old"))
        s.commit()

    counts = keyword_match_counts(engine, ["8035", "6857", "4452"], lookback_days=7)
    assert counts["8035"] == 2  # 1日前 + 2日前（8日前は除外）
    assert counts["6857"] == 2  # 1日前 + 3日前
    assert counts["4452"] == 0


def test_keyword_match_counts_returns_zero_when_no_topics(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    counts = keyword_match_counts(engine, ["8035"], lookback_days=7)
    assert counts == {"8035": 0}


def test_fetch_30d_return_handles_failure_gracefully() -> None:
    # 実際のネット呼び出しはせず、ありえないシンボルで None 返却を確認
    # （yfinance が空 df を返すか例外を投げる）
    result = fetch_30d_return_yfinance("__NOT_A_REAL_SYMBOL__")
    assert result is None
