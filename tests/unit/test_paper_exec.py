"""P4-1 ペーパー執行ループの単体テスト（approved のみ・翌寄り紙約定・record_entry）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.paper_exec import paper_fill_approved


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "paper.sqlite")
    create_all(eng)
    return eng


def _add(eng, ticker: str, status: str) -> int:
    with Session(eng, expire_on_commit=False) as s:
        # v2.10: paper_fill_approved の Universe 照合（ハルシネーション防壁）通過のため
        # テスト fixture にも Universe を追加する。
        existing = s.exec(select(Universe).where(col(Universe.ticker) == ticker)).first()
        if existing is None:
            s.add(
                Universe(
                    ticker=ticker,
                    name=ticker,
                    market="JP",
                    sector="Industrials",
                    market_cap=1.0e12,
                    market_cap_jpy=1.0e12,
                    avg_volume_30d=1.0e6,
                    is_active=True,
                )
            )
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
        # v2.2 TASK-SZ2: 端数 0.67 切り上げで 17 株（旧 16 株）
        assert f.shares == 17
        # v2.4 TASK-F1: volume データ無し → slippage 1.5x（0.2% × 1.5 = 0.3%） → fill_price ≈ 1003
        # cost = 17 × 1003 = 17,051、現金 ≈ 82,949
        import pytest
        assert res.cash_after == pytest.approx(82_949.0, abs=0.001)
        with Session(eng) as s:
            pos = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "7203")).one()
            assert pos.status == "active"
            assert pos.qty == 17
            # Portfolio.buy_price は実約定価格（slippage 適用後・float 精度で 1002.99...）
            assert pos.buy_price == pytest.approx(1003.0, abs=0.001)
            assert pos.stop_loss_pct == 0.12  # v2.1 TASK-SZ4: 正値で統一
            assert pos.target_pct == 0.0  # B'：利確で刻まない
            d = s.get(Decision, did)
            assert d.status == "holding"
            assert d.entry_price == pytest.approx(1003.0, abs=0.001)  # v2.4 TASK-F1
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
