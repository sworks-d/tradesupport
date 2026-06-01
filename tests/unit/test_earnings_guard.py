"""決算前 stop 厳格化の単体テスト（v2.10 Phase 2B）。"""

from __future__ import annotations

import datetime as dt

import pytest

from unittest.mock import MagicMock

import pandas as pd

from trading_agent.portfolio.earnings_guard import (
    compute_effective_stop_with_earnings,
    fetch_next_earnings_date,
    reset_calendar_cache,
)


class TestComputeEffectiveStop:
    def test_baseのみ_trailing_earningsなし(self) -> None:
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=None,
            today=dt.date(2026, 5, 31),
        )
        assert r["effective_stop_pct"] == -0.08
        assert r["source"] == "base"

    def test_trailing_あり_baseより厳しいとtrailing採用(self) -> None:
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=-0.03,  # base -8% より厳しい (-3%)
            earnings_date=None,
            today=dt.date(2026, 5, 31),
        )
        assert r["effective_stop_pct"] == -0.03
        assert r["source"] == "trailing"

    def test_決算3日前で厳格化発動(self) -> None:
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 3),  # 3 日後
            today=dt.date(2026, 5, 31),
        )
        # base -8% × 0.5 = -4% に厳格化
        assert abs(r["effective_stop_pct"] - (-0.04)) < 1e-9
        assert r["source"] == "earnings"
        assert "決算" in r["reason"]

    def test_決算が遠ければ厳格化なし(self) -> None:
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 30),  # 30 日後
            today=dt.date(2026, 5, 31),
        )
        # earnings 候補は計算されず base が選ばれる
        assert r["source"] == "base"
        assert r["effective_stop_pct"] == -0.08

    def test_trailing_と_earnings_厳しい方採用(self) -> None:
        # trailing -3%, earnings -4% → trailing の方が厳しい
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=-0.03,
            earnings_date=dt.date(2026, 6, 3),  # 3 日後
            today=dt.date(2026, 5, 31),
        )
        # trailing -3% > earnings -4% なので trailing 採用
        assert r["source"] == "trailing"
        assert r["effective_stop_pct"] == -0.03

    def test_earnings_の方が_trailing_より厳しい場合は_earnings採用(
        self,
    ) -> None:
        # base -10%, trailing -5%, earnings -10% × 0.5 = -5% → 同点だが trailing 優先
        # base -20%, trailing -3%, earnings -10% (×0.5) → trailing -3 が一番厳しい
        # 逆パターン: base -10%, trailing -8%, earnings -10% × 0.5 = -5% → earnings
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.10,
            trailing_stop_pct=-0.08,
            earnings_date=dt.date(2026, 6, 2),
            today=dt.date(2026, 5, 31),
        )
        # base -10%, trailing -8%, earnings -5% → earnings 採用
        assert r["source"] == "earnings"
        assert abs(r["effective_stop_pct"] - (-0.05)) < 1e-9

    def test_可変_days_before_earnings(self) -> None:
        """5 日前まで厳格化するように変更。"""
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 5),  # 5 日後
            today=dt.date(2026, 5, 31),
            days_before_earnings=5,
        )
        assert r["source"] == "earnings"

    def test_可変_tighten_ratio(self) -> None:
        """厳格化倍率を 0.3 に。"""
        r = compute_effective_stop_with_earnings(
            base_stop_pct=0.10,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 1),
            today=dt.date(2026, 5, 31),
            tighten_ratio=0.3,
        )
        # base -10% × 0.3 = -3%
        assert abs(r["effective_stop_pct"] - (-0.03)) < 1e-9

    def test_決算前後_境界(self) -> None:
        """ちょうど N 日前は厳格化発動、N+1 日前は発動しない。"""
        # N=3, 決算 4 日後 → 発動しない
        r1 = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 4),
            today=dt.date(2026, 5, 31),
            days_before_earnings=3,
        )
        assert r1["source"] == "base"
        # N=3, 決算 3 日後 → 発動
        r2 = compute_effective_stop_with_earnings(
            base_stop_pct=0.08,
            trailing_stop_pct=None,
            earnings_date=dt.date(2026, 6, 3),
            today=dt.date(2026, 5, 31),
            days_before_earnings=3,
        )
        assert r2["source"] == "earnings"


class TestFetchNextEarningsDate:
    """v2.10 修正: get_eq_earnings_cal() は引数なし呼び出し + Code フィルタ。"""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        reset_calendar_cache()
        yield
        reset_calendar_cache()

    def _client_with_df(self, df: pd.DataFrame) -> MagicMock:
        sdk = MagicMock()
        sdk.get_eq_earnings_cal = MagicMock(return_value=df)
        client = MagicMock()
        client._cli = sdk
        return client

    def test_non_jp_ticker_returns_none(self):
        """米国株は J-Quants 対象外。"""
        client = self._client_with_df(pd.DataFrame())
        assert fetch_next_earnings_date("AAPL", client=client) is None
        # SDK は呼ばれない（is_jp_ticker でガード）
        client._cli.get_eq_earnings_cal.assert_not_called()

    def test_calls_sdk_with_no_arguments(self):
        """SDK の get_eq_earnings_cal() は引数なしで呼ばれる（TypeError 回避）。"""
        df = pd.DataFrame(
            {"Code": ["94320"], "DisclosedDate": ["2026-06-03"]}
        )
        client = self._client_with_df(df)
        fetch_next_earnings_date("9432", client=client)
        client._cli.get_eq_earnings_cal.assert_called_once_with()  # 引数なし

    def test_finds_future_date_by_code(self):
        df = pd.DataFrame(
            {
                "Code": ["94320", "94340", "72110"],
                "DisclosedDate": ["2026-06-03", "2026-06-05", "2026-06-01"],
            }
        )
        client = self._client_with_df(df)
        # ticker "9432" → universe_to_jquants で "94320"（末尾 0 補完）に対応
        result = fetch_next_earnings_date("9432", client=client)
        assert result == dt.date(2026, 6, 3)

    def test_empty_dataframe_returns_none(self):
        client = self._client_with_df(pd.DataFrame())
        assert fetch_next_earnings_date("9432", client=client) is None

    def test_sdk_typeerror_returns_none_no_crash(self):
        """SDK が TypeError 投げても None 返して継続する。"""
        sdk = MagicMock()
        sdk.get_eq_earnings_cal = MagicMock(side_effect=TypeError("bad args"))
        client = MagicMock()
        client._cli = sdk
        result = fetch_next_earnings_date("9432", client=client)
        assert result is None

    def test_cache_avoids_repeated_api_calls(self):
        """複数 ticker への問い合わせで SDK は 1 度しか呼ばれない。"""
        df = pd.DataFrame(
            {
                "Code": ["94320", "94340"],
                "DisclosedDate": ["2026-06-03", "2026-06-05"],
            }
        )
        client = self._client_with_df(df)
        fetch_next_earnings_date("9432", client=client)
        fetch_next_earnings_date("9434", client=client)
        fetch_next_earnings_date("7211", client=client)
        # キャッシュにより 1 度だけ
        assert client._cli.get_eq_earnings_cal.call_count == 1

    def test_ticker_not_in_calendar_returns_none(self):
        df = pd.DataFrame(
            {"Code": ["94320"], "DisclosedDate": ["2026-06-03"]}
        )
        client = self._client_with_df(df)
        # 別の ticker → カレンダーに存在しない
        assert fetch_next_earnings_date("9999", client=client) is None
