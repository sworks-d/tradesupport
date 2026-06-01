"""発注リスト生成のテスト（v2.10 楽天かぶミニ運用向け）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.reporting.order_list import (
    build_order_items,
    generate_order_list,
    render_html,
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "orders.sqlite")
    create_all(eng)
    today = dt.date(2026, 5, 31)
    with Session(eng) as s:
        # universe
        s.add(Universe(ticker="3697", name="SHIFT", market="JP",
                       sector="Technology", market_cap=179e9, market_cap_jpy=179e9,
                       avg_volume_30d=500_000, is_active=True))
        s.add(Universe(ticker="4587", name="PeptiDream", market="JP",
                       sector="Healthcare", market_cap=142e9, market_cap_jpy=142e9,
                       avg_volume_30d=300_000, is_active=True))
        # awaiting decisions
        s.add(Decision(date=today, ticker="3697", action="buy",
                       status="awaiting", gendo_stance="要検討", stop_pct=0.10))
        s.add(Decision(date=today, ticker="4587", action="buy",
                       status="awaiting", gendo_stance="静観", stop_pct=0.12))
        s.commit()
    return eng


class TestBuildOrderItems:
    def test_returns_empty_when_no_decisions(self, tmp_path: Path):
        eng = get_engine(tmp_path / "empty.sqlite")
        create_all(eng)
        items = build_order_items(eng, date=dt.date(2026, 5, 31), available_jpy=100_000)
        assert items == []

    def test_creates_one_item_per_awaiting(self, engine):
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            side_effect=lambda t: {"3697": 700.0, "4587": 1100.0}.get(t),
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        assert len(items) == 2
        tickers = {i.ticker for i in items}
        assert tickers == {"3697", "4587"}

    def test_recommended_shares_within_budget(self, engine):
        """1 ポジション 10% = ¥10k で買える株数。"""
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            side_effect=lambda t: {"3697": 700.0, "4587": 1100.0}.get(t),
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        # SHIFT 700 円 → 1 ポジション ¥50k (1/2) で 71 株? いや per_pos = max(10%, 1/n) = max(10k, 50k) = 50k
        # 実装: per_pos_budget = max(available_jpy * 0.10, available_jpy / n) = max(10k, 50k) = 50k
        # 累積制御で 1 件目 50k → SHIFT 50000/700 = 71 株 → ¥49,700
        for item in items:
            assert item.estimated_cost_jpy <= 100_000
            assert item.recommended_shares >= 0

    def test_cumulative_within_total_budget(self, engine):
        """累積コストが予算を超えない。"""
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            side_effect=lambda t: {"3697": 700.0, "4587": 1100.0}.get(t),
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        total = sum(i.estimated_cost_jpy for i in items)
        assert total <= 100_000

    def test_stop_loss_price_calculated(self, engine):
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            side_effect=lambda t: 700.0,
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        for item in items:
            if item.current_price:
                # stop_loss_price = price * (1 - stop_pct)
                assert item.stop_loss_price < item.current_price

    def test_price_unavailable_results_in_zero_shares(self, engine):
        """価格取れない銘柄は推奨株数 0（推測しない）。"""
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            return_value=None,
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        for item in items:
            assert item.recommended_shares == 0
            assert item.estimated_cost_jpy == 0.0


class TestRenderHtml:
    def test_empty_items_shows_no_orders(self):
        html = render_html([], date=dt.date(2026, 5, 31), available_jpy=100_000)
        assert "本日の発注候補はありません" in html

    def test_items_show_in_html(self, engine):
        with patch(
            "trading_agent.reporting.order_list._fetch_price",
            side_effect=lambda t: 700.0,
        ):
            items = build_order_items(engine, date=dt.date(2026, 5, 31), available_jpy=100_000)
        html = render_html(items, date=dt.date(2026, 5, 31), available_jpy=100_000)
        # 各 ticker が出てる
        for item in items:
            assert item.ticker in html
        # ヘッダー金額が出てる
        assert "¥100,000" in html


class TestGenerateOrderList:
    def test_generates_file(self, engine, tmp_path: Path):
        out_dir = tmp_path / "orders"
        path = generate_order_list(engine, date=dt.date(2026, 5, 31), output_dir=out_dir)
        assert path.exists()
        assert path.suffix == ".html"
        content = path.read_text(encoding="utf-8")
        assert "<html" in content
