"""予算優先フィルタ（_filter_affordable_tickers）のテスト（v2.10 少額運用対応）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading_agent.orchestrator.morning_batch import _filter_affordable_tickers


class TestFilterAffordableTickers:
    def test_empty_input_returns_empty(self):
        assert _filter_affordable_tickers([], 100_000) == []

    def test_zero_budget_returns_original(self):
        tickers = ["7203", "9432"]
        assert _filter_affordable_tickers(tickers, 0) == tickers

    def test_negative_budget_returns_original(self):
        tickers = ["7203", "9432"]
        assert _filter_affordable_tickers(tickers, -1000) == tickers

    def test_all_affordable_keeps_order(self):
        """全銘柄が予算内なら元の順序を保つ。"""
        tickers = ["3697", "4587", "5803"]

        def fake_ticker(sym):
            # 全部 ¥100 → 単元 ¥10,000 で ¥100k 内
            m = MagicMock()
            m.fast_info.last_price = 100.0
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker):
            result = _filter_affordable_tickers(tickers, 100_000)
        assert result == tickers

    def test_affordable_prioritized(self):
        """予算内銘柄が予算外より前に来る。"""
        tickers = ["EXPENSIVE", "CHEAP", "MEDIUM"]
        prices = {"EXPENSIVE.T": 10_000.0, "CHEAP.T": 500.0, "MEDIUM.T": 2_000.0}

        def fake_ticker(sym):
            m = MagicMock()
            m.fast_info.last_price = prices.get(sym, 5000.0)
            return m

        # 注意: ticker が 4桁数字でないと is_jp_ticker=False で .T 付与されないが、
        # to_yfinance_symbol("EXPENSIVE") は "EXPENSIVE" を返す。
        # ここでは mock 経由でロジックを確認するため、価格マップを使う。
        prices_no_t = {"EXPENSIVE": 10_000.0, "CHEAP": 500.0, "MEDIUM": 2_000.0}

        def fake_ticker2(sym):
            m = MagicMock()
            m.fast_info.last_price = prices_no_t.get(sym, 5000.0)
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker2):
            # CHEAP (1株) = ¥500、MEDIUM (1株) = ¥2,000、EXPENSIVE (1株) = ¥10,000
            # 米国扱いで lot_size=1。予算 ¥1,500 → CHEAP のみ予算内
            result = _filter_affordable_tickers(tickers, 1_500)
        # CHEAP が先頭、その後 unaffordable
        assert result[0] == "CHEAP"
        assert "EXPENSIVE" in result
        assert "MEDIUM" in result

    def test_unaffordable_kept_at_tail(self):
        """予算外の銘柄も末尾に残る（fallback 用）。"""
        tickers = ["A", "B"]

        def fake_ticker(sym):
            m = MagicMock()
            m.fast_info.last_price = 999_999.0
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker):
            result = _filter_affordable_tickers(tickers, 10_000)
        # 予算外でも結果に含まれる
        assert set(result) == {"A", "B"}

    def test_no_price_treated_as_unaffordable(self):
        """価格取れない銘柄は予算外扱い（推測しない）。"""
        tickers = ["GOOD", "BAD"]

        def fake_ticker(sym):
            m = MagicMock()
            if "BAD" in sym:
                m.fast_info.last_price = 0  # 取れない
            else:
                m.fast_info.last_price = 100.0
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker):
            result = _filter_affordable_tickers(tickers, 100_000)
        # GOOD が先、BAD は末尾
        assert result[0] == "GOOD"

    def test_yfinance_exception_treated_as_unaffordable(self):
        """yfinance 例外は予算外扱い（fail-safe）。"""
        tickers = ["OK", "ERROR"]

        def fake_ticker(sym):
            if "ERROR" in sym:
                raise RuntimeError("network error")
            m = MagicMock()
            m.fast_info.last_price = 100.0
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker):
            result = _filter_affordable_tickers(tickers, 100_000)
        assert set(result) == {"OK", "ERROR"}
        assert result[0] == "OK"

    def test_pool_multiplier_limits_check(self):
        """pool_multiplier で価格取得数を制限。超過分は未チェックで末尾に。"""
        tickers = [f"T{i}" for i in range(50)]
        check_count = [0]

        def fake_ticker(sym):
            check_count[0] += 1
            m = MagicMock()
            m.fast_info.last_price = 1.0
            return m

        with patch("yfinance.Ticker", side_effect=fake_ticker):
            _filter_affordable_tickers(tickers, 1_000, pool_multiplier=2)
        # pool_multiplier=2, limit=10 → 20 件まで
        assert check_count[0] == 20
