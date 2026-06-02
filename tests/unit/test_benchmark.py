"""A4: 保有期間ベンチマーク（TOPIX）lookup の単体テスト。ネット非依存（closes 注入）。"""

from __future__ import annotations

import datetime as dt

from trading_agent.evaluation.benchmark import (
    holding_period_return,
    make_topix_benchmark_lookup,
)
from trading_agent.models.decisions import Decision

# 連続営業日の終値（土日は欠損させて遡り挙動も確認）
_CLOSES = {
    dt.date(2026, 1, 5): 2000.0,
    dt.date(2026, 1, 6): 2010.0,
    dt.date(2026, 4, 1): 2200.0,  # +10% from 2000
}


class TestHoldingPeriodReturn:
    def test_basic_return(self) -> None:
        r = holding_period_return(_CLOSES, dt.date(2026, 1, 5), dt.date(2026, 4, 1))
        assert r == (2200.0 - 2000.0) / 2000.0  # +10%

    def test_walks_back_to_nearest_trading_day(self) -> None:
        # entry が休場日(1/4)でも直近営業日(1/5以前→無いので遡れず)…ここは 1/7 を exit にして
        # entry=1/7(欠損)→1/6 へ遡る挙動を確認
        r = holding_period_return(_CLOSES, dt.date(2026, 1, 5), dt.date(2026, 1, 7))
        assert r == (2010.0 - 2000.0) / 2000.0  # exit 1/7→1/6 へ遡る

    def test_invalid_period_returns_none(self) -> None:
        assert holding_period_return(_CLOSES, dt.date(2026, 4, 1), dt.date(2026, 1, 5)) is None

    def test_missing_price_returns_none(self) -> None:
        # 遡り 10 日でも見つからない場合 None（推測しない）
        assert holding_period_return(_CLOSES, dt.date(2025, 1, 1), dt.date(2025, 2, 1)) is None


class TestTopixLookup:
    def _dec(self, *, date, eval_date) -> Decision:
        return Decision(
            date=date, ticker="7203", action="buy", status="filled",
            evaluation_date=eval_date,
        )

    def test_lookup_uses_holding_period(self) -> None:
        lookup = make_topix_benchmark_lookup(
            closes=_CLOSES, today=dt.date(2026, 4, 2)
        )
        d = self._dec(date=dt.date(2026, 1, 5), eval_date=dt.date(2026, 4, 1))
        assert lookup(d) == (2200.0 - 2000.0) / 2000.0

    def test_future_eval_clamped_to_today(self) -> None:
        # 評価期日が未到来なら today までで暫定評価
        lookup = make_topix_benchmark_lookup(
            closes=_CLOSES, today=dt.date(2026, 4, 1)
        )
        d = self._dec(date=dt.date(2026, 1, 5), eval_date=dt.date(2026, 12, 31))
        assert lookup(d) == (2200.0 - 2000.0) / 2000.0  # today=4/1 まで

    def test_empty_series_returns_none(self) -> None:
        lookup = make_topix_benchmark_lookup(closes={}, today=dt.date(2026, 4, 2))
        d = self._dec(date=dt.date(2026, 1, 5), eval_date=dt.date(2026, 4, 1))
        assert lookup(d) is None

    def test_entry_date_overrides_decision_date(self) -> None:
        # A7: benchmark 起点は entry_date 優先（遅延 fill の α 歪み防止）
        lookup = make_topix_benchmark_lookup(closes=_CLOSES, today=dt.date(2026, 4, 2))
        # decision 日は 1/4（休場・price なし）だが実約定 entry_date=1/5 → 1/5 起点で計算
        d = Decision(
            date=dt.date(2026, 1, 4), ticker="7203", action="buy", status="filled",
            evaluation_date=dt.date(2026, 4, 1), entry_date=dt.date(2026, 1, 5),
        )
        assert lookup(d) == (2200.0 - 2000.0) / 2000.0  # entry_date=1/5 の 2000 起点
