"""P4-1 ペーパー執行ループの単体テスト（approved のみ・翌寄り紙約定・record_entry）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.paper_exec import paper_fill_approved


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "paper.sqlite")
    create_all(eng)
    return eng


def _add(eng, ticker: str, status: str) -> int:
    with Session(eng, expire_on_commit=False) as s:
        d = Decision(date=dt.date(2026, 5, 25), ticker=ticker, action="buy", status=status)
        s.add(d)
        s.commit()
        s.refresh(d)
        return int(d.id)


class TestPaperFill:
    def test_approved_is_filled(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add(eng, "7203", "approved")
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0, today=dt.date(2026, 5, 25),
        )
        assert len(res.fills) == 1
        f = res.fills[0]
        assert f.ticker == "7203"
        assert f.shares == 16  # budget=min(1R/stop=16667, cap20k, cash80k)=16667 → 16株@1000
        assert res.cash_after == 100_000.0 - 16_000.0
        with Session(eng) as s:
            pos = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "7203")).one()
            assert pos.status == "active"
            assert pos.qty == 16
            assert pos.buy_price == 1000.0
            assert pos.stop_loss_pct == -0.12  # 損切りは負値
            assert pos.target_pct == 0.0  # B'：利確で刻まない
            d = s.get(Decision, did)
            assert d.status == "holding"
            assert d.entry_price == 1000.0  # record_entry が刻む
            assert d.evaluation_date is not None  # 評価期日が付く

    def test_only_approved_touched(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "6758", "verifying")  # 未検証は執行しない
        _add(eng, "9984", "awaiting")  # 決裁待ちも執行しない
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert res.fills == []
        with Session(eng) as s:
            assert s.exec(select(Portfolio)).all() == []

    def test_price_unavailable_is_skipped(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "7203", "approved")
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: None, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert res.fills == []
        assert res.skipped and res.skipped[0][0] == "7203"

    def test_too_expensive_is_skipped(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "9983", "approved")  # 1株が予算超（高単価JP）
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 50_000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        # budget=min(1R/stop=16667, ...)=16667 < 50000 → 0株 → skip
        assert res.fills == []
        assert res.skipped and "サイズ0" in res.skipped[0][1]
