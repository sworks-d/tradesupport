"""Discord 通知（v2.10 Phase I-11）。

env: WILLE_DISCORD_WEBHOOK_URL に webhook URL を設定。
未設定なら no-op（warning ログのみ・本処理は止めない）。

設計原則:
  - fire-and-forget: 通知失敗で本処理を絶対に止めない
  - urllib のみ使用（依存を増やさない）
  - 5 秒タイムアウト（朝バッチを止めない）
  - 例外は warn ログのみ
"""

from __future__ import annotations

import json
import os
from typing import Any

from trading_agent.utils.logger import get_logger

_log = get_logger("utils.discord")

_LEVEL_COLORS: dict[str, int] = {
    "info": 0x3498DB,      # 青
    "warning": 0xF39C12,   # オレンジ
    "error": 0xE74C3C,     # 赤
    "critical": 0x8E0000,  # 暗赤
}


def _webhook_url() -> str | None:
    """環境変数から webhook URL を取得。未設定は None。"""
    url = os.environ.get("WILLE_DISCORD_WEBHOOK_URL")
    return url if url and url.startswith("https://") else None


def is_enabled() -> bool:
    """Discord 通知が有効か（webhook URL 設定済か）。"""
    return _webhook_url() is not None


def notify_discord(
    message: str,
    *,
    level: str = "info",
    title: str | None = None,
    fields: dict[str, Any] | None = None,
    timeout_s: float = 5.0,
) -> bool:
    """Discord に通知を送る（同期送信・失敗で本処理は止めない）。

    Args:
        message: 本文（2000 文字超は切り詰め）
        level: "info" | "warning" | "error" | "critical"
        title: タイトル（None なら自動生成）
        fields: 追加フィールド辞書（最大 25 件）
        timeout_s: HTTP タイムアウト

    Returns:
        True なら送信成功、False なら未設定 or 失敗。
    """
    url = _webhook_url()
    if url is None:
        _log.info("discord_notify_skipped_no_webhook", level=level)
        return False

    color = _LEVEL_COLORS.get(level, 0x808080)
    embed: dict[str, Any] = {
        "title": title or f"[{level.upper()}] TradeSupport",
        "description": message[:2000],
        "color": color,
    }
    if fields:
        embed["fields"] = [
            {"name": str(k)[:256], "value": str(v)[:1024], "inline": True}
            for k, v in list(fields.items())[:25]
        ]

    payload = {"embeds": [embed]}

    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            success = 200 <= resp.status < 300
            if not success:
                _log.warning("discord_notify_bad_status", status=resp.status)
            return success
    except Exception as exc:
        # 通知失敗は warn のみ。本処理は絶対に止めない。
        _log.warning(
            "discord_notify_failed",
            error_type=type(exc).__name__,
            level=level,
        )
        return False
