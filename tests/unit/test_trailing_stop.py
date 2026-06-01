"""トレーリングストップの単体テスト（v2.10 Phase 3）。"""

from __future__ import annotations

import pytest

from trading_agent.portfolio.trailing_stop import (
    compute_trailing_stop,
    should_trigger_stop,
)


class TestComputeTrailingStop:
    def test_含み益0pct_は元stop(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=1000.0, base_stop_pct=0.08
        )
        assert r.effective_stop_pct == -0.08
        assert r.shift_applied == 0.0
        assert r.is_break_even is False
        assert "未起動" in r.tier_label

    def test_含み益5pct_でstop_5引き上げ(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=1050.0, base_stop_pct=0.08
        )
        # base -8% + 5% = -3%
        assert abs(r.effective_stop_pct - (-0.03)) < 1e-9
        assert r.shift_applied == 0.05
        assert r.is_break_even is False
        assert "+5%" in r.tier_label

    def test_含み益10pct_で損益分岐到達(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=1100.0, base_stop_pct=0.08
        )
        # base -8% + 10% = +2% = 利益確保
        assert abs(r.effective_stop_pct - 0.02) < 1e-9
        assert r.is_break_even is True
        assert r.is_profit_locked is True

    def test_含み益30pct_で大幅利確線(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=1300.0, base_stop_pct=0.08
        )
        # base -8% + 20% = +12%
        assert abs(r.effective_stop_pct - 0.12) < 1e-9
        assert r.is_profit_locked is True

    def test_含み損_でも元stop_据え置き(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=950.0, base_stop_pct=0.08
        )
        assert r.effective_stop_pct == -0.08
        assert r.shift_applied == 0.0

    def test_current_None_は元stop_推測しない(self) -> None:
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=None, base_stop_pct=0.08
        )
        assert r.effective_stop_pct == -0.08
        assert "n/a" in r.tier_label

    def test_entry_ゼロ_でも例外なし(self) -> None:
        r = compute_trailing_stop(
            entry_price=0.0, current_price=100.0, base_stop_pct=0.08
        )
        assert r.effective_stop_pct == -0.08

    def test_モノトニック_最大シフト採用(self) -> None:
        """+20% 含み益 → tier 3 (+15%) が最大なのでそれが採用される。"""
        r = compute_trailing_stop(
            entry_price=1000.0, current_price=1200.0, base_stop_pct=0.08
        )
        assert r.shift_applied == 0.15


class TestTrueTrailing:
    """v2.10 Phase 1A-Step2: peak_pnl_pct を渡した時の真の trailing."""

    def test_peak10pct_現価3pct_でも_trailing_維持(self) -> None:
        """過去 +10% に達した後、現価が +3% に下落 → trail_price は +10% 基準のまま。"""
        r = compute_trailing_stop(
            entry_price=1000.0,
            current_price=1030.0,  # 現在 +3%
            base_stop_pct=0.08,
            peak_pnl_pct=0.10,     # 過去ピーク +10%
        )
        # peak +10% で shift=+10%、effective_stop = -8% + 10% = +2%
        assert abs(r.effective_stop_pct - 0.02) < 1e-9
        assert r.is_break_even is True
        assert "peak" in r.tier_label

    def test_peakなし_現在の含み益で計算(self) -> None:
        """peak_pnl_pct=None なら旧挙動（動的 stop）。"""
        r = compute_trailing_stop(
            entry_price=1000.0,
            current_price=1030.0,
            base_stop_pct=0.08,
            peak_pnl_pct=None,
        )
        # 現在 +3% は tier +5% 未満 → shift 0 → 元 stop -8%
        assert r.effective_stop_pct == -0.08

    def test_peak_より_現価_が高い場合_現価採用(self) -> None:
        """current_pnl > peak なら current を使う（peak は更新されるべき・呼び出し側責任）。"""
        r = compute_trailing_stop(
            entry_price=1000.0,
            current_price=1200.0,  # 現在 +20%
            base_stop_pct=0.08,
            peak_pnl_pct=0.10,     # peak +10% (古い)
        )
        # max(0.10, 0.20) = 0.20 → tier +20% で shift=+15%
        assert r.shift_applied == 0.15


class TestShouldTriggerStop:
    def test_現在価格がtrail_price以下なら売却推奨(self) -> None:
        assert (
            should_trigger_stop(current_price=900.0, trail_price=920.0) is True
        )

    def test_現在価格がtrail_price超なら維持(self) -> None:
        assert (
            should_trigger_stop(current_price=930.0, trail_price=920.0) is False
        )

    def test_current_None_は判定不能でFalse(self) -> None:
        assert (
            should_trigger_stop(current_price=None, trail_price=920.0) is False
        )

    def test_trail_price_ゼロ以下は推測しない(self) -> None:
        assert (
            should_trigger_stop(current_price=100.0, trail_price=0.0) is False
        )
