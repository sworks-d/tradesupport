"""構造化ロギング基盤（structlog）。SYSTEM_DESIGN.md §7 / OPERATIONS.md §5。

- 全ログを JSON で出力（``jq`` でパース可能）
- 出力先は stdout と日次ファイル ``~/.trading-agent/logs/YYYY-MM-DD.log``
- API キー・パスワード等の機微値はマスキング（OPERATIONS.md §10.2）
- タイムスタンプは JST（朝バッチが JST 基準のため、ログは JST 表示）

使い方::

    from trading_agent.utils.logger import configure_logging, log

    configure_logging()          # 起動時に1回（main.py / scripts から）
    log.info("agent_started", agent="screening_agent", universe_size=500)

日次ローテーションは「起動時に当日付ファイルを開く」方式（OPERATIONS.md §5.2 の
ファイル名規約に一致）。launchd で日次再起動される運用前提。古いログの圧縮は
ops レベル（OPERATIONS.md §5.2）で行う。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

# JST（朝バッチ JST 5:00 基準。永続化する時刻値は別途 UTC、ログ表示は JST）
JST = timezone(timedelta(hours=9))

# マスキング対象のキー名（小文字部分一致）
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "webhook",
)
# マスキング対象の値プレフィックス（キー名に関わらず）
_SENSITIVE_VALUE_MARKERS = ("sk-ant-",)
_REDACTED = "***REDACTED***"

# モジュールレベルの共有ロガー（configure_logging 後に使う）
log: structlog.stdlib.BoundLogger = structlog.get_logger("trading_agent")

_configured = False


def _jst_timestamper(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """ISO8601(JST) のタイムスタンプを付与する。"""
    event_dict["timestamp"] = datetime.now(JST).isoformat()
    return event_dict


def _redact_sensitive(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """機微値をマスクする。

    - キー名が機微語を含む → 値をマスク
    - 値が ``sk-ant-`` 等のマーカーを含む文字列 → マスク

    ネストした dict の中までは追わない（Phase 1 の割り切り）。機微値は
    トップレベルのキーで渡す前提。
    """
    for key in list(event_dict.keys()):
        lower = key.lower()
        if any(part in lower for part in _SENSITIVE_KEY_PARTS) and event_dict[key] is not None:
            event_dict[key] = _REDACTED
            continue
        value = event_dict[key]
        if isinstance(value, str) and any(m in value for m in _SENSITIVE_VALUE_MARKERS):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(
    level: str | None = None,
    log_dir: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """ロギングを初期化する（冪等。``force=True`` で再構築）。

    Args:
        level: ログレベル。``None`` なら設定（``Settings.log_level``）から取得。
        log_dir: ログ出力ディレクトリ。``None`` なら設定から取得。
        force: 既に構築済みでも再構築する。

    Returns:
        当日のログファイルのパス。
    """
    global _configured
    if _configured and not force:
        # 既に構築済み。当日のファイルパスだけ返す。
        resolved_dir = log_dir or _current_log_dir()
        return resolved_dir / f"{datetime.now(JST).date()}.log"

    if level is None or log_dir is None:
        # 遅延 import（config が settings/.env を要求するため）
        from trading_agent.config import get_settings

        settings = get_settings()
        level = level or settings.log_level
        log_dir = log_dir or settings.log_dir

    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{datetime.now(JST).date()}.log"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _redact_sensitive,
        _jst_timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=shared_processors,
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(level.upper())

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    _configured = True
    return logfile


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """名前付きロガーを取得する。"""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def _current_log_dir() -> Path:
    from trading_agent.config import get_settings

    return get_settings().log_dir
