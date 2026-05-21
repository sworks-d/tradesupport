"""logger.py の単体テスト（Task 1.0.3）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
import structlog

from trading_agent.utils import logger as logger_module
from trading_agent.utils.logger import JST, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """各テストでロギング状態をリセットする。"""
    logger_module._configured = False
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    logger_module._configured = False
    structlog.reset_defaults()


def _read_log_lines(logfile: Path) -> list[dict]:
    content = logfile.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in content.splitlines() if line]


class TestConfigure:
    def test_creates_dated_logfile(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="INFO", log_dir=tmp_path)
        expected_name = f"{datetime.now(JST).date()}.log"
        assert logfile.name == expected_name
        assert logfile.exists()

    def test_idempotent_without_force(self, tmp_path: Path) -> None:
        first = configure_logging(level="INFO", log_dir=tmp_path)
        # force しなければ再構築されない（同じ当日パスを返す）
        second = configure_logging(level="DEBUG", log_dir=tmp_path)
        assert first == second


class TestJsonOutput:
    def test_log_is_json_parseable(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="INFO", log_dir=tmp_path)
        log = get_logger("test")
        log.info("agent_started", agent="screening_agent", universe_size=500)

        lines = _read_log_lines(logfile)
        assert len(lines) == 1
        record = lines[0]
        assert record["event"] == "agent_started"
        assert record["agent"] == "screening_agent"
        assert record["universe_size"] == 500
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_timestamp_is_jst(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="INFO", log_dir=tmp_path)
        get_logger("test").info("tick")
        record = _read_log_lines(logfile)[0]
        # JST は +09:00 オフセット
        assert record["timestamp"].endswith("+09:00")

    def test_level_filtering(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="WARNING", log_dir=tmp_path)
        log = get_logger("test")
        log.info("should_not_appear")
        log.warning("should_appear")
        events = [r["event"] for r in _read_log_lines(logfile)]
        assert "should_not_appear" not in events
        assert "should_appear" in events


class TestRedaction:
    def test_anthropic_key_value_redacted(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="INFO", log_dir=tmp_path)
        get_logger("test").info("call", some_value="sk-ant-supersecret123")
        content = logfile.read_text(encoding="utf-8")
        assert "sk-ant-supersecret123" not in content
        assert "***REDACTED***" in content

    def test_sensitive_keys_redacted(self, tmp_path: Path) -> None:
        logfile = configure_logging(level="INFO", log_dir=tmp_path)
        get_logger("test").info(
            "config_loaded",
            anthropic_api_key="topsecret",
            moomoo_trading_pwd="hunter2",
            slack_webhook_url="https://hooks.slack.com/abc",
            normal_field="visible",
        )
        record = _read_log_lines(logfile)[0]
        assert record["anthropic_api_key"] == "***REDACTED***"
        assert record["moomoo_trading_pwd"] == "***REDACTED***"
        assert record["slack_webhook_url"] == "***REDACTED***"
        assert record["normal_field"] == "visible"
