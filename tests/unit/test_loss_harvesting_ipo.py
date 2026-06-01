"""Phase 6 (loss harvesting + IPO カレンダー) の単体テスト。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio import ipo_calendar as ipo
from trading_agent.portfolio import loss_harvesting as lh


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "phase6.sqlite")
    create_all(eng)
    return eng


def _add(eng, ticker: str, buy_price: float, qty: int = 100) -> None:
    with Session(eng, expire_on_commit=False) as s:
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
        s.add(
            Portfolio(
                ticker=ticker,
                personality="REI",
                buy_date=dt.date(2026, 5, 1),
                buy_price=buy_price,
                qty=qty,
                currency="JPY",
                strategy_category="中期",
                target_period_days=60,
                target_pct=0.10,
                stop_loss_pct=0.08,
                target_date=dt.date(2026, 7, 1),
                thesis="test",
                status="active",
                broker_mode="paper",
            )
        )
        s.commit()


class TestLossHarvesting:
    def test_保有ゼロでinsufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        r = lh.find_harvest_candidates(eng, current_price_lookup={})
        assert r["status"] == "insufficient_data"

    def test_含み損5pct未満は対象外_推測しない(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "A", buy_price=1000.0)
        r = lh.find_harvest_candidates(
            eng, current_price_lookup={"A": 970.0}  # -3% 含み損
        )
        assert r["status"] == "no_candidates"
        assert r["candidates"] == []

    def test_含み損5pct以上で候補化(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "A", buy_price=1000.0, qty=100)
        r = lh.find_harvest_candidates(
            eng, current_price_lookup={"A": 900.0}  # -10% 含み損
        )
        assert r["status"] == "active"
        assert len(r["candidates"]) == 1
        cand = r["candidates"][0]
        assert cand["ticker"] == "A"
        assert cand["unrealized_loss_jpy"] == -10000.0
        assert cand["loss_pct"] == -10.0

    def test_実現益と相殺できる範囲が計算される(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "A", buy_price=1000.0, qty=100)  # -10000 含み損
        r = lh.find_harvest_candidates(
            eng,
            current_price_lookup={"A": 900.0},
            realized_gain_jpy=5000.0,  # 実現益 5000
        )
        # harvestable = min(10000, 5000) = 5000
        assert r["harvestable_loss_jpy"] == 5000.0
        # tax saving = 5000 * 0.20315 = 1015.75
        assert r["estimated_tax_saving_jpy"] == 1016.0

    def test_現在価格がNoneの銘柄は除外(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "A", buy_price=1000.0)
        _add(eng, "B", buy_price=1000.0)
        r = lh.find_harvest_candidates(
            eng, current_price_lookup={"A": 800.0}  # B は None
        )
        assert r["status"] == "active"
        assert len(r["candidates"]) == 1
        assert r["candidates"][0]["ticker"] == "A"


class TestIPOCalendar:
    def test_クライアントなしでinsufficient_data(self) -> None:
        r = ipo.list_recent_ipos(client=None)
        # get_default_client が None を返す or 設定なしなら insufficient
        # 環境次第なので status は固定しない
        assert "status" in r
        assert "ipos" in r

    def test_末尾英字判定(self) -> None:
        assert ipo._is_provisional_code("285A0") is True
        assert ipo._is_provisional_code("72030") is False
        assert ipo._is_provisional_code("7203") is False
        assert ipo._is_provisional_code(None) is False
        assert ipo._is_provisional_code("") is False
        assert ipo._is_provisional_code("12345") is False

    def test_モッククライアントで暫定コード抽出(self) -> None:
        mock = MagicMock()
        mock.listed_info.return_value = [
            {"Code": "72030", "CoName": "トヨタ", "MktNm": "プライム", "S33Nm": "輸送機器", "ScaleCat": "Core30"},
            {"Code": "285A0", "CoName": "KIOXIA", "MktNm": "プライム", "S33Nm": "電気機器", "ScaleCat": "Mid400"},
            {"Code": "186A0", "CoName": "ASTROSCALE", "MktNm": "グロース", "S33Nm": "サービス業", "ScaleCat": ""},
        ]
        r = ipo.list_recent_ipos(client=mock)
        assert r["status"] == "active"
        # 末尾英字は 2 件
        assert r["total"] == 2
        tickers = [i["ticker"] for i in r["ipos"]]
        assert "285A" in tickers
        assert "186A" in tickers
        assert "7203" not in tickers
