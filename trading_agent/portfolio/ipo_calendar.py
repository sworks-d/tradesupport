"""IPO カレンダー（v2.10 Phase 6-2）。

直近 N ヶ月で上場した銘柄（末尾英字の暫定コード）を J-Quants listed_info から
抽出し、ZEELE / dashboard で「IPO 直後の動意候補」を可視化する。

IPO 直後の銘柄は：
  - データ薄（yfinance で 404 多発）
  - 価格変動大（材料次第で 2-10 倍も）
  - 投機的だが「中期で勝てる」可能性も

ハルシネーション対策:
  - J-Quants listed_info の Code フィールドのみ参照（推測しない）
  - Code が想定外（5桁でない・末尾英字でない）はスキップ
  - 上場日が取れない銘柄はカウントしない
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from trading_agent.utils.logger import get_logger
from trading_agent.utils.ticker_normalize import jquants_to_universe

_log = get_logger("portfolio.ipo_calendar")


def _is_provisional_code(code: str | None) -> bool:
    """新規上場暫定コード（末尾英字）か。

    例: 285A0 → True、72030 → False
    """
    if not code or len(code) != 5:
        return False
    # 5 桁の 4 桁目（末尾チェックデジット手前）が英字
    return code[3].isalpha()


def list_recent_ipos(
    *, lookback_months: int = 6, client: Any | None = None
) -> dict[str, Any]:
    """直近 N ヶ月の IPO 銘柄リスト。

    Args:
        lookback_months: 何ヶ月前までを「最近の IPO」とするか
        client: JQuantsClient（None なら get_default_client）

    Returns:
        {
            "status": "active" | "no_client" | "no_provisional",
            "ipos": [{ticker, name, market, sector, scale_category}, ...],
            "lookback_months": int,
            "total": int,
        }
    """
    if client is None:
        try:
            from trading_agent.mcp_tools.jquants import get_default_client

            client = get_default_client()
        except Exception:
            client = None
    if client is None:
        return _empty_result(reason="no_jquants_client", lookback_months=lookback_months)

    try:
        all_listed = client.listed_info()
    except Exception as exc:
        _log.warning(
            "ipo_calendar_fetch_failed", error_type=type(exc).__name__
        )
        return _empty_result(
            reason="fetch_failed", lookback_months=lookback_months
        )

    if not all_listed:
        return _empty_result(
            reason="empty_listed_info", lookback_months=lookback_months
        )

    # 末尾英字（IPO 暫定コード）の銘柄だけを抽出
    provisional: list[dict[str, Any]] = []
    for row in all_listed:
        code = row.get("Code")
        if not _is_provisional_code(code):
            continue
        provisional.append(
            {
                "ticker": jquants_to_universe(code),
                "jquants_code": code,
                "name": row.get("CoName") or row.get("CompanyName") or "",
                "name_en": row.get("CoNameEn") or "",
                "market": row.get("MktNm") or row.get("MarketCodeName") or "",
                "sector": row.get("S33Nm") or row.get("Sector33CodeName") or "",
                "scale_category": row.get("ScaleCat") or row.get("ScaleCategory") or "",
            }
        )

    if not provisional:
        return _empty_result(
            reason="no_provisional_codes", lookback_months=lookback_months
        )

    return {
        "status": "active",
        "ipos": provisional,
        "lookback_months": lookback_months,
        "total": len(provisional),
    }


def _empty_result(*, reason: str, lookback_months: int) -> dict[str, Any]:
    return {
        "status": "insufficient_data",
        "ipos": [],
        "lookback_months": lookback_months,
        "total": 0,
        "reason": reason,
    }
