"""ポートフォリオ相関分析（v2.10 Phase 1）。

保有銘柄の 30 日リターンから相関行列を計算し、隠れ集中（同方向の値動き）を検出する。
セクター分散が効いていても、実際の値動きが同期している銘柄ペアを発見できる。

主な指標:
  - 相関行列: 銘柄 N × N の Pearson 相関
  - 強相関ペア: |corr| ≥ 0.7 のペア（隠れ集中アラート）
  - max_corr / mean_abs_corr: ポートフォリオ全体の集中度サマリ

ハルシネーション対策:
  - 価格データが取れない銘柄は計算から除外（None 扱い・推測しない）
  - 標準偏差ゼロ等で NaN になる相関係数は 0.0 にクランプ
  - データ不足時は status="insufficient_data" を返す
  - 株価ソースは yfinance のみ（J-Quants Free は遅延あり）
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.correlation")

# 「強相関」しきい値（|r| がこれを超えるペアは隠れ集中アラート）
_HIGH_CORR_THRESHOLD = 0.7
# 相関計算に最低必要な日次データ点数
_MIN_DATA_POINTS = 20
# 取得対象期間（営業日換算で 30 日分を取るため 60 暦日）
_HISTORY_PERIOD = "60d"


def fetch_daily_returns(ticker: str) -> list[float] | None:
    """30 日分の日次リターンを取得（yfinance）。

    Returns:
        日次リターン list（None なら計算不可・推測しない）。
    """
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        hist = yf.Ticker(symbol).history(period=_HISTORY_PERIOD)
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna().values
        if len(closes) < _MIN_DATA_POINTS:
            return None
        # 日次リターン = (今日 / 昨日) - 1
        returns = (closes[1:] / closes[:-1] - 1.0).tolist()
        return [float(r) for r in returns]
    except Exception as exc:
        _log.warning(
            "fetch_daily_returns_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def compute_correlation_matrix(
    engine: Engine, broker_mode: str = "paper"
) -> dict[str, Any]:
    """保有銘柄の相関行列を計算する。

    Args:
        engine: DB エンジン
        broker_mode: "paper" or "live" の Portfolio を対象に

    Returns:
        相関分析結果 dict。データ不足時は status="insufficient_data"。
    """
    # 保有銘柄の取得
    with Session(engine) as s:
        ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )

    tickers = sorted({p.ticker for p in ports})
    if len(tickers) < 2:
        return _empty_result(
            tickers=tickers, reason="less_than_2_positions"
        )

    # 各銘柄のリターン取得（取れないものは除外・推測しない）
    returns_by_ticker: dict[str, list[float]] = {}
    for t in tickers:
        r = fetch_daily_returns(t)
        if r is not None and len(r) >= _MIN_DATA_POINTS:
            returns_by_ticker[t] = r

    if len(returns_by_ticker) < 2:
        return _empty_result(
            tickers=tickers,
            reason="less_than_2_with_sufficient_data",
            data_available=len(returns_by_ticker),
        )

    # 計算可能な銘柄だけで配列を作る（共通の最短長に揃える）
    valid_tickers = sorted(returns_by_ticker.keys())
    min_len = min(len(returns_by_ticker[t]) for t in valid_tickers)
    arrays = np.array(
        [returns_by_ticker[t][-min_len:] for t in valid_tickers]
    )

    # 相関行列（Pearson）。NaN（std=0 等）は 0.0 にクランプ
    with np.errstate(invalid="ignore"):
        corr = np.corrcoef(arrays)
    if np.any(np.isnan(corr)):
        corr = np.nan_to_num(corr, nan=0.0)

    # 上三角（i<j）で集計
    n = len(valid_tickers)
    high_pairs: list[dict[str, Any]] = []
    abs_corrs: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            c = float(corr[i, j])
            abs_corrs.append(abs(c))
            if abs(c) >= _HIGH_CORR_THRESHOLD:
                high_pairs.append(
                    {
                        "a": valid_tickers[i],
                        "b": valid_tickers[j],
                        "correlation": round(c, 3),
                    }
                )

    # 強相関ペアを |corr| 降順で
    high_pairs.sort(key=lambda x: abs(float(x["correlation"])), reverse=True)

    max_corr: float | None = (
        round(max(abs_corrs), 3) if abs_corrs else None
    )
    mean_abs: float | None = (
        round(sum(abs_corrs) / len(abs_corrs), 3) if abs_corrs else None
    )

    # 集中度ラベル: mean_abs_corr から「分散良/中/集中」を分類
    concentration_label = "n/a"
    if mean_abs is not None:
        if mean_abs < 0.3:
            concentration_label = "分散良"
        elif mean_abs < 0.5:
            concentration_label = "中程度"
        else:
            concentration_label = "集中（要注意）"

    return {
        "status": "active",
        "tickers": valid_tickers,
        "matrix": [[round(float(x), 3) for x in row] for row in corr.tolist()],
        "high_correlation_pairs": high_pairs,
        "high_correlation_threshold": _HIGH_CORR_THRESHOLD,
        "max_corr": max_corr,
        "mean_abs_corr": mean_abs,
        "concentration_label": concentration_label,
        "data_points": min_len,
        "excluded_tickers": [
            t for t in tickers if t not in returns_by_ticker
        ],
    }


def assess_new_buy_correlation(
    engine: Engine,
    new_ticker: str,
    *,
    broker_mode: str = "paper",
    threshold: float = _HIGH_CORR_THRESHOLD,
    returns_cache: dict[str, list[float] | None] | None = None,
) -> dict[str, Any]:
    """新規 buy 候補と保有銘柄の相関を評価（v2.10 Phase H-6）。

    保有銘柄のいずれかと |corr| ≥ threshold なら blocked=True。
    auto モードの auto_fill で前段フィルターとして使う。

    パフォーマンス改善 (v2.10):
      - returns_cache を渡すと同じ ticker のリターンは 1 度だけ fetch する。
      - 呼び出し側で空 dict を作り候補ループ前に渡せば、保有銘柄分の
        yfinance API は最大「保有銘柄数」回に圧縮される（候補数 × 保有数 → 候補数 + 保有数）。

    ハルシネーション対策:
      - 価格データが取れない銘柄は計算から除外 → blocked=False（推測しない）
      - 保有銘柄ゼロ → blocked=False
      - 新規候補が既に保有中（ピラミッディング）→ blocked=False（H-6 対象外）

    Returns:
      {
        "blocked": bool,
        "max_corr": float | None,
        "max_pair": str | None,
        "reason": str,
      }
    """

    def _get_returns(t: str) -> list[float] | None:
        if returns_cache is not None and t in returns_cache:
            return returns_cache[t]
        r = fetch_daily_returns(t)
        if returns_cache is not None:
            returns_cache[t] = r
        return r

    with Session(engine) as s:
        ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )
    held = sorted({p.ticker for p in ports})
    if not held:
        return {
            "blocked": False,
            "max_corr": None,
            "max_pair": None,
            "reason": "no_holdings",
        }
    if new_ticker in held:
        return {
            "blocked": False,
            "max_corr": None,
            "max_pair": None,
            "reason": "already_held_pyramid",
        }

    new_returns = _get_returns(new_ticker)
    if new_returns is None:
        return {
            "blocked": False,
            "max_corr": None,
            "max_pair": None,
            "reason": "no_data_for_new",
        }

    max_abs = 0.0
    max_corr_value: float | None = None
    max_pair: str | None = None
    for held_ticker in held:
        held_returns = _get_returns(held_ticker)
        if held_returns is None:
            continue
        min_len = min(len(new_returns), len(held_returns))
        if min_len < _MIN_DATA_POINTS:
            continue
        arr = np.array([new_returns[-min_len:], held_returns[-min_len:]])
        with np.errstate(invalid="ignore"):
            corr = np.corrcoef(arr)
        c_raw = corr[0, 1]
        c = float(c_raw) if not np.isnan(c_raw) else 0.0
        if abs(c) > max_abs:
            max_abs = abs(c)
            max_corr_value = c
            max_pair = held_ticker

    if max_pair is not None and max_abs >= threshold:
        return {
            "blocked": True,
            "max_corr": round(max_corr_value, 3) if max_corr_value is not None else None,
            "max_pair": max_pair,
            "reason": f"高相関 r={max_corr_value:.2f} vs {max_pair} (閾値 {threshold:.2f})",
        }
    return {
        "blocked": False,
        "max_corr": round(max_corr_value, 3) if max_corr_value is not None else None,
        "max_pair": max_pair,
        "reason": "ok",
    }


def _empty_result(
    *,
    tickers: list[str],
    reason: str,
    data_available: int | None = None,
) -> dict[str, Any]:
    """データ不足時の標準応答（ハルシネーション防止）。"""
    result: dict[str, Any] = {
        "status": "insufficient_data",
        "tickers": tickers,
        "matrix": [],
        "high_correlation_pairs": [],
        "high_correlation_threshold": _HIGH_CORR_THRESHOLD,
        "max_corr": None,
        "mean_abs_corr": None,
        "concentration_label": "n/a",
        "data_points": 0,
        "excluded_tickers": [],
        "reason": reason,
    }
    if data_available is not None:
        result["data_available"] = data_available
    return result
