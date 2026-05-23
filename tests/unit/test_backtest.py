"""価格ベース・バックテスト（先読みなし）の単体テスト。合成価格・シグナル注入。"""

from __future__ import annotations

from trading_agent.evaluation.backtest import backtest_signal, sma_cross_signal


def test_hits_target_on_rising() -> None:
    # 60バー横ばい後に上昇 → 即エントリー(signal常時True)→target到達でhit
    prices = [100.0] * 61 + [100 + 2 * i for i in range(1, 30)]
    trades, tr = backtest_signal(
        prices, signal_fn=lambda _p: True, hold_bars=20,
        stop_pct=0.10, target_return=0.15, warmup=60,
    )
    assert trades
    assert trades[0].outcome == "hit"
    assert tr.n >= 1


def test_stop_hit_on_drop() -> None:
    prices = [100.0] * 61 + [100 - 3 * i for i in range(1, 20)]  # 下落
    trades, _ = backtest_signal(
        prices, signal_fn=lambda _p: True, hold_bars=20,
        stop_pct=0.10, target_return=0.15, warmup=60,
    )
    assert trades[0].outcome == "miss"
    assert trades[0].exit_price <= 100 * 0.90


def test_no_overlapping_positions() -> None:
    prices = [100.0 + i for i in range(120)]  # 単調増加
    trades, _ = backtest_signal(
        prices, signal_fn=lambda _p: True, hold_bars=10,
        stop_pct=0.10, target_return=0.05, warmup=60,
    )
    # エントリーは前トレードの手仕舞い後のみ（重複なし）
    for a, b in zip(trades, trades[1:], strict=False):
        assert b.entry_idx > a.exit_idx


def test_warmup_blocks_early_entry() -> None:
    prices = [100.0 + i for i in range(120)]
    trades, _ = backtest_signal(
        prices, signal_fn=lambda _p: True, hold_bars=5,
        stop_pct=0.10, target_return=0.05, warmup=80,
    )
    assert all(t.entry_idx >= 80 for t in trades)


def test_no_signal_no_trades() -> None:
    prices = [100.0 + i for i in range(120)]
    trades, tr = backtest_signal(
        prices, signal_fn=lambda _p: False, hold_bars=10, stop_pct=0.10, target_return=0.05
    )
    assert trades == [] and tr.n == 0


class TestSmaCrossSignal:
    def test_fires_on_golden_cross(self) -> None:
        sig = sma_cross_signal(short=5, long=20)
        # 長期下落→直近で短期が上抜けする系列
        prices = [100 - 0.5 * i for i in range(30)] + [85 + 3 * i for i in range(10)]
        # 上抜けの瞬間が含まれる（どこかの時点で True）
        assert any(sig(prices[: i + 1]) for i in range(len(prices)))

    def test_insufficient_history_false(self) -> None:
        assert sma_cross_signal(short=5, long=20)([100.0] * 10) is False
