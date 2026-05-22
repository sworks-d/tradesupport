"""portfolio-builder の単体テスト（Task 1.4.5）。決定論的（LLM 不要）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.agents.context import AgentContext
from trading_agent.agents.portfolio_builder import (
    PortfolioBuilderAgent,
    PortfolioBuilderInput,
    is_core,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import BuySignal
from trading_agent.models.universe import Universe


def _buy(ticker: str, score: int, category: str) -> BuySignal:
    return BuySignal(
        ticker=ticker,
        score=score,
        fundamental_score=0.8,
        technical_score=0.8,
        news_sentiment_score=0.5,
        strategy_fit_score=0.7,
        ai_confidence=0.7,
        expected_return=0.2,
        win_rate=0.6,
        target_period_days=90,
        target_price=120.0,
        entry_price=100.0,
        stop_loss_price=92.0,
        strategy_category=category,
        recommended_amount_jpy=20000,
    )


def _ctx(tmp_path: Path) -> AgentContext:
    engine = get_engine(tmp_path / "pb.sqlite")
    create_all(engine)
    return AgentContext(host=MCPHost(), engine=engine, invocation_id="inv")


def test_is_core() -> None:
    assert is_core("中期") is True
    assert is_core("中期-長期") is True
    assert is_core("長期") is False


class TestInitial:
    async def test_splits_core_satellite(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        with Session(ctx.engine) as s:
            s.add(_buy("AAPL", 85, "中期"))
            s.add(_buy("XXXX", 70, "長期"))
            s.commit()
        out = await PortfolioBuilderAgent(ctx).execute(
            PortfolioBuilderInput(invocation_id="inv", mode="initial", available_cash_jpy=100000)
        )
        recs = {r["ticker"]: r for r in out.recommendations}
        assert recs["AAPL"]["target_amount_jpy"] == 80000  # コア枠 80%
        assert recs["XXXX"]["target_amount_jpy"] == 20000  # サテライト枠 20%


class TestReview:
    async def test_warns_sector_concentration(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        with Session(ctx.engine) as s:
            for ticker in ["AAPL", "MSFT"]:
                s.add(
                    Portfolio(
                        ticker=ticker,
                        buy_date=dt.date(2026, 3, 1),
                        buy_price=100.0,
                        qty=10,
                        currency="USD",
                        strategy_category="中期",
                        target_period_days=90,
                        target_pct=0.2,
                        stop_loss_pct=-0.08,
                        target_date=dt.date(2026, 6, 1),
                        thesis="t",
                        status="active",
                    )
                )
                s.add(
                    Universe(
                        ticker=ticker,
                        name=ticker,
                        market="US",
                        sector="Tech",
                        market_cap=1e12,
                        market_cap_jpy=1e14,
                        avg_volume_30d=1e7,
                    )
                )
            s.commit()
        out = await PortfolioBuilderAgent(ctx).execute(
            PortfolioBuilderInput(invocation_id="inv", mode="review")
        )
        assert any("セクター集中" in w for w in out.warnings)
