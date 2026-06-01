"""ポートフォリオ相関分析の単体テスト（v2.10 Phase 1）。

実 yfinance は叩かない（fetch_daily_returns を monkeypatch）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio import correlation as cmod


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "corr.sqlite")
    create_all(eng)
    return eng


def _add_position(eng, ticker: str, qty: int = 100) -> None:
    with Session(eng, expire_on_commit=False) as s:
        from trading_agent.models.universe import Universe

        # Universe にも追加（foreign_key 制約のため）
        existing_u = s.get(Universe, ticker)
        if existing_u is None:
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
                buy_price=1000.0,
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


class TestEmptyOrInsufficient:
    def test_保有ゼロは_insufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        result = cmod.compute_correlation_matrix(eng)
        assert result["status"] == "insufficient_data"
        assert result["tickers"] == []
        assert result["matrix"] == []

    def test_保有1銘柄も_insufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_position(eng, "7203")
        result = cmod.compute_correlation_matrix(eng)
        assert result["status"] == "insufficient_data"
        assert result["reason"] == "less_than_2_positions"


class TestCorrelationCalculation:
    def test_完全同一リターンで相関1(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """2 銘柄が完全に同じ値動きなら corr=1.0、強相関ペアに入る。"""
        eng = _engine(tmp_path)
        _add_position(eng, "7203")
        _add_position(eng, "9432")

        # 両方とも同じリターン
        def _fake_returns(ticker: str):
            return [0.01, -0.02, 0.03, 0.01, -0.01] * 5  # 25 日分

        monkeypatch.setattr(cmod, "fetch_daily_returns", _fake_returns)

        result = cmod.compute_correlation_matrix(eng)
        assert result["status"] == "active"
        assert len(result["tickers"]) == 2
        assert result["max_corr"] == 1.0
        # 強相関ペアに含まれる（≥0.7）
        assert len(result["high_correlation_pairs"]) == 1

    def test_逆方向リターンで相関マイナス1(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        eng = _engine(tmp_path)
        _add_position(eng, "7203")
        _add_position(eng, "9432")

        seq_a = [0.01, -0.02, 0.03, 0.01, -0.01] * 5
        seq_b = [-x for x in seq_a]

        def _fake_returns(ticker: str):
            return seq_a if ticker == "7203" else seq_b

        monkeypatch.setattr(cmod, "fetch_daily_returns", _fake_returns)

        result = cmod.compute_correlation_matrix(eng)
        assert result["status"] == "active"
        # 逆相関も「強相関」扱い（|r|=1）
        assert result["max_corr"] == 1.0
        assert len(result["high_correlation_pairs"]) == 1
        assert result["high_correlation_pairs"][0]["correlation"] == -1.0

    def test_データ取れない銘柄は除外(self, tmp_path: Path, monkeypatch) -> None:
        eng = _engine(tmp_path)
        _add_position(eng, "7203")
        _add_position(eng, "285A")  # データ取れない想定

        def _fake_returns(ticker: str):
            return [0.01] * 25 if ticker == "7203" else None

        monkeypatch.setattr(cmod, "fetch_daily_returns", _fake_returns)
        result = cmod.compute_correlation_matrix(eng)
        # 1 銘柄しかデータ取れない → insufficient_data
        assert result["status"] == "insufficient_data"
        assert "285A" in result["tickers"]  # tickers には残す
        assert result["data_available"] == 1

    def test_集中度ラベル_分散良(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """ランダム的なリターンなら mean_abs_corr が小さく分散良ラベル。"""
        eng = _engine(tmp_path)
        _add_position(eng, "A")
        _add_position(eng, "B")
        _add_position(eng, "C")

        import random

        random.seed(42)
        rets_a = [random.uniform(-0.05, 0.05) for _ in range(30)]
        rets_b = [random.uniform(-0.05, 0.05) for _ in range(30)]
        rets_c = [random.uniform(-0.05, 0.05) for _ in range(30)]
        bank = {"A": rets_a, "B": rets_b, "C": rets_c}

        def _fake_returns(ticker: str):
            return bank.get(ticker)

        monkeypatch.setattr(cmod, "fetch_daily_returns", _fake_returns)

        result = cmod.compute_correlation_matrix(eng)
        assert result["status"] == "active"
        # ランダムなら集中ラベルにはならない（強相関ペアもないはず）
        assert result["concentration_label"] in ("分散良", "中程度")
