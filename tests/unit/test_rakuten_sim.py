"""楽天かぶミニ simulate_fill のテスト（v2.10）。"""

from __future__ import annotations

import pytest

from trading_agent.portfolio.fill_simulator import simulate_fill_for_provider
from trading_agent.portfolio.rakuten_sim import (
    RakutenFeeSchedule,
    simulate_fill,
)


class TestRakutenSimKifutsuke:
    """寄付取引（朝バッチ標準・手数料 0 + スプレッド 0）。"""

    def test_jp_kifutsuke_buy_only_slippage(self):
        """JP 寄付取引の buy: 手数料 0、スプレッドなし、スリッページのみ。"""
        result = simulate_fill(
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="buy",
            realtime_trading=False,
        )
        assert result.fee_jpy == 0.0
        assert result.fx_cost_jpy == 0.0
        # スリッページ込みで fill_price = 1000 × (1 + 0.002 × 1.5) = 1003
        # (volume_30d_avg=None → 1.5x)
        assert 1000.0 < result.fill_price <= 1010.0
        # notional = fill_price × qty
        assert result.total_cost_jpy == result.notional_jpy
        # 手数料・スプレッドなしなのでコスト = notional のみ
        assert result.total_cost_jpy == pytest.approx(result.fill_price * 10)

    def test_jp_kifutsuke_sell_no_fee(self):
        """JP 寄付取引の sell: 手数料 0、スプレッドなし。"""
        result = simulate_fill(
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="sell",
            realtime_trading=False,
        )
        assert result.fee_jpy == 0.0
        # sell は下方向スリッページ
        assert result.fill_price < 1000.0
        assert result.total_cost_jpy == result.notional_jpy

    def test_jp_qty_100_unit_also_free(self):
        """単元株（100 株）でも手数料 0（楽天かぶミニは単元未満・単元両方無料）。"""
        result = simulate_fill(
            market_price=500.0,
            qty=100,
            is_jp=True,
            side="buy",
            realtime_trading=False,
        )
        assert result.fee_jpy == 0.0


class TestRakutenSimRealtime:
    """リアルタイム取引（場中 trailing 売却用・スプレッド 0.22%）。"""

    def test_jp_realtime_buy_with_spread(self):
        """JP リアルタイム buy: スプレッド 0.22% が乗る。"""
        result = simulate_fill(
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="buy",
            realtime_trading=True,
            volume_30d_avg=2_000_000,  # 高流動性 → スリッページ base のみ
        )
        # spread 0.22% + slippage 0.2% = 0.42% 上振れ
        assert result.fill_price == pytest.approx(1000.0 * 1.0042, abs=1.0)
        assert result.fee_jpy == 0.0  # 手数料は無料

    def test_jp_realtime_sell_with_spread(self):
        """JP リアルタイム sell: スプレッド 0.22% 下方向。"""
        result = simulate_fill(
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="sell",
            realtime_trading=True,
            volume_30d_avg=2_000_000,
        )
        # 下方向 0.42%
        assert result.fill_price == pytest.approx(1000.0 * (1 - 0.0042), abs=1.0)


class TestRakutenSimUS:
    """米国株（楽天通常口座・FX 込み）。"""

    def test_us_requires_usdjpy(self):
        with pytest.raises(ValueError):
            simulate_fill(
                market_price=100.0,
                qty=1,
                is_jp=False,
                usdjpy=None,
                side="buy",
            )

    def test_us_buy_with_fx(self):
        result = simulate_fill(
            market_price=100.0,
            qty=1,
            is_jp=False,
            usdjpy=150.0,
            side="buy",
        )
        # FX 25 銭 × 1 株 = 0.25 円
        assert result.fx_cost_jpy == pytest.approx(0.25)
        # 米国株手数料 0.495% × notional
        assert result.fee_jpy > 0


class TestProviderDispatcher:
    """broker_provider 別の dispatcher が正しく振り分けるか。"""

    def test_moomoo_provider_uses_moomoo_sim(self):
        """moomoo provider: 単元未満は 0%、単元 100 株は 0.088%。"""
        # 単元 100 株 → moomoo 手数料 0.088% 発生
        result = simulate_fill_for_provider(
            broker_provider="moomoo",
            market_price=1000.0,
            qty=100,
            is_jp=True,
            side="buy",
        )
        # moomoo 100 株 = 1000 × 100 × 0.00088 = 88 円
        assert result.fee_jpy > 50.0  # 何らかの手数料が発生

    def test_rakuten_provider_uses_rakuten_sim(self):
        """rakuten provider: 寄付取引デフォルトで手数料 0。"""
        result = simulate_fill_for_provider(
            broker_provider="rakuten",
            market_price=1000.0,
            qty=100,
            is_jp=True,
            side="buy",
        )
        assert result.fee_jpy == 0.0  # 楽天は完全無料

    def test_sbi_uses_rakuten_sim(self):
        """sbi provider: 楽天と同等のコスト構造で計算。"""
        result = simulate_fill_for_provider(
            broker_provider="sbi",
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="buy",
        )
        assert result.fee_jpy == 0.0  # SBI S 株も完全無料

    def test_fractional_uses_rakuten_sim(self):
        result = simulate_fill_for_provider(
            broker_provider="fractional",
            market_price=1000.0,
            qty=10,
            is_jp=True,
            side="buy",
        )
        assert result.fee_jpy == 0.0

    def test_unknown_provider_falls_back_to_moomoo(self):
        """不明な provider は moomoo_sim にフォールバック（互換維持）。"""
        result = simulate_fill_for_provider(
            broker_provider="unknown_broker",
            market_price=1000.0,
            qty=100,
            is_jp=True,
            side="buy",
        )
        # moomoo 100 株 = 0.088% 発生
        assert result.fee_jpy > 50.0


class TestRakutenLowCostComparedToMoomoo:
    """楽天が moomoo より低コストであることの確認（運用判断の妥当性）。"""

    def test_jp_unit_rakuten_cheaper_than_moomoo(self):
        """JP 単元株 100 株: 楽天 0 円 vs moomoo 88 円。"""
        rakuten = simulate_fill_for_provider(
            broker_provider="rakuten",
            market_price=1000.0,
            qty=100,
            is_jp=True,
            side="buy",
        )
        moomoo = simulate_fill_for_provider(
            broker_provider="moomoo",
            market_price=1000.0,
            qty=100,
            is_jp=True,
            side="buy",
        )
        assert rakuten.fee_jpy == 0.0
        assert moomoo.fee_jpy > 0
        assert rakuten.total_cost_jpy < moomoo.total_cost_jpy
