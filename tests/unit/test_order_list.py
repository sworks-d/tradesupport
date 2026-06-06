"""発注リスト生成のテスト（v2.10 楽天かぶミニ運用向け）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, col, select

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


def _add_portfolio(engine, ticker: str, *, qty: int, buy_price: float, broker_mode: str):
    from trading_agent.models.portfolio import Portfolio

    with Session(engine) as s:
        s.add(
            Portfolio(
                ticker=ticker,
                buy_date=dt.date(2026, 5, 1),
                buy_price=buy_price,
                qty=qty,
                currency="JPY",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.20,
                stop_loss_pct=0.10,
                target_date=dt.date(2026, 8, 1),
                thesis="test",
                status="active",
                broker_mode=broker_mode,
            )
        )
        s.commit()


class TestSellItems:
    """A-2: live 売り出口（発注リストに売り指示が出る）。"""

    def test_build_sell_items_from_approved_sell(self, engine):
        from trading_agent.reporting.order_list import build_sell_items

        _add_portfolio(engine, "3697", qty=50, buy_price=1000.0, broker_mode="live")
        with Session(engine) as s:
            s.add(
                Decision(
                    date=dt.date(2026, 5, 31), ticker="3697", action="sell_loss",
                    status="approved", gendo_stance="撤退",
                    thesis_at_decision="trailing stop 到達", stop_pct=0.10,
                    entry_price=1000.0,
                )
            )
            s.commit()
        with patch(
            "trading_agent.reporting.order_list._fetch_price", return_value=850.0
        ):
            items = build_sell_items(engine, broker_mode="live")
        assert len(items) == 1
        it = items[0]
        assert it.ticker == "3697"
        assert it.qty == 50
        assert it.action == "sell_loss"
        assert it.pnl_pct is not None and it.pnl_pct < 0

    def test_paper_holding_not_in_live_sell_list(self, engine):
        """paper 保有は live の発注リストに出ない（broker_mode 分離）。"""
        from trading_agent.reporting.order_list import build_sell_items

        _add_portfolio(engine, "3697", qty=50, buy_price=1000.0, broker_mode="paper")
        with Session(engine) as s:
            s.add(
                Decision(
                    date=dt.date(2026, 5, 31), ticker="3697", action="sell_loss",
                    status="approved", gendo_stance="撤退", stop_pct=0.10,
                )
            )
            s.commit()
        with patch(
            "trading_agent.reporting.order_list._fetch_price", return_value=850.0
        ):
            items = build_sell_items(engine, broker_mode="live")
        assert items == []

    def test_render_includes_sell_section(self):
        from trading_agent.reporting.order_list import SellOrderItem

        sell = [
            SellOrderItem(
                decision_id=1, ticker="3697", name="SHIFT", qty=50,
                action="sell_loss", stance="撤退", reason="trailing stop 到達",
                current_price=850.0, buy_price=1000.0, pnl_pct=-0.15, pnl_jpy=-7500.0,
            )
        ]
        html = render_html(
            [], date=dt.date(2026, 5, 31), available_jpy=100_000, sell_items=sell
        )
        assert "今日の売り" in html
        assert "3697" in html
        assert "損切り" in html
        assert "全部売却" in html

    def test_e2e_trailing_to_sell_list(self, engine):
        """E2E: trailing_check が approved sell を作り → 発注リストに売りが出る。"""
        from trading_agent.portfolio.trailing_check import run_trailing_check
        from trading_agent.reporting.order_list import build_sell_items

        _add_portfolio(engine, "3697", qty=30, buy_price=1000.0, broker_mode="live")
        with patch(
            "trading_agent.portfolio.earnings_guard.fetch_next_earnings_date",
            return_value=None,
        ):
            res = run_trailing_check(
                engine, broker_mode="live", today=dt.date(2026, 5, 31),
                price_lookup={"3697": 700.0},  # entry 1000 比 -30% → stop 発火
            )
        assert len(res["stop_triggered"]) == 1
        assert res["stop_triggered"][0]["action"] == "sell_loss"
        with patch(
            "trading_agent.reporting.order_list._fetch_price", return_value=700.0
        ):
            items = build_sell_items(engine, broker_mode="live")
        assert len(items) == 1
        assert items[0].ticker == "3697"
        assert items[0].qty == 30
        assert items[0].action == "sell_loss"


class TestSellCompletion:
    """A-2b: 手動売却の DB 反映（close_sold_decision）。"""

    def test_close_sold_decision_closes_and_records(self, engine):
        from trading_agent.models.portfolio import Portfolio
        from trading_agent.portfolio.paper_exec import close_sold_decision

        _add_portfolio(engine, "3697", qty=50, buy_price=1000.0, broker_mode="live")
        with Session(engine) as s:
            d = Decision(
                date=dt.date(2026, 5, 31), ticker="3697", action="sell_loss",
                status="approved", gendo_stance="撤退", stop_pct=0.10, entry_price=1000.0,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            dec_id = d.id

        res = close_sold_decision(engine, dec_id, closed_price=850.0, broker_mode="live")
        assert "error" not in res
        assert res["qty"] == 50
        assert res["pnl_jpy"] == (850.0 - 1000.0) * 50
        assert abs(res["actual_return"] - (-0.15)) < 1e-9
        with Session(engine) as s:
            d2 = s.get(Decision, dec_id)
            assert d2.status == "ordered"  # 評価対象へ
            assert d2.actual_return is not None
            ports = list(s.exec(select(Portfolio).where(col(Portfolio.ticker) == "3697")))
            assert all(p.status == "closed" for p in ports)
            assert all(p.closed_reason == "sell_loss" for p in ports)

    def test_close_sold_no_holding_errors(self, engine):
        from trading_agent.portfolio.paper_exec import close_sold_decision

        with Session(engine) as s:
            d = Decision(
                date=dt.date(2026, 5, 31), ticker="3697", action="sell_loss",
                status="approved", gendo_stance="撤退", stop_pct=0.10,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            dec_id = d.id
        res = close_sold_decision(engine, dec_id, closed_price=850.0, broker_mode="live")
        assert res.get("error") == "no_active_holding"

    def test_close_sold_rejects_buy_decision(self, engine):
        from trading_agent.portfolio.paper_exec import close_sold_decision

        with Session(engine) as s:
            d = Decision(
                date=dt.date(2026, 5, 31), ticker="3697", action="buy",
                status="awaiting", gendo_stance="要検討", stop_pct=0.10,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            dec_id = d.id
        res = close_sold_decision(engine, dec_id, closed_price=850.0, broker_mode="live")
        assert res.get("error") == "not_a_sell"

    def test_build_sell_items_weighted_avg_buy_price(self, engine):
        """P2（codex）: 複数保有は加重平均取得単価で損益表示。"""
        from trading_agent.reporting.order_list import build_sell_items

        # 100 株 @1000 + 100 株 @1200 → 加重平均 1100
        _add_portfolio(engine, "3697", qty=100, buy_price=1000.0, broker_mode="live")
        _add_portfolio(engine, "3697", qty=100, buy_price=1200.0, broker_mode="live")
        with Session(engine) as s:
            s.add(
                Decision(
                    date=dt.date(2026, 5, 31), ticker="3697", action="sell_profit",
                    status="approved", gendo_stance="利確", stop_pct=0.10,
                )
            )
            s.commit()
        with patch(
            "trading_agent.reporting.order_list._fetch_price", return_value=1300.0
        ):
            items = build_sell_items(engine, broker_mode="live")
        assert len(items) == 1
        it = items[0]
        assert it.qty == 200
        assert it.buy_price == 1100.0  # 加重平均（先頭 1000 ではない）
        assert abs(it.pnl_pct - (1300.0 - 1100.0) / 1100.0) < 1e-9


class TestGenerateOrderList:
    def test_generates_file(self, engine, tmp_path: Path):
        out_dir = tmp_path / "orders"
        path = generate_order_list(engine, date=dt.date(2026, 5, 31), output_dir=out_dir)
        assert path.exists()
        assert path.suffix == ".html"
        content = path.read_text(encoding="utf-8")
        assert "<html" in content
