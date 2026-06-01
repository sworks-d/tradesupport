"""決算前 stop 厳格化（v2.10 Phase 2B）。

決算発表 N 日前に保有銘柄の stop_pct を厳格化することで、決算ギャップ
（決算後ストップ高/安）のリスクを軽減する。

設計:
  - J-Quants の get_eq_earnings_cal で決算カレンダー取得
  - 保有銘柄のうち N 日以内に決算予定があれば stop_pct を「元の半分」に
  - trailing_stop との合成: **より厳しい方を採用**（モノトニック）

ハルシネーション対策:
  - 決算日が取れない銘柄は通常 stop（推測しない）
  - N 日前は可変パラメータ（デフォルト 3 日）
  - 既存 trailing_stop のモノトニック性を維持
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from trading_agent.utils.logger import get_logger
from trading_agent.utils.ticker_normalize import is_jp_ticker, universe_to_jquants

_log = get_logger("portfolio.earnings_guard")

# 決算前 N 日（デフォルト・呼び出し側で上書き可能）
DEFAULT_DAYS_BEFORE_EARNINGS = 3

# stop_pct の厳格化倍率（デフォルト 0.5 = 元の半分）
DEFAULT_TIGHTEN_RATIO = 0.5


def fetch_next_earnings_date(
    ticker: str, *, client: Any | None = None
) -> dt.date | None:
    """J-Quants から該当銘柄の次回決算予定日を取得。

    Returns:
        日付 or None（取れない・推測しない）
    """
    if not is_jp_ticker(ticker):
        return None
    if client is None:
        try:
            from trading_agent.mcp_tools.jquants import get_default_client

            client = get_default_client()
        except Exception:
            return None
    if client is None:
        return None
    df = _load_calendar(client)
    if df is None or len(df) == 0:
        return None

    try:
        jq_code = str(universe_to_jquants(ticker)).strip()
        # code 列を探して該当 ticker でフィルタ
        code_col = None
        for c in ("Code", "LocalCode"):
            if c in df.columns:
                code_col = c
                break
        if code_col is None:
            return None
        df_t = df[df[code_col].astype(str).str.strip() == jq_code]
        if df_t.empty:
            return None
        # 日付らしき列を抽出（DisclosedDate / Date 等）
        today = dt.date.today()
        date_col = None
        for col_name in ("DisclosedDate", "Date", "AnnouncementDate", "RecordDate"):
            if col_name in df_t.columns:
                date_col = col_name
                break
        if date_col is None:
            return None
        # 未来の最も近い日付
        future_dates: list[dt.date] = []
        for v in df_t[date_col]:
            try:
                if hasattr(v, "date"):
                    d = v.date()
                else:
                    d = dt.date.fromisoformat(str(v)[:10])
                if d >= today:
                    future_dates.append(d)
            except Exception:
                continue
        if not future_dates:
            return None
        return min(future_dates)
    except Exception as exc:
        _log.warning(
            "earnings_cal_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


# モジュールレベルキャッシュ（1 朝バッチで API は 1 度だけ叩く）
_calendar_cache: dict[str, Any] = {"df": None, "date": None, "tried": False}


def reset_calendar_cache() -> None:
    """テスト用にキャッシュをリセット。"""
    _calendar_cache["df"] = None
    _calendar_cache["date"] = None
    _calendar_cache["tried"] = False


def _load_calendar(client: Any) -> Any | None:
    """earnings calendar を 1 日 1 回だけ取得（TTL = 当日）。

    取得失敗時も tried=True にして同じ朝バッチ内で再試行しない
    （TypeError 等で遅くならないように）。
    """
    today = dt.date.today()
    if _calendar_cache.get("date") == today and _calendar_cache.get("tried"):
        return _calendar_cache.get("df")

    _calendar_cache["date"] = today
    _calendar_cache["tried"] = True

    sdk = getattr(client, "_cli", None)
    if sdk is None or not hasattr(sdk, "get_eq_earnings_cal"):
        _calendar_cache["df"] = None
        _log.warning("earnings_cal_unavailable", reason="no_sdk_method")
        return None

    try:
        df = sdk.get_eq_earnings_cal()  # v2.10 修正: 引数なしで全件取得
        if df is None or len(df) == 0:
            _calendar_cache["df"] = None
            _log.info("earnings_cal_empty")
            return None
        _calendar_cache["df"] = df
        _log.info("earnings_cal_loaded", rows=len(df))
        return df
    except Exception as exc:
        _log.warning(
            "earnings_cal_load_failed",
            error_type=type(exc).__name__,
        )
        _calendar_cache["df"] = None
        return None


def compute_effective_stop_with_earnings(
    *,
    base_stop_pct: float,
    trailing_stop_pct: float | None,
    earnings_date: dt.date | None,
    today: dt.date | None = None,
    days_before_earnings: int = DEFAULT_DAYS_BEFORE_EARNINGS,
    tighten_ratio: float = DEFAULT_TIGHTEN_RATIO,
) -> dict[str, Any]:
    """trailing stop と決算前厳格化を合成して、最終的な effective stop を返す。

    合成ルール: より厳しい（小さい絶対値の負値、または高い stop ライン）方を採用。

    Args:
        base_stop_pct: 元の stop_loss_pct（正値で渡す、内部で負値化）
        trailing_stop_pct: trailing_stop で計算された effective_stop_pct（負値・None なら未適用）
        earnings_date: 次回決算予定日（None なら厳格化なし）
        today: 今日（None なら utcnow）
        days_before_earnings: 何日前から厳格化するか（可変）
        tighten_ratio: 厳格化倍率（0.5 = 元の半分）

    Returns:
        {
          "effective_stop_pct": float,
          "source": "base" | "trailing" | "earnings",
          "reason": str,
        }
    """
    if today is None:
        today = dt.date.today()

    # 候補 1: base stop
    base_stop = -abs(base_stop_pct)

    # 候補 2: trailing stop
    trailing = trailing_stop_pct  # 既に負値（または利益確定で正値）

    # 候補 3: 決算前厳格化
    earnings_stop: float | None = None
    earnings_reason = ""
    if earnings_date is not None:
        days_to_earnings = (earnings_date - today).days
        if 0 <= days_to_earnings <= days_before_earnings:
            earnings_stop = -abs(base_stop_pct) * tighten_ratio
            earnings_reason = (
                f"決算 {days_to_earnings} 日前 → stop 厳格化 "
                f"(×{tighten_ratio})"
            )

    # 合成: 最も厳しい stop（= 最も大きい数値）を採用
    candidates: list[tuple[str, float]] = [("base", base_stop)]
    if trailing is not None:
        candidates.append(("trailing", trailing))
    if earnings_stop is not None:
        candidates.append(("earnings", earnings_stop))

    # 最も厳しい = 最も「上」の stop（数値が最大）
    winner = max(candidates, key=lambda x: x[1])
    source = winner[0]
    effective = winner[1]

    reason_map = {
        "base": "通常 stop",
        "trailing": "trailing stop が最も厳しい",
        "earnings": earnings_reason or "決算前厳格化",
    }

    return {
        "effective_stop_pct": effective,
        "source": source,
        "reason": reason_map[source],
        "candidates": {name: val for name, val in candidates},
    }
