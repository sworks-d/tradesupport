"""ダッシュボード API の単体テスト。get_dashboard_data を一時 DB で検証する。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.api.routes_dashboard import get_dashboard_data
from trading_agent.db import create_all, get_engine
from trading_agent.models.market_data import MarketDataCache
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.models.signals import BuySignal, SellSignal
from trading_agent.models.topics import Topic


def _seed(engine) -> None:
    with Session(engine) as session:
        session.add(
            PortfolioSnapshot(
                date=dt.date(2026, 5, 22),
                total_assets_jpy=104000.0,
                cash_jpy=32000.0,
                us_stocks_value_jpy=40000.0,
                jp_stocks_value_jpy=32000.0,
                satellite_value_jpy=12000.0,
                core_value_jpy=92000.0,
                usd_jpy_rate=152.0,
                holding_count=1,
                daily_pnl_jpy=300.0,
            )
        )
        session.add(
            Portfolio(
                ticker="7203",
                buy_date=dt.date(2026, 3, 10),
                buy_price=2800.0,
                qty=5,
                currency="JPY",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.15,
                stop_loss_pct=-0.08,
                target_date=dt.date(2026, 6, 8),
                thesis="PBR改革",
                status="active",
            )
        )
        session.add(
            MarketDataCache(
                ticker="7203",
                current_price=2950.0,
                open_price=2930.0,
                high_today=2960.0,
                low_today=2920.0,
                prev_close=2930.0,
                volume_today=1_000_000.0,
                price_change_today=20.0,
                price_change_pct_today=0.0068,
                market_status="closed",
            )
        )
        session.add(
            SellSignal(ticker="AAPL", signal_type="profit_taking", score=68, ai_confidence=0.7)
        )
        session.add(
            BuySignal(
                ticker="NVDA",
                score=82,
                fundamental_score=0.85,
                technical_score=0.78,
                news_sentiment_score=0.8,
                strategy_fit_score=0.9,
                ai_confidence=0.8,
                expected_return=0.32,
                win_rate=0.62,
                target_period_days=120,
                target_price=188.0,
                entry_price=142.0,
                stop_loss_price=126.0,
                strategy_category="中期",
                recommended_amount_jpy=25000,
            )
        )
        session.add(
            Topic(
                source="Bloomberg",
                source_url="https://example.com/a",
                category="macro",
                importance="high",
                headline="FRB 利下げ観測",
                summary="ハイテクに追い風",
                original_text_hash="h1",
                impact_text="",
                fetched_by="sample",
                importance_judged_by="rule",
            )
        )
        session.commit()


def test_dashboard_data_shape(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "dash.sqlite")
    create_all(engine)
    _seed(engine)

    data = get_dashboard_data(engine)

    assert data["summary"]["total_assets_jpy"] == 104000.0
    assert len(data["snapshots"]) == 1
    assert data["holdings"][0]["ticker"] == "7203"
    # 2950 vs 2800 → 約 +5.36%
    assert data["holdings"][0]["pnl_pct"] == round((2950.0 - 2800.0) / 2800.0, 4)
    assert data["sell_recommendations"][0]["ticker"] == "AAPL"
    assert data["buy_recommendations"][0]["ticker"] == "NVDA"
    assert data["topics"][0]["headline"] == "FRB 利下げ観測"


def test_dashboard_empty_db(tmp_path: Path) -> None:
    engine = get_engine(tmp_path / "empty.sqlite")
    create_all(engine)
    data = get_dashboard_data(engine)
    assert data["summary"]["total_assets_jpy"] == 0.0
    assert data["holdings"] == []
    assert data["sell_recommendations"] == []
