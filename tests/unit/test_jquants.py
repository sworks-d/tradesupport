"""J-Quants クライアント単体テスト（v2.10）。

実 API は叩かない（ネット非依存・低速回避・API トークン秘匿）。
SDK の ClientV2 をモック化して、ラッパー層の挙動だけ確認する。
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _make_dummy_df(rows: list[dict]) -> pd.DataFrame:
    """テスト用 DataFrame を作る。"""
    return pd.DataFrame(rows)


@pytest.fixture
def mock_settings(monkeypatch):
    """jquants_refresh_token を持つ Settings をモック。"""
    from trading_agent.config import Settings

    def _stub() -> Settings:
        return Settings(
            anthropic_api_key="dummy",
            jquants_refresh_token="dummy-api-key-xxx",
            moomoo_trading_pwd="dummy",
            moomoo_account_id="00000000",
        )

    monkeypatch.setattr(
        "trading_agent.mcp_tools.jquants.load_settings", _stub
    )


class TestJQuantsClient:
    def test_listed_info_成功時はlist_dictを返す(self, mock_settings):
        from trading_agent.mcp_tools import jquants as j

        with patch.object(
            j, "JQuantsClient"
        ):  # SDK Import を妨げないためのスタブ
            pass

        # 直接 ClientV2 をモック化
        fake_df = _make_dummy_df(
            [{"Code": "72030", "CoName": "トヨタ自動車", "ScaleCat": "TOPIX Core30"}]
        )

        with patch("jquantsapi.ClientV2") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_eq_master.return_value = fake_df
            mock_cls.return_value = mock_inst

            client = j.JQuantsClient()
            result = client.listed_info(ticker="7203")
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["Code"] == "72030"
            assert result[0]["CoName"] == "トヨタ自動車"

    def test_listed_info_空DataFrameは空list(self, mock_settings):
        from trading_agent.mcp_tools import jquants as j

        with patch("jquantsapi.ClientV2") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_eq_master.return_value = pd.DataFrame()
            mock_cls.return_value = mock_inst

            client = j.JQuantsClient()
            assert client.listed_info(ticker="9999") == []

    def test_listed_info_例外時は空list_ハルシネーション防止(self, mock_settings):
        from trading_agent.mcp_tools import jquants as j

        with patch("jquantsapi.ClientV2") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_eq_master.side_effect = RuntimeError("network error")
            mock_cls.return_value = mock_inst

            client = j.JQuantsClient()
            # 例外を握り潰して空 list を返す（埋めない・推測しない）
            assert client.listed_info(ticker="7203") == []

    def test_daily_quotes_引数変換(self, mock_settings):
        """from_date/to_date が YYYYMMDD 文字列に変換されることを確認。"""
        from trading_agent.mcp_tools import jquants as j

        with patch("jquantsapi.ClientV2") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_eq_bars_daily.return_value = pd.DataFrame()
            mock_cls.return_value = mock_inst

            client = j.JQuantsClient()
            client.daily_quotes(
                ticker="7203",
                from_date=dt.date(2026, 5, 20),
                to_date=dt.date(2026, 5, 30),
            )
            # SDK に渡された引数
            mock_inst.get_eq_bars_daily.assert_called_once_with(
                code="7203", from_yyyymmdd="20260520", to_yyyymmdd="20260530"
            )

    def test_statements_例外時は空list(self, mock_settings):
        from trading_agent.mcp_tools import jquants as j

        with patch("jquantsapi.ClientV2") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.get_fin_summary.side_effect = RuntimeError("subscription")
            mock_cls.return_value = mock_inst

            client = j.JQuantsClient()
            assert client.statements(ticker="7203") == []


class TestGetDefaultClient:
    def test_設定なしならNone(self, monkeypatch):
        """jquants_refresh_token が無いなら None を返す（フォールバック判定用）。"""
        from trading_agent.config import Settings
        from trading_agent.mcp_tools import jquants as j

        def _stub() -> Settings:
            return Settings(
                anthropic_api_key="dummy",
                jquants_refresh_token=None,
                moomoo_trading_pwd="dummy",
                moomoo_account_id="00000000",
            )

        monkeypatch.setattr(j, "load_settings", _stub)
        # singleton をリセット
        monkeypatch.setattr(j, "_singleton", None)

        # token 無し → ValueError を JQuantsClient で raise → get_default_client は None
        assert j.get_default_client() is None
