"""約定履歴（_purchase_history）の単体テスト。official/legacy 分離と台帳フィールドを検証。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe

from scripts.phase_c_status import _purchase_history


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ph.sqlite")
    create_all(eng)
    return eng


def _seed_universe(engine, ticker, name):
    with Session(engine) as s:
        s.add(Universe(
            ticker=ticker, name=name, market="JP", sector="X",
            market_cap=1e10, market_cap_jpy=1e10, avg_volume_30d=1e6, is_active=True,
        ))
        s.commit()


def _seed(engine, *, ticker, via, buy_date, buy_price, qty, status="active",
          closed_price=None, closed_reason=None, tags=None):
    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=buy_date, ticker=ticker, action="buy", status="ordered")
        d.filled_via = via
        d.entry_broker_mode = "paper"
        d.entry_signal_tags = tags or []
        s.add(d)
        s.commit()
        s.refresh(d)
        p = Portfolio(
            ticker=ticker, buy_date=buy_date, buy_price=buy_price, qty=qty, currency="JPY",
            strategy_category="中期", status=status, broker_mode="paper", decision_id=d.id,
            target_period_days=60, target_pct=0.2, stop_loss_pct=0.1,
            target_date=buy_date + dt.timedelta(days=60), thesis="test",
            closed_price=closed_price, closed_reason=closed_reason,
        )
        s.add(p)
        s.commit()


def test_official_only_with_legacy_excluded(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _seed_universe(eng, "AAA", "エー社")
    _seed(eng, ticker="AAA", via="ds_dispatch", buy_date=dt.date(2026, 6, 5),
          buy_price=100.0, qty=10, tags=["sector_rs"])
    # legacy: filled_via が公式でない → 除外
    _seed(eng, ticker="AAA", via=None, buy_date=dt.date(2026, 1, 1), buy_price=50.0, qty=5)

    res = _purchase_history(eng, broker_mode="paper")
    assert res["official_n"] == 1
    assert res["legacy_excluded_n"] == 1
    p = res["purchases"][0]
    assert p["ticker"] == "AAA" and p["name"] == "エー社"
    assert p["buy_date"] == "2026-06-05"
    assert p["buy_price"] == 100.0 and p["qty"] == 10.0
    assert p["cost_jpy"] == 1000  # 100 × 10
    assert p["status"] == "active" and p["realized_pnl_jpy"] is None
    assert p["entry_signal_tags"] == ["sector_rs"]


def test_closed_shows_sell_and_pnl(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _seed_universe(eng, "BBB", "ビー社")
    _seed(eng, ticker="BBB", via="ds_dispatch", buy_date=dt.date(2026, 6, 1),
          buy_price=100.0, qty=10, status="closed", closed_price=90.0, closed_reason="stop_loss")

    res = _purchase_history(eng, broker_mode="paper")
    p = res["purchases"][0]
    assert p["status"] == "closed"
    assert p["sell_price"] == 90.0 and p["closed_reason"] == "stop_loss"
    assert p["realized_pnl_jpy"] == -100  # (90-100)*10


def test_sorted_newest_first(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _seed_universe(eng, "AAA", "A")
    _seed(eng, ticker="AAA", via="manual", buy_date=dt.date(2026, 6, 1), buy_price=10.0, qty=1)
    _seed(eng, ticker="AAA", via="manual", buy_date=dt.date(2026, 6, 5), buy_price=20.0, qty=1)
    res = _purchase_history(eng, broker_mode="paper")
    dates = [p["buy_date"] for p in res["purchases"]]
    assert dates == ["2026-06-05", "2026-06-01"]  # 新しい順
