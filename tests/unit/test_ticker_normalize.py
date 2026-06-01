"""ticker_normalize の単体テスト（v2.10）。"""

from __future__ import annotations

from trading_agent.utils.ticker_normalize import (
    is_jp_ticker,
    jquants_to_universe,
    universe_to_jquants,
)


class TestJQuantsToUniverse:
    def test_普通株_5桁から4桁(self) -> None:
        assert jquants_to_universe("72030") == "7203"

    def test_新規上場暫定_末尾英字(self) -> None:
        assert jquants_to_universe("285A0") == "285A"

    def test_既に4桁なら無変換(self) -> None:
        assert jquants_to_universe("7203") == "7203"

    def test_None入力で空文字(self) -> None:
        assert jquants_to_universe(None) == ""

    def test_空文字で空文字(self) -> None:
        assert jquants_to_universe("") == ""

    def test_想定外の長さはそのまま返す(self) -> None:
        # 6桁・3桁などは推測しない
        assert jquants_to_universe("123") == "123"
        assert jquants_to_universe("1234567") == "1234567"


class TestUniverseToJQuants:
    def test_普通株_4桁から5桁(self) -> None:
        assert universe_to_jquants("7203") == "72030"

    def test_新規上場暫定_末尾英字に_0付加(self) -> None:
        assert universe_to_jquants("285A") == "285A0"

    def test_既に5桁なら無変換(self) -> None:
        assert universe_to_jquants("72030") == "72030"

    def test_None入力で空文字(self) -> None:
        assert universe_to_jquants(None) == ""


class TestIsJpTicker:
    def test_4桁数字_JP(self) -> None:
        assert is_jp_ticker("7203") is True
        assert is_jp_ticker("9984") is True

    def test_末尾英字_JP(self) -> None:
        assert is_jp_ticker("285A") is True

    def test_5桁_J_Quants_JP(self) -> None:
        assert is_jp_ticker("72030") is True
        assert is_jp_ticker("285A0") is True

    def test_US_銘柄_非JP(self) -> None:
        assert is_jp_ticker("AAPL") is False
        assert is_jp_ticker("QQQ") is False
        assert is_jp_ticker("VOO") is False

    def test_None_空_非JP(self) -> None:
        assert is_jp_ticker(None) is False
        assert is_jp_ticker("") is False
