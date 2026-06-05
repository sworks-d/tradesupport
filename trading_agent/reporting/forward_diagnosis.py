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


def _current_excess(
    forward_closes: list[float], entry_price: float, bench_closes: list[float]
) -> tuple[float, int] | None:
    """codex P2: entry から **今日（最新取得 close）まで** の対 TOPIX 超過 + 経過 bar 数。

    若い position（固定 horizon 未到達）でも即時の含み兆候が見える暫定値。日数不揃いに注意。
    """
    if (
        not entry_price
        or len(forward_closes) < 2
        or len(bench_closes) < 2
        or not bench_closes[0]
    ):
        return None
    bars = min(len(forward_closes), len(bench_closes)) - 1  # entry を 0 とした経過営業日
    stock_ret = forward_closes[bars] / entry_price - 1.0
    bench_ret = bench_closes[bars] / bench_closes[0] - 1.0
    return stock_ret - bench_ret, bars


def _current_finalize(vals: list[float], bars: list[int]) -> dict[str, Any]:
    n = len(vals)
    return {
        "n": n,
        "hit_rate": round(sum(1 for v in vals if v > 0) / n, 4) if n else None,
        "avg_excess": round(sum(vals) / n, 4) if n else None,
        "avg_bars_held": round(sum(bars) / n, 1) if n else None,  # 日数不揃いの目安
    }


def _tag_control_compare(
    records: list[dict[str, Any]], horizons: tuple[int, ...]
) -> dict[str, Any]:
    """codex P2/#3: 各 horizon で tag with/without の avg_excess 差（forward の正味エッジ）。

    by_tag の絶対成績は地合い・fill 選定バイアスを受けるため、同 forward universe 内で
    tag あり/なし cohort を対照する。control が薄い horizon は n を見て参考扱い。
    """
    all_tags: set[str] = set()
    for r in records:
        all_tags.update(r["tags"])
    out: dict[str, Any] = {}
    for tag in sorted(all_tags):
        per_h = {}
        for h in horizons:
            with_v = [r["exc"][h] for r in records if tag in r["tags"] and r["exc"][h] is not None]
            wo_v = [r["exc"][h] for r in records if tag not in r["tags"] and r["exc"][h] is not None]
            aw = (sum(with_v) / len(with_v)) if with_v else None
            awo = (sum(wo_v) / len(wo_v)) if wo_v else None
            per_h[str(h)] = {
                "with_n": len(with_v),
                "without_n": len(wo_v),
                "with_avg_excess": round(aw, 4) if aw is not None else None,
                "without_avg_excess": round(awo, 4) if awo is not None else None,
                "net_avg_excess": (
                    round(aw - awo, 4) if (aw is not None and awo is not None) else None
                ),
            }
        out[tag] = per_h
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
    records: list[dict[str, Any]] = []  # tag with/without control 用に per-decision を保持
    cur_vals: list[float] = []  # current_mtm（entry→今日）の超過
    cur_bars: list[int] = []

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
            tags = list(d.entry_signal_tags or [])
            # current_mtm: 固定 horizon 未到達でも entry→今日 の暫定超過は出せる（若い position 向け）。
            cur = _current_excess(stock, float(d.entry_price), bench)
            if cur is not None:
                cur_vals.append(cur[0])
                cur_bars.append(cur[1])
            if all(v is None for v in exc.values()):
                # 固定 horizon は未到達でも、control 用に exc(None) は records に残す
                records.append({"tags": tags, "exc": exc})
                continue
            _agg_add(overall, exc)
            for tag in tags:
                _agg_add(by_tag[tag], exc)
            if d.entry_exposure_recommendation:
                _agg_add(by_exposure[d.entry_exposure_recommendation], exc)
            records.append({"tags": tags, "exc": exc})

    return {
        "overall": _agg_finalize(overall),
        "by_tag": {k: _agg_finalize(v) for k, v in by_tag.items()},
        "by_exposure": {k: _agg_finalize(v) for k, v in by_exposure.items()},
        # codex P2: 若い position 向けの暫定含み超過（entry→今日・日数不揃い）。
        "current_mtm": _current_finalize(cur_vals, cur_bars),
        # codex P2/#3: tag with/without control（地合い/fill バイアス補正）。
        "tag_vs_control": _tag_control_compare(records, horizons),
        "decisions_n": len(decs),
        # codex P1: base 混在の注記。銘柄=実 entry_price 基準 / TOPIX=entry日 series[0] close 基準。
        "benchmark_base": "stock=entry_price / benchmark=entry_date_close（基準時点が厳密一致でない）",
        "note": (
            "gate(60日実績)とは別枠の早期診断。固定 horizon 未到達はスキップ（推測しない）。"
            "current_mtm は日数不揃いの暫定値。前倒しで gate を通すためでなく待機期間の仮説棄却用。"
            "n が薄い間は参考値・tag は with/without control も併記。"
        ),
    }


__all__ = ["compute_forward_diagnosis"]
