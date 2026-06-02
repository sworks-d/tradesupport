"""A8: trailing 相場局面分類 classify_market_cycle の単体テスト（純粋関数・ネット非依存）。"""

from __future__ import annotations

from trading_agent.wille.ritsuko import classify_market_cycle


class TestClassifyMarketCycle:
    def test_unknown_when_too_few(self) -> None:
        assert classify_market_cycle([100.0] * 30) == "unknown"

    def test_bull_above_ma_no_drawdown(self) -> None:
        # 緩やかな右肩上がり 250 本 → 直近は 200日MA 上・高値近辺 → bull
        closes = [100.0 + i * 0.5 for i in range(250)]
        assert classify_market_cycle(closes) == "bull"

    def test_bear_below_ma(self) -> None:
        # 右肩下がり 250 本 → 直近は 200日MA 下 → bear
        closes = [200.0 - i * 0.4 for i in range(250)]
        assert classify_market_cycle(closes) == "bear"

    def test_bear_on_deep_drawdown(self) -> None:
        # 上昇後に直近高値から 20% 急落 → DD≥15% で bear
        closes = [100.0 + i * 0.5 for i in range(200)]  # ~199 まで上昇
        peak = closes[-1]
        closes += [peak * (1 - 0.20)] * 5  # -20% 急落
        assert classify_market_cycle(closes) == "bear"

    def test_sideways(self) -> None:
        # 上昇後に高値から ~12% 戻し（DD が 10-15% 帯・latest は MA200 上）→ sideways
        rising = [100.0 + i * 0.25 for i in range(200)]  # 100 → ~149.75
        peak = rising[-1]
        pulled = [peak * (1 - 0.12)] * 5  # 高値から -12%
        closes = rising + pulled
        assert classify_market_cycle(closes) == "sideways"
