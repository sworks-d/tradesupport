"""価格ベース・バックテスト（先読みなし）。RESEARCH_METHODS 領域4（FINSABER流の正直な検証）。

「規律（BALTHASARシグナル＋R-mult＋損切り）が過去でプラス期待だったか」を**過去株価だけ**で測る。
- **先読みしない**：各時点 i の判断は prices[:i+1] のみ。財務(MELCHIOR)は point-in-time が要るため
  **ここでは扱わない**（現財務を過去判断に使うと look-ahead bias＝研究の罠）。価格は先読み回避可。
- 1トレード＝シグナル→エントリー、target/stop/保有期間満了で手仕舞い。重複建てはしない。
- 成績は R-multiple / hit-miss / Track Record（評価層と同じ単位）。

純粋関数（価格列・シグナル関数を注入）でテスト可能。LLM非関与。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from trading_agent.evaluation.metrics import (
    EvalResult,
    TrackRecord,
    build_track_record,
    evaluate_position,
)

# prices[:i+1] を見て「この時点で買うか」を返すシグナル関数
SignalFn = Callable[[list[float]], bool]


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    outcome: str  # hit / miss / neutral
    r_multiple: float
    bars_held: int


def backtest_signal(
    prices: list[float],
    *,
    signal_fn: SignalFn,
    hold_bars: int,
    stop_pct: float,
    target_return: float,
    warmup: int = 60,
) -> tuple[list[Trade], TrackRecord]:
    """価格列に signal_fn を適用し、先読みなしで建玉→手仕舞いを再生する。

    warmup：シグナル算定に要る最小バー数（それ未満では建てない）。
    """
    trades: list[Trade] = []
    results: list[EvalResult] = []
    n = len(prices)
    i = max(warmup, 1)
    while i < n - 1:
        if prices[i] > 0 and signal_fn(prices[: i + 1]):
            entry = prices[i]
            stop_price = entry * (1.0 - stop_pct)
            target_price = entry * (1.0 + target_return)
            exit_idx = min(i + hold_bars, n - 1)
            # 期間内に stop か target に触れたら早期手仕舞い（先に触れた方）
            for j in range(i + 1, min(i + hold_bars, n - 1) + 1):
                if prices[j] <= stop_price or prices[j] >= target_price:
                    exit_idx = j
                    break
            res = evaluate_position(
                entry_price=entry, exit_price=prices[exit_idx],
                target_return=target_return, stop_pct=stop_pct,
            )
            trades.append(
                Trade(
                    entry_idx=i, exit_idx=exit_idx, entry_price=entry,
                    exit_price=prices[exit_idx], outcome=res.outcome,
                    r_multiple=res.r_multiple, bars_held=exit_idx - i,
                )
            )
            results.append(res)
            i = exit_idx + 1  # 重複建てしない
        else:
            i += 1
    return trades, build_track_record(results)


def sma_cross_signal(short: int = 20, long: int = 60) -> SignalFn:
    """ゴールデンクロス（短期SMAが長期SMAを上抜け）でエントリー。先読みなし。"""

    def signal(prices: list[float]) -> bool:
        if len(prices) <= long + 1:
            return False

        def sma(seq: list[float], p: int) -> float:
            return sum(seq[-p:]) / p

        s_now, s_prev = sma(prices, short), sma(prices[:-1], short)
        l_now, l_prev = sma(prices, long), sma(prices[:-1], long)
        return s_prev <= l_prev and s_now > l_now  # 直近バーで上抜け

    return signal
