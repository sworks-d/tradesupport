"""データ整合性チェック（run_integrity_check）の単体テスト。I7（Q2 不変条件）中心。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.integrity_check import run_integrity_check
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "integ.sqlite")
    create_all(eng)
    return eng


def _portfolio(ticker: str, decision_id: int) -> Portfolio:
    return Portfolio(
        ticker=ticker, buy_date=dt.date(2026, 5, 25), buy_price=1000.0, qty=10,
        currency="JPY", strategy_category="中期", target_period_days=90,
        target_pct=0.20, stop_loss_pct=0.10, target_date=dt.date(2026, 8, 23),
        thesis="t", status="active", broker_mode="paper", planned_total_qty=10,
        decision_id=decision_id,
    )


class TestI7OnePortfolioPerDecision:
    def test_clean_when_one_to_one(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        with Session(eng, expire_on_commit=False) as s:
            d = Decision(date=dt.date(2026, 5, 25), ticker="7203", action="buy",
                         status="filled", entry_price=1000.0)
            s.add(d); s.commit(); s.refresh(d)
            s.add(_portfolio("7203", d.id)); s.commit()
        res = run_integrity_check(eng)
        assert not any(i["kind"] == "I7_multiple_portfolio_per_decision"
                       for i in res.issues)

    def test_detects_multiple_per_decision(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        with Session(eng, expire_on_commit=False) as s:
            d = Decision(date=dt.date(2026, 5, 25), ticker="7203", action="buy",
                         status="filled", entry_price=1000.0)
            s.add(d); s.commit(); s.refresh(d)
            # 同一 decision_id に Portfolio 2件（不変条件違反）
            s.add(_portfolio("7203", d.id))
            s.add(_portfolio("7203", d.id))
            s.commit()
        res = run_integrity_check(eng)
        i7 = [i for i in res.issues if i["kind"] == "I7_multiple_portfolio_per_decision"]
        assert len(i7) == 1
        assert i7[0]["count"] == 2
        assert i7[0]["decision_id"] == d.id

    def test_detects_active_plus_closed_per_decision(self, tmp_path: Path) -> None:
        # codex 指摘3: 片方 closed・片方 active でも検出（active 限定だとすり抜ける歪み後を捕捉）
        eng = _engine(tmp_path)
        with Session(eng, expire_on_commit=False) as s:
            d = Decision(date=dt.date(2026, 5, 25), ticker="7203", action="buy",
                         status="filled", entry_price=1000.0)
            s.add(d); s.commit(); s.refresh(d)
            p_active = _portfolio("7203", d.id)
            p_closed = _portfolio("7203", d.id)
            p_closed.status = "closed"
            p_closed.closed_at = utcnow()
            s.add(p_active); s.add(p_closed); s.commit()
        res = run_integrity_check(eng)
        i7 = [i for i in res.issues if i["kind"] == "I7_multiple_portfolio_per_decision"]
        assert len(i7) == 1
        assert i7[0]["count"] == 2
