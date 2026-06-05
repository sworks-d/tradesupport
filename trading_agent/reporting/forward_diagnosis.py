"""Forward 診断ハーネス（codex #4）：約定済の forward mark-to-market 早期診断.

ゲート⑥の正本は 60 日実績だが、約定（6 月）の評価確定は 8 月＝**2 ヶ月の観測空白**。
その間も、今 record し始めた signal_tags / exposure posture の「効いてる兆候」を、
**評価期日を待たず** 5/20/40/60 営業日の対 TOPIX 超過リターンで前倒し診断する。

**ゲートを前倒しで通すためではない**（gate は 60 日実績で守る）。待機期間中の
診断・仮説棄却のための別枠レポート（read-only・売買は変えない）。

設計（推測しない・H10）:
- horizon 未到達（entry から N 営業日経っていない）はスキップ。捏造しない。
- 超過 = (銘柄: entry_price→N日後 のリターン) − (TOPIX: entry日→N日後 のリターン)。
- tag 別 / exposure recommendation 別 / 全体に集計（各 horizon で avg_excess / 勝率/ n）。
価格系列は注入（ネット非依存・テスト可能）。本番は yfinance バッチ + キャッシュ。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.utils.time_utils import today_jst

# series_fetcher(tickers, start_date) -> {ticker: [close, ...]}（index 0 = start日以降の最初の営業日）
SeriesFetcher = Callable[[list[str], dt.date], dict[str, list[float]]]

_OFFICIAL_SOURCES = ("ds_dispatch", "manual")
_DEFAULT_HORIZONS = (5, 20, 40, 60)  # 営業日
_BENCHMARK_TICKER = "1306.T"  # TOPIX 連動 ETF（対市場超過の基準）


def _excess_at(
    forward_closes: list[float], entry_price: float, bench_closes: list[float], n: int
) -> float | None:
    """entry から N 営業日後の対 TOPIX 超過リターン。未到達/欠損は None（推測しない）。"""
    if (
        not entry_price
        or len(forward_closes) <= n
        or len(bench_closes) <= n
        or not bench_closes[0]
    ):
        return None
    stock_ret = forward_closes[n] / entry_price - 1.0
    bench_ret = bench_closes[n] / bench_closes[0] - 1.0
    return stock_ret - bench_ret


def _agg_init(horizons: tuple[int, ...] = _DEFAULT_HORIZONS) -> dict[str, Any]:
    return {h: {"n": 0, "win": 0, "excess_sum": 0.0} for h in horizons}


def _agg_add(bucket: dict[str, Any], excess_by_h: dict[int, float | None]) -> None:
    for h, exc in excess_by_h.items():
        if exc is None:
            continue
        bucket[h]["n"] += 1
        bucket[h]["excess_sum"] += exc
        if exc > 0:
            bucket[h]["win"] += 1


def _agg_finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for h, d in bucket.items():
        n = d["n"]
        out[str(h)] = {
            "n": n,
            "hit_rate": round(d["win"] / n, 4) if n else None,
            "avg_excess": round(d["excess_sum"] / n, 4) if n else None,
        }
    return out


def compute_forward_diagnosis(
    engine: Engine,
    *,
    series_fetcher: SeriesFetcher,
    benchmark_ticker: str = _BENCHMARK_TICKER,
    today: dt.date | None = None,
    broker_mode: str = "paper",
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """約定済 official 決定の forward 超過リターン診断（read-only）。

    Returns: {overall, by_tag, by_exposure, decisions_n, note}。各 horizon ごとに n/hit_rate/avg_excess。
    """
    today = today or today_jst()
    with Session(engine) as s:
        all_decs = list(s.exec(select(Decision)))
    decs = [
        d
        for d in all_decs
        if d.entry_date is not None
        and d.entry_price
        and d.filled_via in _OFFICIAL_SOURCES
        and d.entry_broker_mode == broker_mode
    ]

    overall = _agg_init(horizons)
    by_tag: dict[str, dict[str, Any]] = defaultdict(lambda: _agg_init(horizons))
    by_exposure: dict[str, dict[str, Any]] = defaultdict(lambda: _agg_init(horizons))

    # entry_date でグルーピングして系列を取得（同日約定はまとめて 1 fetch）。
    by_date: dict[dt.date, list[Decision]] = defaultdict(list)
    for d in decs:
        by_date[d.entry_date].append(d)

    for entry_date, group in by_date.items():
        tickers = list({d.ticker for d in group})
        try:
            series = series_fetcher([*tickers, benchmark_ticker], entry_date)
        except Exception:
            continue
        bench = series.get(benchmark_ticker) or []
        for d in group:
            stock = series.get(d.ticker) or []
            exc = {h: _excess_at(stock, float(d.entry_price), bench, h) for h in horizons}
            if all(v is None for v in exc.values()):
                continue  # どの horizon も未到達 → 集計に乗せない
            _agg_add(overall, exc)
            for tag in (d.entry_signal_tags or []):
                _agg_add(by_tag[tag], exc)
            if d.entry_exposure_recommendation:
                _agg_add(by_exposure[d.entry_exposure_recommendation], exc)

    return {
        "overall": _agg_finalize(overall),
        "by_tag": {k: _agg_finalize(v) for k, v in by_tag.items()},
        "by_exposure": {k: _agg_finalize(v) for k, v in by_exposure.items()},
        "decisions_n": len(decs),
        "note": (
            "gate(60日実績)とは別枠の早期診断。horizon 未到達はスキップ（推測しない）。"
            "前倒しでgateを通すためでなく待機期間の仮説棄却用。n が薄い間は参考値。"
        ),
    }


__all__ = ["compute_forward_diagnosis"]
