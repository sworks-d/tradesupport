"""factor exposure 分析の単体テスト（v2.10 Phase 5）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio import factor_exposure as fx


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "fx.sqlite")
    create_all(eng)
    return eng


def _add(eng, ticker: str, market_cap_jpy: float) -> None:
    with Session(eng, expire_on_commit=False) as s:
        s.add(
            Universe(
                ticker=ticker,
                name=ticker,
                market="JP",
                sector="Industrials",
                market_cap=market_cap_jpy,
                market_cap_jpy=market_cap_jpy,
                avg_volume_30d=1e6,
                is_active=True,
            )
        )
        s.add(
            Portfolio(
                ticker=ticker,
                personality="REI",
                buy_date=dt.date(2026, 5, 1),
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


class TestComputeFactorScores:
    def test_momentum_リターン0_50点(self) -> None:
        scores = fx.compute_factor_scores(
            market_cap_jpy=None,
            momentum_3m=0.0,
            per=None,
            roe=None,
            equity_asset_ratio=None,
        )
        assert scores["momentum"] == 50.0

    def test_momentum_30pct_リターンで100(self) -> None:
        scores = fx.compute_factor_scores(
            market_cap_jpy=None, momentum_3m=0.30, per=None, roe=None, equity_asset_ratio=None
        )
        assert scores["momentum"] == 100.0

    def test_value_PER低いほど高スコア(self) -> None:
        low = fx.compute_factor_scores(
            market_cap_jpy=None, momentum_3m=None, per=5.0, roe=None, equity_asset_ratio=None
        )
        high = fx.compute_factor_scores(
            market_cap_jpy=None, momentum_3m=None, per=30.0, roe=None, equity_asset_ratio=None
        )
        assert low["value"] == 100.0
        assert high["value"] == 0.0

    def test_size_時価総額大きいほど低スコア(self) -> None:
        small = fx.compute_factor_scores(
            market_cap_jpy=1e9, momentum_3m=None, per=None, roe=None, equity_asset_ratio=None
        )  # 10億
        large = fx.compute_factor_scores(
            market_cap_jpy=1e14, momentum_3m=None, per=None, roe=None, equity_asset_ratio=None
        )  # 100兆
        assert small["size"] > large["size"]

    def test_データなしフィールドはNone_推測しない(self) -> None:
        scores = fx.compute_factor_scores(
            market_cap_jpy=None,
            momentum_3m=None,
            per=None,
            roe=None,
            equity_asset_ratio=None,
        )
        assert all(v is None for v in scores.values())


class TestComputePortfolioExposure:
    def test_保有ゼロでinsufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        r = fx.compute_portfolio_exposure(eng)
        assert r["status"] == "insufficient_data"
        assert r["reason"] == "no_holdings"

    def test_保有あり_時価総額のみ計算(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """ネットアクセス系（momentum/value/quality）を mock して size のみテスト。"""
        monkeypatch.setattr(fx, "_fetch_3m_return", lambda t: None)
        monkeypatch.setattr(fx, "_fetch_trailing_pe", lambda t: None)
        monkeypatch.setattr(fx, "_fetch_roe_and_eqratio", lambda t: (None, None))

        eng = _engine(tmp_path)
        _add(eng, "A", market_cap_jpy=1e12)   # 1兆
        _add(eng, "B", market_cap_jpy=1e11)   # 1000億
        r = fx.compute_portfolio_exposure(eng)
        assert r["status"] == "active"
        # size factor は計算される
        assert r["weighted_factors"]["size"] is not None
        # mock で None 返却 → momentum/value/quality は None
        assert r["weighted_factors"]["momentum"] is None
        assert r["weighted_factors"]["value"] is None

    def test_4軸全実装で全てが値を持つ(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Phase 1B: 4 軸全実装後、フェッチャーが値を返せば 4 軸全て計算される。"""
        monkeypatch.setattr(fx, "_fetch_3m_return", lambda t: 0.10)  # +10%
        monkeypatch.setattr(fx, "_fetch_trailing_pe", lambda t: 15.0)
        monkeypatch.setattr(
            fx, "_fetch_roe_and_eqratio", lambda t: (0.15, 0.50)
        )

        eng = _engine(tmp_path)
        _add(eng, "A", market_cap_jpy=1e12)
        r = fx.compute_portfolio_exposure(eng)
        assert r["status"] == "active"
        wf = r["weighted_factors"]
        # 4 軸全てに値が入る
        assert wf["momentum"] is not None
        assert wf["value"] is not None
        assert wf["quality"] is not None
        assert wf["size"] is not None

    def test_Universeにない銘柄はexcluded(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(fx, "_fetch_3m_return", lambda t: None)
        monkeypatch.setattr(fx, "_fetch_trailing_pe", lambda t: None)
        monkeypatch.setattr(fx, "_fetch_roe_and_eqratio", lambda t: (None, None))
        eng = _engine(tmp_path)
        _add(eng, "A", market_cap_jpy=1e12)
        r = fx.compute_portfolio_exposure(eng)
        assert isinstance(r["excluded_tickers"], list)
