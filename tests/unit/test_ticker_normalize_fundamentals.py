"""fundamentals の ticker 正規化（旧型 + 新型 ticker 対応）。"""

from __future__ import annotations

import pytest

from trading_agent.mcp_tools.fundamentals import is_jp_ticker, to_yfinance_symbol


class TestIsJpTicker:
    @pytest.mark.parametrize(
        "ticker,expected",
        [
            # 旧型 (4 桁数字)
            ("7203", True),
            ("9432", True),
            ("0001", True),
            # 新型 (数字 + 末尾 1 文字アルファベット)
            ("141A", True),
            ("285A", True),
            ("417A", True),
            ("9999X", True),
            # .T サフィックス
            ("7203.T", True),
            ("141A.T", True),
            # 米国株
            ("AAPL", False),
            ("NVDA", False),
            ("MSFT", False),
            # 規格外
            ("", False),
            ("AB", False),
        ],
    )
    def test_classification(self, ticker: str, expected: bool):
        assert is_jp_ticker(ticker) is expected


class TestToYfinanceSymbol:
    @pytest.mark.parametrize(
        "ticker,expected",
        [
            # 旧型
            ("7203", "7203.T"),
            ("9432", "9432.T"),
            # 新型 (これが今回の修正対象)
            ("141A", "141A.T"),
            ("285A", "285A.T"),
            ("417A", "417A.T"),
            # 冪等性
            ("7203.T", "7203.T"),
            ("141A.T", "141A.T"),
            # 米国株 (そのまま)
            ("AAPL", "AAPL"),
            ("NVDA", "NVDA"),
        ],
    )
    def test_conversion(self, ticker: str, expected: str):
        assert to_yfinance_symbol(ticker) == expected
