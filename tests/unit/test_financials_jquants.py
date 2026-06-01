"""financials.py の J-Quants 統合の単体テスト（v2.10）。

実 API は叩かない。J-Quants クライアントをモック化して、ラッパー層の挙動だけ確認する。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading_agent.screening.financials import (
    PeriodFinancials,
    _jquants_stmt_to_period,
    fetch_financials,
)


class TestJQuantsStmtToPeriod:
    def test_主要4フィールドの変換(self) -> None:
        stmt = {
            "CurPerEn": "2024-03-31",
            "Sales": "45095325000000",
            "OP": "5352934000000",
            "NP": "4944933000000",
            "TA": "90114296000000",
            "EPS": "365.94",
            "CFO": "4206373000000",
        }
        pf = _jquants_stmt_to_period(stmt)
        assert pf is not None
        assert pf.period == "2024-03-31"
        assert pf.revenue == 45_095_325_000_000.0
        assert pf.ebit == 5_352_934_000_000.0
        assert pf.net_income == 4_944_933_000_000.0
        assert pf.total_assets == 90_114_296_000_000.0
        assert pf.operating_cashflow == 4_206_373_000_000.0
        assert pf.shares is not None
        # shares = NP / EPS で逆算
        assert abs(pf.shares - 4_944_933_000_000.0 / 365.94) < 1.0

    def test_欠損フィールドは_None_のまま_推測しない(self) -> None:
        """J-Quants から取れないフィールド（cogs/sga 等）は None。"""
        stmt = {"CurPerEn": "2024-03-31", "Sales": "100", "NP": "10"}
        pf = _jquants_stmt_to_period(stmt)
        assert pf is not None
        # 取れないものは None のまま（推測禁止）
        assert pf.cogs is None
        assert pf.gross_profit is None
        assert pf.sga is None
        assert pf.depreciation is None
        assert pf.current_assets is None
        assert pf.inventory is None
        assert pf.total_liabilities is None
        assert pf.long_term_debt is None
        assert pf.retained_earnings is None
        assert pf.working_capital is None

    def test_EPS_ゼロのとき_shares_計算しない_推測しない(self) -> None:
        stmt = {"CurPerEn": "2024-03-31", "NP": "100", "EPS": "0"}
        pf = _jquants_stmt_to_period(stmt)
        assert pf is not None
        assert pf.shares is None

    def test_空文字フィールドは_None_扱い(self) -> None:
        stmt = {"CurPerEn": "2024-03-31", "Sales": "", "NP": None, "OP": ""}
        pf = _jquants_stmt_to_period(stmt)
        assert pf is not None
        assert pf.revenue is None
        assert pf.net_income is None
        assert pf.ebit is None

    def test_NaN_文字列は_None_扱い(self) -> None:
        stmt = {"CurPerEn": "2024-03-31", "Sales": "abc", "NP": "NaN"}
        pf = _jquants_stmt_to_period(stmt)
        assert pf is not None
        assert pf.revenue is None
        # "NaN" は float("NaN") に変換 → NaN チェックで None
        assert pf.net_income is None


class TestFetchFinancialsFallback:
    """J-Quants 失敗 → yfinance fallback の経路テスト。"""

    def test_JP株_JQuants成功_でそのまま使う(self, monkeypatch) -> None:
        """J-Quants が動く時は yfinance を呼ばない。"""
        from trading_agent.screening import financials as fmod

        # J-Quants 成功シナリオ
        fake_financials = MagicMock()
        fake_financials.current.revenue = 1_000_000.0
        fake_financials.source = "jquants"

        def _stub_jq(ticker, market_cap=None):
            return fake_financials

        yf_mock = MagicMock()

        monkeypatch.setattr(fmod, "_fetch_jquants_financials", _stub_jq)
        monkeypatch.setattr(fmod, "_fetch_yfinance_statements", yf_mock)

        result = fetch_financials("7203")
        assert result is fake_financials
        # yfinance は呼ばれていない
        yf_mock.assert_not_called()

    def test_JP株_JQuants失敗_でyfinanceにfallback(self, monkeypatch) -> None:
        from trading_agent.screening import financials as fmod

        def _stub_jq_none(ticker, market_cap=None):
            return None  # J-Quants 取れず

        yf_mock = MagicMock(return_value={"periods": [], "rows": {}})

        monkeypatch.setattr(fmod, "_fetch_jquants_financials", _stub_jq_none)
        monkeypatch.setattr(fmod, "_fetch_yfinance_statements", yf_mock)

        fetch_financials("7203")
        # yfinance が呼ばれた
        yf_mock.assert_called_once_with("7203")

    def test_JP株_JQuants部分的にしか取れない場合もfallback(self, monkeypatch) -> None:
        """revenue が None なら J-Quants は不完全 → yfinance fallback。"""
        from trading_agent.screening import financials as fmod

        partial_financials = MagicMock()
        partial_financials.current.revenue = None  # 主要指標が無い

        def _stub_partial(ticker, market_cap=None):
            return partial_financials

        yf_mock = MagicMock(return_value={"periods": [], "rows": {}})

        monkeypatch.setattr(fmod, "_fetch_jquants_financials", _stub_partial)
        monkeypatch.setattr(fmod, "_fetch_yfinance_statements", yf_mock)

        fetch_financials("7203")
        yf_mock.assert_called_once()

    def test_US株_はJQuantsを試さない(self, monkeypatch) -> None:
        """US 株は最初から yfinance（J-Quants は JP のみ）。"""
        from trading_agent.screening import financials as fmod

        jq_mock = MagicMock()
        yf_mock = MagicMock(return_value={"periods": [], "rows": {}})

        monkeypatch.setattr(fmod, "_fetch_jquants_financials", jq_mock)
        monkeypatch.setattr(fmod, "_fetch_yfinance_statements", yf_mock)

        fetch_financials("AAPL")
        # J-Quants は呼ばれない
        jq_mock.assert_not_called()
        # yfinance は呼ばれる
        yf_mock.assert_called_once()

    def test_fetcher注入時は_JQuantsをスキップ(self, monkeypatch) -> None:
        """既存テスト互換: fetcher を明示注入したら J-Quants 経路を通らない。"""
        from trading_agent.screening import financials as fmod

        jq_mock = MagicMock()
        monkeypatch.setattr(fmod, "_fetch_jquants_financials", jq_mock)

        # 任意の fetcher を注入
        def _custom_fetcher(ticker):
            return {"periods": [], "rows": {}}

        fetch_financials("7203", fetcher=_custom_fetcher)
        # J-Quants はスキップされる
        jq_mock.assert_not_called()
