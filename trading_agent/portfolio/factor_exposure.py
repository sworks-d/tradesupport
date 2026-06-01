"""factor exposure 分析（v2.10 Phase 5）。

ポートフォリオの保有銘柄が **どの factor に偏っているか** を可視化。
ファクター: momentum / value / quality / size の 4 軸（個人レベルで実装可能な範囲）。

各銘柄の factor スコアを 0-100 で計算し、ポートフォリオ全体の加重平均で偏りを測る。

ファクター定義（個人 backtest 経験ベース・簡素化版）:
  momentum:  3 ヶ月リターン（上位ほど高スコア）
  value:     PER の逆数（低 PER ほど value 寄り = 高スコア）
  quality:   ROE × 自己資本比率（高いほど質高い）
  size:      時価総額の log 逆数（小さいほど size factor 寄り = 高スコア）

ハルシネーション対策:
  - データが取れない指標は None（推測しない・0 で埋めない）
  - 全銘柄でデータ揃わない場合は status="insufficient_data"
  - 加重平均から欠損銘柄は除外（按分しない）
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.factor_exposure")


def _safe_log10(x: float) -> float | None:
    if x <= 0:
        return None
    return math.log10(x)


# === v2.10 Phase 1B: 残り 3 軸のデータ取得（None なら推測しない）===========

def _fetch_3m_return(ticker: str) -> float | None:
    """yfinance で過去 3 ヶ月リターン（None なら計算不能・推測しない）。"""
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        hist = yf.Ticker(symbol).history(period="90d")
        if hist is None or hist.empty or len(hist) < 5:
            return None
        closes = hist["Close"].dropna().values
        if len(closes) < 2 or closes[0] <= 0:
            return None
        return float((closes[-1] - closes[0]) / closes[0])
    except Exception as exc:
        _log.warning(
            "factor_3m_return_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def _fetch_trailing_pe(ticker: str) -> float | None:
    """yfinance.info.trailingPE（None なら取れない・推測しない）。"""
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        info = yf.Ticker(symbol).info
        if not info:
            return None
        per = info.get("trailingPE") or info.get("forwardPE")
        if per is None:
            return None
        per = float(per)
        # 異常値（負・極端に大）はクランプせず None（推測しない）
        if per <= 0 or per > 1000:
            return None
        return per
    except Exception as exc:
        _log.warning(
            "factor_per_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def _fetch_roe_and_eqratio(ticker: str) -> tuple[float | None, float | None]:
    """J-Quants statements から ROE と EqAR（自己資本比率）を取得。

    ROE = NP / Eq （簡素近似・期中平均ではなく期末）
    EqAR は J-Quants の EqAR フィールドをそのまま使う。

    JP 株のみ動作。取れない場合は (None, None)。
    """
    try:
        from trading_agent.mcp_tools.jquants import get_default_client
        from trading_agent.utils.ticker_normalize import is_jp_ticker, universe_to_jquants

        if not is_jp_ticker(ticker):
            return None, None
        client = get_default_client()
        if client is None:
            return None, None
        jq_code = universe_to_jquants(ticker)
        statements = client.statements(ticker=jq_code)
        if not statements:
            return None, None

        # 直近の通期 (FY) を優先、無ければ最新
        latest_fy = None
        for stmt in statements:
            if stmt.get("CurPerType") == "FY":
                latest_fy = stmt
                break
        target = latest_fy or statements[0]

        def _f(key: str) -> float | None:
            v = target.get(key)
            if v is None or v == "":
                return None
            try:
                f = float(v)
                if f != f:
                    return None
                return f
            except (TypeError, ValueError):
                return None

        np_ = _f("NP")
        eq = _f("Eq")
        eq_ratio = _f("EqAR")

        roe: float | None = None
        if np_ is not None and eq is not None and eq > 0:
            roe = np_ / eq

        return roe, eq_ratio
    except Exception as exc:
        _log.warning(
            "factor_roe_eqratio_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None, None


def compute_factor_scores(
    *,
    market_cap_jpy: float | None,
    momentum_3m: float | None,
    per: float | None,
    roe: float | None,
    equity_asset_ratio: float | None,
) -> dict[str, float | None]:
    """銘柄ごとの factor スコアを 0-100 で計算。

    取れない指標は None のまま（推測しない）。
    """
    scores: dict[str, float | None] = {
        "momentum": None,
        "value": None,
        "quality": None,
        "size": None,
    }

    # momentum: 3ヶ月リターンを 0-100 に（-30% → 0、+30% → 100、リニア）
    if momentum_3m is not None:
        s = (momentum_3m + 0.30) / 0.60 * 100.0
        scores["momentum"] = round(max(0.0, min(100.0, s)), 1)

    # value: PER の逆数（低 PER ほど value 寄り）。PER 5-30 → 100-0 にリニア
    if per is not None and per > 0:
        # PER 5 → 100, PER 30 → 0、それ以外はクランプ
        s = (30.0 - per) / (30.0 - 5.0) * 100.0
        scores["value"] = round(max(0.0, min(100.0, s)), 1)

    # quality: ROE × 自己資本比率
    if roe is not None and equity_asset_ratio is not None:
        # ROE 0-25%, EqAR 0.2-0.8 をそれぞれ 0-1 に
        roe_n = max(0.0, min(1.0, roe / 0.25))
        eq_n = max(0.0, min(1.0, (equity_asset_ratio - 0.2) / 0.6))
        s = (roe_n * 0.7 + eq_n * 0.3) * 100.0
        scores["quality"] = round(s, 1)

    # size: 時価総額の log 逆数。小さいほど size factor 寄り
    if market_cap_jpy is not None and market_cap_jpy > 0:
        log_mcap = math.log10(market_cap_jpy)  # 10億=10、1兆=12、10兆=13
        # log10=12（1兆）が中央。log 9-14 を 100-0 にリニア
        s = (14.0 - log_mcap) / (14.0 - 9.0) * 100.0
        scores["size"] = round(max(0.0, min(100.0, s)), 1)

    return scores


def compute_portfolio_exposure(
    engine: Engine, broker_mode: str = "paper"
) -> dict[str, Any]:
    """ポートフォリオ全体の factor exposure を加重平均で計算。

    Returns:
        {
            "status": "active" | "insufficient_data",
            "weighted_factors": {"momentum": float, "value": float, ...},
            "concentration_label": "偏りなし" | "偏りあり" | "強偏",
            "max_factor": str,  # 最も偏っている factor 名
            "per_ticker": {ticker: {factors}, ...},
            "excluded_tickers": [データなし銘柄],
        }
    """
    # 保有銘柄取得
    with Session(engine) as s:
        ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )

    if not ports:
        return {
            "status": "insufficient_data",
            "weighted_factors": {f: None for f in ("momentum", "value", "quality", "size")},
            "concentration_label": "n/a",
            "max_factor": None,
            "per_ticker": {},
            "excluded_tickers": [],
            "reason": "no_holdings",
        }

    # 各銘柄の factor スコア取得（v2.10 Phase 1B: 4 軸全実装）
    per_ticker: dict[str, dict[str, float | None]] = {}
    excluded: list[str] = []

    with Session(engine) as s:
        for p in ports:
            uni = s.get(Universe, p.ticker)
            if uni is None:
                excluded.append(p.ticker)
                continue
            momentum_3m = _fetch_3m_return(p.ticker)
            per = _fetch_trailing_pe(p.ticker)
            roe, eq_ratio = _fetch_roe_and_eqratio(p.ticker)
            scores = compute_factor_scores(
                market_cap_jpy=uni.market_cap_jpy,
                momentum_3m=momentum_3m,
                per=per,
                roe=roe,
                equity_asset_ratio=eq_ratio,
            )
            per_ticker[p.ticker] = scores

    if not per_ticker:
        return {
            "status": "insufficient_data",
            "weighted_factors": {f: None for f in ("momentum", "value", "quality", "size")},
            "concentration_label": "n/a",
            "max_factor": None,
            "per_ticker": {},
            "excluded_tickers": excluded,
            "reason": "no_universe_data",
        }

    # 加重平均（保有金額ベース）。ここではシンプルに等加重で計算（本来は cost ベース）
    # 各 factor で None でない銘柄だけを加重平均
    weighted: dict[str, float | None] = {}
    for factor in ("momentum", "value", "quality", "size"):
        vals = [
            scores[factor]
            for scores in per_ticker.values()
            if scores.get(factor) is not None
        ]
        if vals:
            weighted[factor] = round(sum(vals) / len(vals), 1)
        else:
            weighted[factor] = None

    # 集中度ラベル
    non_none = [v for v in weighted.values() if v is not None]
    if not non_none:
        concentration_label = "n/a"
        max_factor = None
    else:
        max_v = max(non_none)
        # 「最も偏ってる factor」= 最も中央(50)から離れた factor
        deviations = [
            (f, abs(v - 50.0)) for f, v in weighted.items() if v is not None
        ]
        deviations.sort(key=lambda x: x[1], reverse=True)
        max_factor = deviations[0][0]
        max_dev = deviations[0][1]
        if max_dev < 10:
            concentration_label = "偏りなし"
        elif max_dev < 25:
            concentration_label = "偏りあり"
        else:
            concentration_label = "強偏（要分散）"

    return {
        "status": "active",
        "weighted_factors": weighted,
        "concentration_label": concentration_label,
        "max_factor": max_factor,
        "per_ticker": per_ticker,
        "excluded_tickers": excluded,
    }
