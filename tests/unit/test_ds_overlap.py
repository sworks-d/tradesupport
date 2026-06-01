"""DS 4 機間重複度の単体テスト（v2.10 Phase 1C）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.ds_overlap import _jaccard, compute_ds_overlap


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ds.sqlite")
    create_all(eng)
    return eng


def _add_fill(
    eng,
    ticker: str,
    personality: str,
    buy_date: dt.date = dt.date(2026, 5, 20),
) -> None:
    with Session(eng, expire_on_commit=False) as s:
        existing = s.get(Universe, ticker)
        if existing is None:
            s.add(
                Universe(
                    ticker=ticker,
                    name=ticker,
                    market="JP",
                    sector="Industrials",
                    market_cap=1e12,
                    market_cap_jpy=1e12,
                    avg_volume_30d=1e6,
                    is_active=True,
                )
            )
        s.add(
            Portfolio(
                ticker=ticker,
                personality=personality,
                buy_date=buy_date,
                buy_price=1000.0,
                qty=100,
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


class TestJaccard:
    def test_完全一致で1(self) -> None:
        assert _jaccard({"A", "B"}, {"A", "B"}) == 1.0

    def test_完全独立で0(self) -> None:
        assert _jaccard({"A"}, {"B"}) == 0.0

    def test_部分重複(self) -> None:
        # {A,B} と {B,C} → 共通 1, 全体 3 → 1/3
        assert abs(_jaccard({"A", "B"}, {"B", "C"}) - 1 / 3) < 1e-9

    def test_両方空で0(self) -> None:
        assert _jaccard(set(), set()) == 0.0


class TestComputeDsOverlap:
    def test_fill_ゼロでinsufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        r = compute_ds_overlap(eng)
        assert r["status"] == "insufficient_data"

    def test_active_機が1つだけでもinsufficient(
        self, tmp_path: Path
    ) -> None:
        eng = _engine(tmp_path)
        _add_fill(eng, "A", "REI")
        r = compute_ds_overlap(eng)
        assert r["status"] == "insufficient_data"
        assert r["reason"] == "less_than_2_active_pilots"

    def test_2機が完全独立(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_fill(eng, "A", "REI")
        _add_fill(eng, "B", "ASUKA")
        r = compute_ds_overlap(eng)
        assert r["status"] == "active"
        assert r["mean_jaccard"] == 0.0
        assert r["concentration_label"] == "独立性高"
        assert r["high_overlap_pairs"] == []

    def test_2機が完全一致で高警戒(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_fill(eng, "A", "REI")
        _add_fill(eng, "A", "ASUKA")
        r = compute_ds_overlap(eng)
        assert r["status"] == "active"
        assert r["mean_jaccard"] == 1.0
        assert "重複多" in r["concentration_label"]
        assert len(r["high_overlap_pairs"]) == 1
        pair = r["high_overlap_pairs"][0]
        assert pair["jaccard"] == 1.0
        assert pair["shared_tickers"] == ["A"]

    def test_部分重複(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # REI: {A, B}, ASUKA: {B, C} → jaccard 1/3
        _add_fill(eng, "A", "REI")
        _add_fill(eng, "B", "REI")
        _add_fill(eng, "B", "ASUKA")
        _add_fill(eng, "C", "ASUKA")
        r = compute_ds_overlap(eng)
        assert r["status"] == "active"
        assert abs(r["mean_jaccard"] - 0.333) < 0.01
        # 0.333 < 0.5 なので high_overlap_pairs はゼロ
        assert r["high_overlap_pairs"] == []

    def test_期間外fill_は含まれない(self, tmp_path: Path) -> None:
        """lookback_days=30 で 30 日より古い fill は除外。"""
        eng = _engine(tmp_path)
        _add_fill(eng, "A", "REI", buy_date=dt.date(2026, 1, 1))  # 30 日より古い
        _add_fill(eng, "B", "ASUKA", buy_date=dt.date.today())
        r = compute_ds_overlap(eng, lookback_days=30)
        # REI は期間外 → active_pilots に含まれず insufficient
        assert r["status"] == "insufficient_data"

    def test_可変パラメータ_閾値変更(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # 部分重複 1/3 → 閾値 0.3 にすれば high_overlap
        _add_fill(eng, "A", "REI")
        _add_fill(eng, "B", "REI")
        _add_fill(eng, "B", "ASUKA")
        _add_fill(eng, "C", "ASUKA")
        r = compute_ds_overlap(eng, high_overlap_threshold=0.3)
        # 0.333 > 0.3 で high_overlap 入り
        assert len(r["high_overlap_pairs"]) == 1
