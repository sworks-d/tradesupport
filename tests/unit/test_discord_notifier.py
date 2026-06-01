"""Discord 通知のテスト（v2.10 Phase I-11）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trading_agent.utils import discord_notifier


class TestIsEnabled:
    def test_no_env_returns_false(self, monkeypatch):
        monkeypatch.delenv("WILLE_DISCORD_WEBHOOK_URL", raising=False)
        assert discord_notifier.is_enabled() is False

    def test_valid_https_url_returns_true(self, monkeypatch):
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/xxx/yyy"
        )
        assert discord_notifier.is_enabled() is True

    def test_non_https_returns_false(self, monkeypatch):
        """http:// 始まり (typo or 悪意) は弾く。"""
        monkeypatch.setenv("WILLE_DISCORD_WEBHOOK_URL", "http://evil.example/x")
        assert discord_notifier.is_enabled() is False


class TestNotifyDiscord:
    def test_no_webhook_returns_false(self, monkeypatch):
        monkeypatch.delenv("WILLE_DISCORD_WEBHOOK_URL", raising=False)
        assert discord_notifier.notify_discord("hi") is False

    def test_sends_payload_with_correct_url(self, monkeypatch):
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as m:
            result = discord_notifier.notify_discord(
                "test message", level="warning", title="Test"
            )

        assert result is True
        # 1 度だけ呼ばれて、URL が正しい
        req = m.call_args[0][0]
        assert req.full_url == "https://discord.com/api/webhooks/test"
        assert req.method == "POST"

    def test_long_message_truncates(self, monkeypatch):
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"
        )
        long_msg = "x" * 3000

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as m:
            discord_notifier.notify_discord(long_msg)

        import json

        req = m.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert len(body["embeds"][0]["description"]) == 2000

    def test_network_failure_returns_false_no_raise(self, monkeypatch):
        """ネットワーク失敗で例外を投げず False を返すこと（fire-and-forget）。"""
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"
        )
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = discord_notifier.notify_discord("test")
        assert result is False  # 例外を投げない・本処理を止めない

    def test_critical_level_color(self, monkeypatch):
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"
        )
        mock_response = MagicMock()
        mock_response.status = 204
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as m:
            discord_notifier.notify_discord("critical", level="critical")

        import json

        body = json.loads(m.call_args[0][0].data.decode("utf-8"))
        assert body["embeds"][0]["color"] == 0x8E0000


class TestIntegrationWithAnomalyDetector:
    """trigger_halt / check_dd_brake が Discord を fire-and-forget で呼ぶこと。"""

    def test_trigger_halt_calls_discord(self, tmp_path, monkeypatch):
        from trading_agent.portfolio import anomaly_detector

        fake_halt = tmp_path / "HALT"
        monkeypatch.setattr(anomaly_detector, "DEFAULT_HALT_FILE", fake_halt)
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x"
        )

        with patch(
            "trading_agent.utils.discord_notifier.notify_discord", return_value=True
        ) as m:
            anomaly_detector.trigger_halt("test reason", source="unit")

        m.assert_called_once()
        args = m.call_args
        assert "HALT 発火" in args[0][0]
        assert args[1]["level"] == "critical"

    def test_trigger_halt_discord_failure_no_raise(self, tmp_path, monkeypatch):
        """Discord 通知失敗で trigger_halt 自体は成功すること。"""
        from trading_agent.portfolio import anomaly_detector

        fake_halt = tmp_path / "HALT"
        monkeypatch.setattr(anomaly_detector, "DEFAULT_HALT_FILE", fake_halt)
        monkeypatch.setenv(
            "WILLE_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x"
        )

        with patch(
            "trading_agent.utils.discord_notifier.notify_discord",
            side_effect=RuntimeError("boom"),
        ):
            result = anomaly_detector.trigger_halt("r", source="u")

        # HALT ファイルは作成済（Discord 失敗で本処理は止まらない）
        assert fake_halt.exists()
        assert result.get("halted") is True
