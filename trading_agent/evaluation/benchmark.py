"""A4: 保有期間ベンチマーク（TOPIX）リターン lookup。

`evaluate_due_decisions` の `benchmark_lookup` に渡す。decision の保有期間
（entry 日＝decision.date → exit 日＝evaluation_date、未到来なら today）の
TOPIX リターンを返す。これにより `excess_return`（対ベンチ超過＝α）と、P0.5 の
`net_excess`（コスト後α）が算定できる。

設計:
  - 日付→終値の純粋計算（`holding_period_return`）と、yfinance 取得の factory を分離
    （純粋部分はネット非依存でテスト可能）。
  - 同期間に同期する。当日騰落ではない（codex 指摘）。
  - 価格欠損・期間不正は None（推測しない）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from trading_agent.models.decisions import Decision
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import today_jst

_log = get_logger("evaluation.benchmark")

# TOPIX 連動 ETF（^TPX は yfinance 非対応のため ETF で代替・backtest エンジンと統一）。
DEFAULT_BENCHMARK_TICKER = "1306.T"


def _close_on_or_before(closes: dict[dt.date, float], target: dt.date) -> float | None:
    """target 日（含む）以前で最も近い終値。土日祝・欠損をまたいで遡る（最大 10 日）。"""
    for back in range(0, 11):
        day = target - dt.timedelta(days=back)
        if day in closes:
            return closes[day]
    return None


def holding_period_return(
    closes: dict[dt.date, float], entry: dt.date, exit_day: dt.date
) -> float | None:
    """[entry, exit_day] の保有期間リターン（純粋関数）。価格欠損・期間不正は None。"""
    if exit_day <= entry:
        return None
    p0 = _close_on_or_before(closes, entry)
    p1 = _close_on_or_before(closes, exit_day)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (p1 - p0) / p0


def make_topix_benchmark_lookup(
    *,
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    today: dt.date | None = None,
    closes: dict[dt.date, float] | None = None,
) -> Callable[[Decision], float | None]:
    """decision の保有期間 TOPIX リターンを返す lookup を生成。

    `closes`（日付→終値）を渡せばネット非依存（テスト用）。未指定なら yfinance で
    2 年分の TOPIX ETF 終値を 1 回取得してキャッシュする。
    """
    day_now = today or today_jst()
    series = closes if closes is not None else _fetch_topix_closes(benchmark_ticker)

    def lookup(d: Decision) -> float | None:
        if not series:
            return None
        # A7: benchmark 起点は実約定日（entry_date）優先。遅延 fill（manual/live）で
        #     d.date（decision 日）と乖離する場合の α 歪みを防ぐ。無ければ d.date。
        entry = d.entry_date or d.date
        # 評価期日が未到来なら today までの期間で暫定評価（price_lookup 側と整合）。
        exit_day = d.evaluation_date or day_now
        if exit_day > day_now:
            exit_day = day_now
        return holding_period_return(series, entry, exit_day)

    return lookup


def _fetch_topix_closes(ticker: str) -> dict[dt.date, float]:
    """yfinance で TOPIX ETF の日次終値を取得（失敗時は空 dict＝benchmark なし扱い）。"""
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=True)
        out: dict[dt.date, float] = {}
        for idx, close in df["Close"].dropna().items():
            out[idx.date()] = float(close)
        return out
    except Exception as exc:
        _log.warning("topix_fetch_failed", error_type=type(exc).__name__)
        return {}
