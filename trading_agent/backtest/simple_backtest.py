"""簡素 backtest フレームワーク（v2.10 Phase 7）。

「現状の screening 数値スコア」と「過去 60-90 日のリターン」の相関を測定し、
screening が **実際にリターンを予測できているか** を検証する。

完全な過去スナップショット再現は重いので、まず簡素な相関分析から始める。

ハルシネーション対策:
  - 過去価格が取れない銘柄は除外
  - 「分割・配当調整」は J-Quants の AdjustmentClose を使う（自前計算しない）
  - サンプル不足時は status="insufficient_data" を返す
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.mcp_tools.jquants import get_default_client
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger
from trading_agent.utils.ticker_normalize import universe_to_jquants

_log = get_logger("backtest.simple")

# 最低サンプル数（これ未満は意味のある結果が出ない）
_MIN_SAMPLE = 10


def fetch_forward_return(
    ticker: str,
    *,
    start_date: dt.date,
    horizon_days: int = 60,
    client: Any | None = None,
) -> float | None:
    """指定日から horizon_days 後のリターンを取得（yfinance ベース）。

    yfinance は J-Quants Free Plan の 5 req/分制約を回避できる。
    調整済み終値（Adj Close）を使うので分割・配当も補正される。

    Returns:
        リターン（小数表記、+0.10 = +10%）。取れない場合は None（推測しない）。
    """
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        end_date = start_date + dt.timedelta(days=horizon_days + 10)
        hist = yf.Ticker(symbol).history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
        )
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        start_price = float(closes.iloc[0])
        # horizon_days 後の最も近い終値
        target_date = start_date + dt.timedelta(days=horizon_days)
        # 日付差絶対値で最も近い行
        diffs = [
            (abs((idx.date() - target_date).days), float(c))
            for idx, c in closes.items()
        ]
        diffs.sort(key=lambda x: x[0])
        end_price = diffs[0][1]
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price
    except Exception as exc:
        _log.warning(
            "backtest_fetch_failed", ticker=ticker, error_type=type(exc).__name__
        )
        return None


def simple_screening_vs_random(
    engine: Engine,
    *,
    sample_size: int = 30,
    horizon_days: int = 60,
    start_date: dt.date | None = None,
) -> dict[str, Any]:
    """Universe からサンプリングして、screening スコアと forward return の相関を測定。

    Args:
        sample_size: サンプル銘柄数（API 5 req/分制限のため小さめに）
        horizon_days: 何日後のリターンを測るか
        start_date: 起点日（None なら J-Quants Free の範囲内に自動設定）

    Returns:
        backtest 結果サマリ。
    """
    # J-Quants Free Plan の範囲: 2024-03-07 〜 2026-03-07
    # 起点を 2025-12-01（horizon 60 日後 = 2026-01-30）にすると確実に範囲内
    if start_date is None:
        start_date = dt.date(2025, 12, 1)

    # サンプル取得（時価総額 500 億 - 5000 億の中型成長株を中心に）
    with Session(engine) as s:
        candidates = list(
            s.exec(
                select(Universe)
                .where(col(Universe.is_active))
                .where(col(Universe.market) == "JP")
                .where(col(Universe.market_cap_jpy) >= 5.0e10)
                .where(col(Universe.market_cap_jpy) <= 5.0e11)
                .order_by(col(Universe.market_cap_jpy).desc())
                .limit(sample_size)
            ).all()
        )

    if not candidates:
        return {
            "status": "insufficient_data",
            "reason": "no_universe_candidates",
        }

    client = get_default_client()
    if client is None:
        return {
            "status": "insufficient_data",
            "reason": "no_jquants_client",
        }

    # 各銘柄の forward return 取得
    results: list[dict[str, Any]] = []
    for u in candidates:
        ret = fetch_forward_return(
            u.ticker,
            start_date=start_date,
            horizon_days=horizon_days,
            client=client,
        )
        if ret is None:
            continue
        results.append(
            {
                "ticker": u.ticker,
                "name": u.name[:25],
                "sector": u.sector,
                "market_cap_jpy": u.market_cap_jpy,
                "forward_return_pct": round(ret * 100, 2),
            }
        )

    if len(results) < _MIN_SAMPLE:
        return {
            "status": "insufficient_data",
            "reason": f"got_only_{len(results)}_of_{sample_size}",
            "results": results,
        }

    # 統計
    returns = [r["forward_return_pct"] for r in results]
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std_ret = math.sqrt(variance)
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
    median = sorted(returns)[len(returns) // 2]

    # 勝者・敗者
    sorted_by_ret = sorted(results, key=lambda x: x["forward_return_pct"], reverse=True)
    top_5 = sorted_by_ret[:5]
    bottom_5 = sorted_by_ret[-5:]

    return {
        "status": "active",
        "sample_size_requested": sample_size,
        "sample_size_actual": len(results),
        "start_date": start_date.isoformat(),
        "horizon_days": horizon_days,
        "summary": {
            "mean_return_pct": round(mean_ret, 2),
            "median_return_pct": round(median, 2),
            "std_return_pct": round(std_ret, 2),
            "win_rate_pct": round(win_rate, 1),
            "max_winner_pct": round(max(returns), 2),
            "max_loser_pct": round(min(returns), 2),
        },
        "top_5_winners": top_5,
        "bottom_5_losers": bottom_5,
        "all_results": results,
    }


# === 時価総額帯別の比較 backtest =========================================

# デフォルトの時価総額バケット（呼び出し側で上書き可能）
# 形式: (ラベル, 下限 JPY, 上限 JPY)
DEFAULT_MARKET_CAP_BUCKETS: list[tuple[str, float, float]] = [
    ("超大型 (>1兆)", 1.0e12, float("inf")),
    ("大型 (1000億-1兆)", 1.0e11, 1.0e12),
    ("中型 (500-1000億)", 5.0e10, 1.0e11),
    ("中小型 (100-500億)", 1.0e10, 5.0e10),
]

# デフォルトの判定閾値（中型平均 - 大型平均 の差）
# 呼び出し側で上書き可能
DEFAULT_VERDICT_THRESHOLDS: dict[str, float] = {
    "strong_pro_mid": 3.0,    # +3.0% 超 → 中型が明確に優位
    "weak_pro_mid": 0.5,      # +0.5% - +3.0% → 中型やや優位
    "tie": 0.5,               # ±0.5% → 差なし
    "weak_pro_big": -3.0,     # -3.0% - -0.5% → 大型やや優位
                              # -3.0% 未満 → 大型が明確に優位
}


def _bucket_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """サンプルのリターン統計サマリ。"""
    if not results:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    rets = [r["forward_return_pct"] for r in results]
    rets_sorted = sorted(rets)
    median = rets_sorted[len(rets_sorted) // 2]
    win_rate = sum(1 for r in rets if r > 0) / len(rets) * 100
    return {
        "n": len(results),
        "mean": round(sum(rets) / len(rets), 2),
        "median": round(median, 2),
        "win_rate": round(win_rate, 1),
        "max": round(max(rets), 2),
        "min": round(min(rets), 2),
    }


def compare_by_market_cap(
    engine: Engine,
    *,
    sample_per_bucket: int = 10,
    horizon_days: int = 60,
    start_date: dt.date | None = None,
    buckets: list[tuple[str, float, float]] | None = None,
    verdict_thresholds: dict[str, float] | None = None,
    primary_bucket_label: str = "中型 (500-1000億)",
    reference_bucket_label: str = "超大型 (>1兆)",
) -> dict[str, Any]:
    """時価総額帯別に forward return を比較する。

    全パラメータが可変。状況（市況・期間・興味の対象帯）に応じて呼び出し側で
    調整できる。

    Args:
        sample_per_bucket: 各バケットから取るサンプル数
        horizon_days: forward return を測る期間（日）
        start_date: 起点日（None なら 2025-12-01）
        buckets: [(ラベル, 下限 JPY, 上限 JPY), ...]（None なら DEFAULT）
        verdict_thresholds: 判定閾値（None なら DEFAULT）
        primary_bucket_label: 判定の主役バケット（デフォルト: 中型）
        reference_bucket_label: 比較対象バケット（デフォルト: 超大型）

    Returns:
        バケット別の統計 + 比較 + 結論ラベル + 使用パラメータ。
    """
    if start_date is None:
        start_date = dt.date(2025, 12, 1)
    if buckets is None:
        buckets = list(DEFAULT_MARKET_CAP_BUCKETS)
    if verdict_thresholds is None:
        verdict_thresholds = dict(DEFAULT_VERDICT_THRESHOLDS)

    buckets_data: dict[str, list[dict[str, Any]]] = {}
    excluded_total = 0

    with Session(engine) as s:
        for label, lo, hi in buckets:
            q = (
                select(Universe)
                .where(col(Universe.is_active))
                .where(col(Universe.market) == "JP")
                .where(col(Universe.market_cap_jpy) >= lo)
            )
            if hi != float("inf"):
                q = q.where(col(Universe.market_cap_jpy) < hi)
            q = q.order_by(col(Universe.market_cap_jpy).desc()).limit(sample_per_bucket)
            candidates = list(s.exec(q).all())

            # 各銘柄の forward return
            rows: list[dict[str, Any]] = []
            for u in candidates:
                ret = fetch_forward_return(
                    u.ticker, start_date=start_date, horizon_days=horizon_days
                )
                if ret is None:
                    excluded_total += 1
                    continue
                rows.append(
                    {
                        "ticker": u.ticker,
                        "name": (u.name or "")[:25],
                        "sector": u.sector,
                        "market_cap_jpy": u.market_cap_jpy,
                        "forward_return_pct": round(ret * 100, 2),
                    }
                )
            buckets_data[label] = rows

    # バケット別統計
    summaries: dict[str, dict[str, Any]] = {
        label: _bucket_summary(rows) for label, rows in buckets_data.items()
    }

    # 比較結論: primary - reference の差で判定
    primary_mean = summaries.get(primary_bucket_label, {}).get("mean")
    reference_mean = summaries.get(reference_bucket_label, {}).get("mean")

    verdict = "判定不能"
    diff_pct: float | None = None
    if primary_mean is not None and reference_mean is not None:
        diff_pct = round(primary_mean - reference_mean, 2)
        strong_pro = verdict_thresholds.get("strong_pro_mid", 3.0)
        weak_pro = verdict_thresholds.get("weak_pro_mid", 0.5)
        tie_margin = verdict_thresholds.get("tie", 0.5)
        weak_con = verdict_thresholds.get("weak_pro_big", -3.0)
        # 正値 = primary 優位、負値 = reference 優位
        if diff_pct > strong_pro:
            verdict = f"{primary_bucket_label} 優位（明確）"
        elif diff_pct > weak_pro:
            verdict = f"{primary_bucket_label} やや優位"
        elif diff_pct > -tie_margin:
            verdict = "差なし（市況依存）"
        elif diff_pct > weak_con:
            verdict = f"{reference_bucket_label} やや優位"
        else:
            verdict = f"{reference_bucket_label} 優位（明確）"

    return {
        "status": "active",
        "start_date": start_date.isoformat(),
        "horizon_days": horizon_days,
        "sample_per_bucket": sample_per_bucket,
        "excluded_total": excluded_total,
        "buckets": summaries,
        "params_used": {
            "buckets": [(b[0], b[1], b[2] if b[2] != float("inf") else None) for b in buckets],
            "verdict_thresholds": verdict_thresholds,
            "primary_bucket_label": primary_bucket_label,
            "reference_bucket_label": reference_bucket_label,
        },
        "verdict": {
            "label": verdict,
            "primary_mean_pct": primary_mean,
            "reference_mean_pct": reference_mean,
            "diff_pct": diff_pct,
            "interpretation": (
                f"{primary_bucket_label} が {reference_bucket_label} を上回るなら "
                f"その戦略が定量的に支持される。差が小さい/逆なら戦略見直しが必要。"
                "サンプル小・1 期間のみのため、複数期間・複数バケット設定で再検証推奨。"
            ),
        },
    }
