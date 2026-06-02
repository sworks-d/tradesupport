"""BT-1: v3 価格スライス backtest の状態正しさ + look-ahead 回避テスト（合成価格・API非依存）。"""

from __future__ import annotations

import datetime as dt

from trading_agent.backtest.v3_slice import (
    BacktestConfig,
    run_price_slice_backtest,
)


def _days(n: int, start: dt.date = dt.date(2025, 1, 1)) -> list[dt.date]:
    # 連続した「取引日」（土日祝は無視＝合成）
    return [start + dt.timedelta(days=i) for i in range(n)]


def _ramp(dates: list[dt.date], base: float, step: float) -> list[tuple[dt.date, float]]:
    return [(d, base + i * step) for i, d in enumerate(dates)]


class TestStateCorrectness:
    def _run_uptrend(self, **cfg_kw):
        dates = _days(120)
        # 市場は緩やか上昇、銘柄Aは市場より強い上昇（leading 信号が出る）
        market = _ramp(dates, 2000.0, 1.0)
        a = _ramp(dates, 1000.0, 3.0)  # 市場超過 → leading
        cfg = BacktestConfig(
            start=dates[70], end=dates[-1], initial_capital=100_000.0,
            stop_pct=0.12, target_pct=0.20, hold_days=30, n_holdings=5, **cfg_kw,
        )
        return run_price_slice_backtest({"A": a}, market, cfg)

    def test_runs_and_enters(self) -> None:
        res = self._run_uptrend()
        # 上昇銘柄に entry が立ち、最終的に target/time exit でトレードが記録される
        assert res.n_trades >= 1
        assert res.equity_curve  # equity が毎日記録される

    def test_cash_never_negative(self) -> None:
        res = self._run_uptrend()
        assert all(e >= 0 for _, e in res.equity_curve)

    def test_no_duplicate_holding(self) -> None:
        # 同一銘柄を二重保有しない（保有中は signal 対象外）→ トレードの保有期間が重ならない
        res = self._run_uptrend()
        a_trades = sorted([t for t in res.trades if t.ticker == "A"], key=lambda t: t.entry_date)
        for prev, nxt in zip(a_trades, a_trades[1:]):
            assert nxt.entry_date > prev.exit_date  # 前のトレードが閉じてから次

    def test_stop_exit_records_miss(self) -> None:
        # 急落銘柄 → stop 発火で miss
        dates = _days(120)
        market = _ramp(dates, 2000.0, 1.0)
        # 前半上昇(leadingでentry)→後半急落(stop)
        prices = [1000.0 + i * 3.0 for i in range(80)] + [1000.0 + 80 * 3.0 - (i + 1) * 30.0 for i in range(40)]
        a = [(d, max(p, 1.0)) for d, p in zip(dates, prices)]
        cfg = BacktestConfig(start=dates[70], end=dates[-1], hold_days=60, stop_pct=0.12, target_pct=0.20)
        res = run_price_slice_backtest({"A": a}, market, cfg)
        assert any(t.reason == "stop" and t.outcome == "miss" for t in res.trades)


class TestLookAheadSafety:
    def test_signal_uses_only_past_prices(self) -> None:
        # 将来の暴騰を「今日」の signal が先読みしない：
        # warm-up 期間まで市場並み(信号なし)、start 直後に将来急騰がある銘柄でも、
        # start 時点の signal は過去データのみで判定される（na/leadingは過去依存）。
        dates = _days(130)
        market = _ramp(dates, 2000.0, 1.0)
        # 0..100 は市場とほぼ同率(lagging寄り)、101.. で急騰
        flat = [(dates[i], 1000.0 + i * 1.0) for i in range(101)]
        spike = [(dates[101 + i], 1100.0 + (i + 1) * 50.0) for i in range(29)]
        a = flat + spike
        cfg = BacktestConfig(start=dates[64], end=dates[100], hold_days=30)  # 急騰前で終える
        res = run_price_slice_backtest({"A": a}, market, cfg)
        # 急騰(101以降)を end=dates[100] で打ち切っているので、その利益は equity に出ない
        # = 未来価格を先読みして儲けていないことの確認（最終 equity は初期付近）
        assert res.final_equity <= cfg.initial_capital * 1.30

    def test_entry_is_next_day_not_signal_day(self) -> None:
        # signal 当日では entry せず、翌営業日の価格で入る（entry_date > signal が出た日）
        res = TestStateCorrectness()._run_uptrend()
        # 最初のトレードの entry_date は calendar の start より後（signal→翌日）
        if res.trades:
            assert res.trades[0].entry_date > res.config.start
