"""technicals MCP ツール（SYSTEM_DESIGN.md §3.2）。

テクニカル指標（RSI / MACD / SMA / ボリンジャーバンド）を計算し、シグナル名を返す。

実装方針（Task 1.1.6）:
- TA-Lib は C ライブラリ依存で `uv sync` を壊すため使わず、numpy/pandas で自前計算
  （IMPLEMENTATION_PHASES が「TA-Lib または pandas_ta」を許容）。少数の標準指標のため
  自前計算が確実かつ固定系列で検証容易。
- 過去データ（終値系列）は ``history_provider`` を注入（既定 yfinance、moomoo 接続時に差替）。
- シグナル名は定義済み（"overbought_rsi" / "golden_cross" 等）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from pydantic import Field

from trading_agent.mcp_tools.base import (
    DataNotFoundError,
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
    SourceRef,
)
from trading_agent.utils.time_utils import utcnow

# 終値系列の取得関数の型：(ticker, period_days) → 終値リスト（古い→新しい）
HistoryProvider = Callable[[str, int], list[float]]


def _default_indicators() -> list[str]:
    return ["rsi", "macd", "sma_20", "sma_60", "bollinger"]


class TechnicalsInput(MCPToolInput):
    ticker: str
    indicators: list[str] = Field(default_factory=_default_indicators)
    period_days: int = 90


class TechnicalsOutput(MCPToolOutput):
    data: dict[str, Any] = Field(default_factory=dict)
    signals: list[str] = Field(default_factory=list)


# ---- 指標計算（純粋関数。固定系列で検証可能） --------------------------------


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    result = pd.Series(values).rolling(period).mean().iloc[-1]
    return None if pd.isna(result) else float(result)


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    series = pd.Series(values)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0  # 上昇のみ=100 / 変化なし=中立50
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, float] | None:
    if len(values) < slow:
        return None
    series = pd.Series(values)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {
        "value": float(macd_line.iloc[-1]),
        "signal": float(signal_line.iloc[-1]),
        "hist": float(macd_line.iloc[-1] - signal_line.iloc[-1]),
    }


def bollinger(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, float] | None:
    if len(values) < period:
        return None
    series = pd.Series(values)
    mid = series.rolling(period).mean().iloc[-1]
    std = series.rolling(period).std(ddof=0).iloc[-1]
    if pd.isna(mid) or pd.isna(std):
        return None
    return {
        "upper": float(mid + num_std * std),
        "middle": float(mid),
        "lower": float(mid - num_std * std),
    }


def _sma_cross_signal(values: list[float], short: int, long: int) -> str | None:
    """直近で短期SMAが長期SMAを上抜け→golden_cross、下抜け→death_cross。"""
    if len(values) < long + 1:
        return None
    s = pd.Series(values)
    short_ma = s.rolling(short).mean()
    long_ma = s.rolling(long).mean()
    prev_diff = short_ma.iloc[-2] - long_ma.iloc[-2]
    curr_diff = short_ma.iloc[-1] - long_ma.iloc[-1]
    if pd.isna(prev_diff) or pd.isna(curr_diff):
        return None
    if prev_diff <= 0 < curr_diff:
        return "golden_cross"
    if prev_diff >= 0 > curr_diff:
        return "death_cross"
    return None


# ---- ツール本体 ------------------------------------------------------------


class TechnicalsTool(MCPTool[TechnicalsInput]):
    """テクニカル指標の計算ツール。"""

    name = "technicals"
    description = "RSI/MACD/SMA/ボリンジャーを計算し、シグナル名を返す。"
    input_schema = TechnicalsInput
    output_schema = TechnicalsOutput

    def __init__(self, *, history_provider: HistoryProvider | None = None) -> None:
        self._history: HistoryProvider = history_provider or _fetch_history_yfinance

    async def _execute(self, tool_input: TechnicalsInput) -> MCPToolOutput:
        closes = self._history(tool_input.ticker, tool_input.period_days)
        if not closes:
            raise DataNotFoundError(f"no price history for {tool_input.ticker}")

        data: dict[str, Any] = {}
        for indicator in tool_input.indicators:
            value = self._compute(indicator, closes)
            if value is not None:
                data[indicator] = value

        signals = self._signals(tool_input.indicators, closes, data)
        now = utcnow()
        # テクニカルはコード計算（LLMに計算させない原則の体現）。出典=computed。
        ref = SourceRef(
            source="computed",
            ref=tool_input.ticker,
            as_of=now,
            note="technical indicators computed from price history",
        )
        return TechnicalsOutput(
            success=True, data=data, signals=signals, data_asof=now, source_refs=[ref]
        )

    def _compute(self, indicator: str, closes: list[float]) -> Any:
        if indicator == "rsi":
            return rsi(closes)
        if indicator == "macd":
            return macd(closes)
        if indicator == "bollinger":
            return bollinger(closes)
        if indicator.startswith("sma_"):
            try:
                period = int(indicator.split("_", 1)[1])
            except ValueError:
                return None
            return sma(closes, period)
        return None

    def _signals(
        self, indicators: list[str], closes: list[float], data: dict[str, Any]
    ) -> list[str]:
        signals: list[str] = []
        rsi_val = data.get("rsi")
        if isinstance(rsi_val, int | float):
            if rsi_val >= 70:
                signals.append("overbought_rsi")
            elif rsi_val <= 30:
                signals.append("oversold_rsi")

        macd_val = data.get("macd")
        if isinstance(macd_val, dict):
            signals.append("macd_bullish" if macd_val["hist"] > 0 else "macd_bearish")

        if "sma_20" in indicators and "sma_60" in indicators:
            cross = _sma_cross_signal(closes, 20, 60)
            if cross:
                signals.append(cross)

        boll = data.get("bollinger")
        if isinstance(boll, dict) and closes:
            price = closes[-1]
            if price > boll["upper"]:
                signals.append("bollinger_breakout_up")
            elif price < boll["lower"]:
                signals.append("bollinger_breakout_down")

        return signals


def _fetch_history_yfinance(ticker: str, period_days: int) -> list[float]:
    import yfinance as yf

    try:
        hist = yf.Ticker(ticker).history(period=f"{period_days}d")
    except Exception as exc:
        raise NetworkError(f"yfinance history failed: {exc}") from exc
    if hist.empty:
        return []
    return [float(x) for x in hist["Close"].tolist()]
