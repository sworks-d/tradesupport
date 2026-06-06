"""technicals ツールの単体テスト（Task 1.1.6）。固定系列で期待値を検証。"""

from __future__ import annotations

import pytest

from trading_agent.mcp_tools.base import DataNotFoundError, MCPErrorType, NetworkError
from trading_agent.mcp_tools.technicals import (
    TechnicalsInput,
    TechnicalsTool,
    _fetch_ohlcv_yfinance,
    _sma_cross_signal,
    bollinger,
    macd,
    rsi,
    sma,
)


class TestFetchErrorClassification:
    """T4（監査）: yfinance の恒久失敗は DataNotFoundError（非 retry）、一時は NetworkError（retry）。"""

    def test_permanent_yfinance_error_raises_datanotfound(self, monkeypatch) -> None:
        import yfinance

        class _BoomTicker:
            def __init__(self, *a, **k) -> None: ...
            def history(self, *a, **k):
                # 新規上場 XXXA の yfinance 内部クラッシュを模す（恒久）
                raise AttributeError("'PriceHistory' object has no attribute '_dividends'")

        monkeypatch.setattr(yfinance, "Ticker", _BoomTicker)
        with pytest.raises(DataNotFoundError):
            _fetch_ohlcv_yfinance("336A", 90)

    def test_transient_yfinance_error_raises_networkerror(self, monkeypatch) -> None:
        import yfinance

        class _BoomTicker:
            def __init__(self, *a, **k) -> None: ...
            def history(self, *a, **k):
                raise Exception("Connection timed out")  # 一時 → retry 対象

        monkeypatch.setattr(yfinance, "Ticker", _BoomTicker)
        with pytest.raises(NetworkError):
            _fetch_ohlcv_yfinance("7203", 90)


class TestSMA:
    def test_exact(self) -> None:
        assert sma([1, 2, 3, 4, 5], 3) == 4.0

    def test_insufficient(self) -> None:
        assert sma([1, 2], 3) is None


class TestRSI:
    def test_monotonic_up_is_100(self) -> None:
        assert rsi([float(i) for i in range(1, 30)]) == 100.0

    def test_monotonic_down_is_0(self) -> None:
        assert rsi([float(i) for i in range(30, 0, -1)]) == 0.0

    def test_constant_is_neutral(self) -> None:
        assert rsi([10.0] * 30) == 50.0

    def test_insufficient(self) -> None:
        assert rsi([1.0, 2.0], period=14) is None


class TestMACD:
    def test_constant_is_zero(self) -> None:
        result = macd([10.0] * 40)
        assert result is not None
        assert abs(result["value"]) < 1e-9
        assert abs(result["hist"]) < 1e-9

    def test_insufficient(self) -> None:
        assert macd([1.0] * 10) is None


class TestBollinger:
    def test_constant_zero_width(self) -> None:
        result = bollinger([5.0] * 25, period=20)
        assert result is not None
        assert result["upper"] == result["lower"] == result["middle"] == 5.0

    def test_insufficient(self) -> None:
        assert bollinger([1.0] * 5, period=20) is None


class TestCrossSignal:
    def test_golden_cross(self) -> None:
        assert _sma_cross_signal([1.0, 1.0, 3.0], 1, 2) == "golden_cross"

    def test_death_cross(self) -> None:
        assert _sma_cross_signal([3.0, 3.0, 1.0], 1, 2) == "death_cross"

    def test_no_cross(self) -> None:
        assert _sma_cross_signal([1.0, 2.0, 3.0], 1, 2) is None


class TestTool:
    async def test_computes_indicators_and_signals(self) -> None:
        series = [float(i) for i in range(1, 121)]  # 強い上昇トレンド

        def provider(_ticker: str, _days: int) -> list[float]:
            return series

        tool = TechnicalsTool(history_provider=provider)
        out = await tool.execute(TechnicalsInput(ticker="AAPL"))
        assert out.success is True
        assert out.data["rsi"] == 100.0
        assert "sma_20" in out.data and "sma_60" in out.data
        assert "macd" in out.data and "bollinger" in out.data
        # S7 株価底打ち判定用：90日終値の最小・最大
        assert out.data["min_price_90d"] == 1.0
        assert out.data["max_price_90d"] == 120.0
        assert "overbought_rsi" in out.signals
        assert "macd_bullish" in out.signals

    async def test_volume_indicators_opt_in_single_fetch(self) -> None:
        """B-3b/T1: volume 指標を要求したときだけ出来高平均が計算され、かつ close と volume を
        1 回の OHLCV 取得（ohlcv_provider）で同時に得る（2→1 統合・呼び出し回数を固定）。"""
        closes = [float(i) for i in range(1, 121)]
        # 直近5日に出来高サージ（古い→新しい）。最後5本=2000、それ以前=1000。
        volumes = [1000.0] * 115 + [2000.0] * 5

        ohlcv_calls = {"n": 0}
        history_calls = {"n": 0}

        def _ohlcv(_t, _d):
            ohlcv_calls["n"] += 1
            return {"close": closes, "volume": volumes}

        def _history(_t, _d):
            history_calls["n"] += 1
            return closes

        tool = TechnicalsTool(history_provider=_history, ohlcv_provider=_ohlcv)

        # 既定 indicators（volume 無し）→ 出来高は計算されない・closes-only 経路（history のみ）
        out_default = await tool.execute(TechnicalsInput(ticker="AAPL"))
        assert "volume_5d_avg" not in out_default.data
        assert ohlcv_calls["n"] == 0 and history_calls["n"] == 1

        # opt-in 要求 → 計算される・OHLCV を 1 回だけ取得（history は呼ばない＝2→1）
        out = await tool.execute(
            TechnicalsInput(
                ticker="AAPL",
                indicators=["rsi", "volume_5d_avg", "volume_30d_avg"],
            )
        )
        assert out.data["volume_5d_avg"] == 2000.0
        # 直近30本=2000*5 + 1000*25 = 35000 → /30
        assert abs(out.data["volume_30d_avg"] - 35000.0 / 30.0) < 1e-6
        # T1: 出来高経路は OHLCV 1 回のみ（history を追加で叩かない＝yfinance 呼が 2→1）
        assert ohlcv_calls["n"] == 1 and history_calls["n"] == 1  # history は既定経路の1回のみ

    async def test_no_history_is_not_found(self) -> None:
        tool = TechnicalsTool(history_provider=lambda _t, _d: [])
        out = await tool.execute(TechnicalsInput(ticker="ZZZZ"))
        assert out.success is True
        assert out.error_type == MCPErrorType.DATA_NOT_FOUND

    async def test_provider_failure_returns_error(self) -> None:
        def boom(_t: str, _d: int) -> list[float]:
            raise NetworkError("history down")

        tool = TechnicalsTool(history_provider=boom)
        tool.backoff_base = 0.0
        out = await tool.execute(TechnicalsInput(ticker="AAPL"))
        assert out.success is False
        assert out.error_type == MCPErrorType.NETWORK_ERROR
