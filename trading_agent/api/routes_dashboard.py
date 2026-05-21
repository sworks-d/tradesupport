"""ダッシュボード用 API（PANEL_SPECS の各パネルにデータを供給）。

DB（portfolio / signals / topics / snapshots 等）を集約して 1 レスポンスで返す。
エージェント未稼働でも、サンプル投入（scripts/seed_sample_data.py）でパネルが描画できる。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.config import get_settings
from trading_agent.db import get_engine
from trading_agent.models.market_data import MarketDataCache
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.models.signals import BuySignal, SellSignal
from trading_agent.models.topics import Topic

router = APIRouter(prefix="/api")


def _holding_view(p: Portfolio, price: MarketDataCache | None) -> dict[str, Any]:
    current = price.current_price if price is not None else p.buy_price
    pnl_pct = (current - p.buy_price) / p.buy_price if p.buy_price else 0.0
    return {
        "ticker": p.ticker,
        "buy_date": p.buy_date,
        "buy_price": p.buy_price,
        "current_price": current,
        "qty": p.qty,
        "currency": p.currency,
        "strategy_category": p.strategy_category,
        "target_pct": p.target_pct,
        "stop_loss_pct": p.stop_loss_pct,
        "target_date": p.target_date,
        "status": p.status,
        "thesis": p.thesis,
        "pnl_pct": round(pnl_pct, 4),
    }


def get_dashboard_data(engine: Engine) -> dict[str, Any]:
    """全パネルのデータを 1 つの dict に集約する。"""
    with Session(engine) as session:
        snapshots = list(
            session.exec(select(PortfolioSnapshot).order_by(col(PortfolioSnapshot.date)))
        )
        holdings = list(session.exec(select(Portfolio).where(Portfolio.status == "active")))
        sells = list(
            session.exec(
                select(SellSignal)
                .where(col(SellSignal.is_active))
                .order_by(col(SellSignal.score).desc())
            )
        )
        buys = list(
            session.exec(
                select(BuySignal)
                .where(col(BuySignal.is_active))
                .order_by(col(BuySignal.score).desc())
            )
        )
        topics = list(session.exec(select(Topic).order_by(col(Topic.collected_at).desc())))[:10]
        prices = {row.ticker: row for row in session.exec(select(MarketDataCache))}

    latest = snapshots[-1] if snapshots else None
    summary = {
        "total_assets_jpy": latest.total_assets_jpy if latest else 0.0,
        "cash_jpy": latest.cash_jpy if latest else 0.0,
        "daily_pnl_jpy": latest.daily_pnl_jpy if latest else 0.0,
        "holding_count": latest.holding_count if latest else len(holdings),
        "core_value_jpy": latest.core_value_jpy if latest else 0.0,
        "satellite_value_jpy": latest.satellite_value_jpy if latest else 0.0,
    }

    return {
        "summary": summary,
        "snapshots": [{"date": s.date, "total_assets_jpy": s.total_assets_jpy} for s in snapshots],
        "holdings": [_holding_view(p, prices.get(p.ticker)) for p in holdings],
        "sell_recommendations": [
            {
                "ticker": s.ticker,
                "signal_type": s.signal_type,
                "score": s.score,
                "reasons": s.reasons,
                "recommended_action": s.recommended_action,
            }
            for s in sells
        ],
        "buy_recommendations": [
            {
                "ticker": b.ticker,
                "score": b.score,
                "expected_return": b.expected_return,
                "target_period_days": b.target_period_days,
                "strategy_category": b.strategy_category,
                "entry_price": b.entry_price,
                "target_price": b.target_price,
                "scenarios": b.scenarios,
                "recommended_amount_jpy": b.recommended_amount_jpy,
            }
            for b in buys
        ],
        "topics": [
            {
                "headline": t.headline,
                "summary": t.summary,
                "category": t.category,
                "importance": t.importance,
                "source": t.source,
                "source_url": t.source_url,
                "affected_tickers": t.affected_tickers,
            }
            for t in topics
        ],
    }


@router.get("/dashboard")
def dashboard() -> dict[str, Any]:
    """ダッシュボード全パネルのデータを返す。"""
    engine = get_engine(get_settings().db_path)
    return get_dashboard_data(engine)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
