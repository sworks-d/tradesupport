"""load_universe（A-1）の単体テスト。yfinance は注入 fetcher でモック。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from scripts.load_universe import (
    ENTRIES,
    JP_TICKERS,
    US_TICKERS,
    build_rows,
    upsert_universe,
)
from trading_agent.db import create_all, get_engine
from trading_agent.models.universe import Universe


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "uni.sqlite")
    create_all(eng)
    return eng


def _meta(ticker: str, market: str) -> dict:
    return {
        "name": f"Co {ticker}",
        "name_en": f"Co {ticker}",
        "sector": "Technology",
        "industry": "Software",
        "market_cap": 1.0e9,
        "avg_volume_30d": 5.0e6,
    }


class TestCuratedLists:
    def test_us_are_alpha_tickers(self) -> None:
        assert US_TICKERS
        assert all(t.isalpha() and t.isupper() for t in US_TICKERS)

    def test_jp_are_numeric_codes(self) -> None:
        assert JP_TICKERS
        assert all(t.isdigit() for t in JP_TICKERS)

    def test_entries_tag_market(self) -> None:
        markets = {m for _, m in ENTRIES}
        assert markets == {"US", "JP"}
        assert len(ENTRIES) == len(US_TICKERS) + len(JP_TICKERS)


class TestBuildRows:
    def test_market_cap_jpy_conversion(self) -> None:
        rows = build_rows((("AAPL", "US"), ("7203", "JP")), _meta, usdjpy=150.0)
        by = {r.ticker: r for r in rows}
        assert by["AAPL"].market_cap_jpy == 1.0e9 * 150.0  # US は換算
        assert by["7203"].market_cap_jpy == 1.0e9  # JP は据え置き
        assert by["7203"].market == "JP"

    def test_skips_unfetchable(self) -> None:
        def fetcher(t: str, m: str) -> dict | None:
            return None if t == "BAD" else _meta(t, m)

        rows = build_rows((("AAPL", "US"), ("BAD", "US")), fetcher, usdjpy=150.0)
        assert {r.ticker for r in rows} == {"AAPL"}


class TestUpsert:
    def test_insert_then_idempotent_update(self, engine) -> None:
        assert upsert_universe(engine, build_rows((("AAPL", "US"),), _meta, 150.0)) == 1
        # 2回目：別実行を模して新規行を作る→重複せず更新される
        upsert_universe(engine, build_rows((("AAPL", "US"),), _meta, 150.0))
        with Session(engine) as session:
            all_rows = session.exec(select(Universe)).all()
            assert len(all_rows) == 1
            assert all_rows[0].is_active is True

    def test_screening_can_read_active_by_cap(self, engine) -> None:
        rows = build_rows((("AAPL", "US"), ("7203", "JP")), _meta, usdjpy=150.0)
        upsert_universe(engine, rows)
        with Session(engine) as session:
            ordered = session.exec(
                select(Universe).order_by(Universe.market_cap_jpy.desc())  # type: ignore[attr-defined]
            ).all()
            assert ordered[0].ticker == "AAPL"  # US 換算で大きい
