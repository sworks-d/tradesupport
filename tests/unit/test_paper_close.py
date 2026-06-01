"""paper_close_approved の単体テスト（v2.10 Phase 1A-Step2 修正・致命 1）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.paper_exec import paper_close_approved


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "close.sqlite")
    create_all(eng)
    return eng


def _add_universe(eng, ticker: str) -> None:
    with Session(eng, expire_on_commit=False) as s:
        existing = s.get(Universe, ticker)
        if existing is None:
            s.add(
                Universe(
                    ticker=ticker, name=ticker, market="JP", sector="Industrials",
                    market_cap=1e12, market_cap_jpy=1e12, avg_volume_30d=1e6,
                    is_active=True,
                )
            )
            s.commit()


def _add_holding(
    eng, ticker: str, *, qty: int = 100, buy_price: float = 1000.0
) -> int:
    _add_universe(eng, ticker)
    with Session(eng, expire_on_commit=False) as s:
        p = Portfolio(
            ticker=ticker, personality="ASUKA",
            buy_date=dt.date(2026, 5, 1), buy_price=buy_price, qty=qty,
            currency="JPY", strategy_category="中期",
            target_period_days=60, target_pct=0.10, stop_loss_pct=0.08,
            target_date=dt.date(2026, 7, 1), thesis="test",
            status="active", broker_mode="paper",
        )
        s.add(p)
        s.commit()
        s.refresh(p)
        return int(p.id) if p.id is not None else 0


def _add_sell_decision(eng, ticker: str, action: str = "sell_loss") -> int:
    with Session(eng, expire_on_commit=False) as s:
        d = Decision(
            date=dt.date(2026, 5, 31), ticker=ticker, action=action,
            status="approved", entry_price=1000.0, stop_pct=0.08,
        )
        s.add(d)
        s.commit()
        s.refresh(d)
        return int(d.id) if d.id is not None else 0


class TestPaperCloseApproved:
    def test_sell_decision_でportfolio_close(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", qty=100, buy_price=1000.0)
        _add_sell_decision(eng, "A", action="sell_loss")

        r = paper_close_approved(
            eng, price_lookup=lambda t: 900.0, today=dt.date(2026, 5, 31)
        )
        assert r["closed"] == 1
        assert len(r["details"]) == 1
        d = r["details"][0]
        assert d["ticker"] == "A"
        assert d["closed_price"] == 900.0
        assert d["pnl_jpy"] == -10_000.0  # (900 - 1000) * 100

        # Portfolio が closed になっている
        with Session(eng) as s:
            p = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "A")).first()
            assert p.status == "closed"
            assert p.closed_price == 900.0
            assert p.closed_reason == "sell_loss"

        # Decision に actual_return が記録されている
        with Session(eng) as s:
            d = s.exec(select(Decision).where(col(Decision.ticker) == "A")).first()
            assert d.actual_return is not None
            assert abs(d.actual_return - (-0.10)) < 1e-9
            assert d.status == "ordered"

    def test_価格None_でskip_推測しない(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", qty=100)
        _add_sell_decision(eng, "A")

        r = paper_close_approved(eng, price_lookup=lambda t: None)
        assert "A" in r["skipped_no_price"]
        # Portfolio は active のまま
        with Session(eng) as s:
            p = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "A")).first()
            assert p.status == "active"

    def test_active_portfolio_なしでskip(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_universe(eng, "A")
        _add_sell_decision(eng, "A")
        # Portfolio 無し（事前に close された）
        r = paper_close_approved(eng, price_lookup=lambda t: 900.0)
        assert "A" in r["skipped_no_holding"]
        assert r["closed"] == 0

    def test_sell_profit_でcloseとactual_return正_利益(
        self, tmp_path: Path
    ) -> None:
        eng = _engine(tmp_path)
        _add_holding(eng, "A", buy_price=1000.0)
        _add_sell_decision(eng, "A", action="sell_profit")
        r = paper_close_approved(eng, price_lookup=lambda t: 1100.0)
        assert r["closed"] == 1
        with Session(eng) as s:
            d = s.exec(select(Decision).where(col(Decision.ticker) == "A")).first()
            assert d.actual_return == 0.10
        with Session(eng) as s:
            p = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "A")).first()
            assert p.closed_reason == "sell_profit"
