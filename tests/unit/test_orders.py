"""A-5 / S3：決裁→発注リスト（貫通の出口）の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.portfolio.orders import (
    approved_buy_decisions,
    build_order_list,
    decide,
)
from trading_agent.risk import Held
from trading_agent.utils.time_utils import utcnow


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "orders.sqlite")
    create_all(eng)
    return eng


def _seed(engine, ticker: str, status: str = "awaiting") -> int:
    # JST 統一: approved_buy_decisions が today_jst() でクエリするため
    from trading_agent.utils.time_utils import today_jst as _today_jst

    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=_today_jst(), ticker=ticker, action="buy", status=status)
        s.add(d)
        s.commit()
        s.refresh(d)
        return d.id


class TestDecide:
    def test_approve_updates_status(self, engine) -> None:
        did = _seed(engine, "NVDA")
        assert decide(engine, did, action="approved", reason="3審判一致") is True
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.status == "approved"
            assert d.user_action == "adopted"
            assert d.user_note == "3審判一致"
            assert d.user_acted_at is not None

    def test_held_is_default_safe_choice(self, engine) -> None:
        did = _seed(engine, "NVDA")
        decide(engine, did, action="held", reason="割れ/未照合")
        with Session(engine) as s:
            assert s.get(Decision, did).status == "held"

    def test_invalid_action_raises(self, engine) -> None:
        did = _seed(engine, "NVDA")
        with pytest.raises(ValueError):
            decide(engine, did, action="yolo")

    def test_missing_decision_returns_false(self, engine) -> None:
        assert decide(engine, 9999, action="approved") is False

    def test_approved_buy_decisions_query(self, engine) -> None:
        a = _seed(engine, "NVDA")
        _seed(engine, "AAPL")  # awaiting（対象外）
        decide(engine, a, action="approved")
        rows = approved_buy_decisions(engine)
        assert {r.ticker for r in rows} == {"NVDA"}


class TestBuildOrderList:
    def _prices(self, m: dict[str, float]):
        return lambda t: m.get(t)

    def _sectors(self, m: dict[str, str]):
        return lambda t: m.get(t, "unknown")

    def test_us_and_jp_order_lines(self, engine) -> None:
        orders = build_order_list(
            ["NVDA", "7203"],
            price_lookup=self._prices({"NVDA": 20_000.0, "7203": 3_000.0}),
            sector_lookup=self._sectors({"NVDA": "Tech", "7203": "Auto"}),
            account_total_jpy=100_000.0,
            cash_jpy=100_000.0,
        )
        by = {o.ticker: o for o in orders}
        assert by["NVDA"].market == "US" and by["NVDA"].shares > 0  # 端株
        assert by["7203"].market == "JP" and by["7203"].shares == int(by["7203"].shares)  # 1株単位
        # 想定損失と stop が併記される（規律＝1R / stop価格）
        assert all(o.risk_jpy > 0 and o.stop_price_jpy > 0 for o in orders)

    def test_sector_concentration_blocks_third(self, engine) -> None:
        # 同一セクター3銘柄 → 3つ目は規律ゲートで除外（発注リストに出ない）
        orders = build_order_list(
            ["A", "B", "C"],
            price_lookup=self._prices({"A": 1_000.0, "B": 1_000.0, "C": 1_000.0}),
            sector_lookup=self._sectors({"A": "AI", "B": "AI", "C": "AI"}),
            account_total_jpy=100_000.0,
            cash_jpy=100_000.0,
        )
        assert {o.ticker for o in orders} == {"A", "B"}

    def test_drawdown_halts_all(self, engine) -> None:
        orders = build_order_list(
            ["NVDA"],
            price_lookup=self._prices({"NVDA": 5_000.0}),
            sector_lookup=self._sectors({"NVDA": "Tech"}),
            account_total_jpy=85_000.0,
            cash_jpy=85_000.0,
            peak_total_jpy=100_000.0,
        )
        assert orders == []  # DD -15% で新規停止

    def test_unpriced_skipped(self, engine) -> None:
        orders = build_order_list(
            ["NVDA"],
            price_lookup=self._prices({}),  # 価格取得不可
            sector_lookup=self._sectors({"NVDA": "Tech"}),
            account_total_jpy=100_000.0,
            cash_jpy=100_000.0,
        )
        assert orders == []

    def test_held_counts_toward_limits(self, engine) -> None:
        # 既にAIを2銘柄保有 → 新規AIはセクター上限でブロック
        held = [Held("X", "AI", 10_000.0), Held("Y", "AI", 10_000.0)]
        orders = build_order_list(
            ["Z"],
            price_lookup=self._prices({"Z": 1_000.0}),
            sector_lookup=self._sectors({"Z": "AI"}),
            account_total_jpy=100_000.0,
            cash_jpy=100_000.0,
            held=held,
        )
        assert orders == []
