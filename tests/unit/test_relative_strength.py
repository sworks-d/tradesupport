"""S7（テーマ）：相対力・4象限の単体テスト。価格列は合成（ネット非依存）。"""

from __future__ import annotations

from trading_agent.screening.relative_strength import (
    compute_relative_strength,
    market_proxy,
    relative_strength_live,
)


def _series(start: float, step: float, n: int = 80) -> list[float]:
    return [start + step * i for i in range(n)]


def test_leading_when_outperforms_both_windows() -> None:
    # 銘柄が市場より一貫して速く上昇 → leading
    ticker = _series(100, 2.0)  # 急上昇
    market = _series(100, 0.5)  # 緩やか
    res = compute_relative_strength(ticker, market)
    assert res.quadrant == "leading"
    assert res.rs_long > 0 and res.rs_short > 0


def test_lagging_when_underperforms() -> None:
    ticker = _series(100, 0.2)
    market = _series(100, 1.5)
    assert compute_relative_strength(ticker, market).quadrant == "lagging"


def test_improving_when_recent_turns_up() -> None:
    # 長期は市場に負けるが、直近(short)で市場を上回り始める＝improving（テーマV字）
    n = 80
    market = _series(100, 1.0, n)
    # 前半は出遅れ（0.3/日<市場1.0）、直近21日で市場(1.0)を上回る回復（1.5/日）だが
    # 63日window全体ではまだ市場に負ける＝improving
    ticker = [100 + 0.3 * i for i in range(n - 21)]
    last = ticker[-1]
    ticker += [last + 1.5 * (i + 1) for i in range(21)]
    res = compute_relative_strength(ticker, market)
    assert res.quadrant == "improving"
    assert res.rs_long < 0 < res.rs_short


def test_insufficient_history_is_na() -> None:
    assert compute_relative_strength([1, 2, 3], [1, 2, 3]).quadrant == "na"


def test_market_proxy_by_market() -> None:
    assert market_proxy("US") == "^GSPC"
    assert market_proxy("JP") == "^N225"
    assert market_proxy("XX") == "^GSPC"  # 既定


def test_live_handles_fetch_failure() -> None:
    def boom(_sym: str) -> list[float]:
        raise RuntimeError("net down")

    assert relative_strength_live("NVDA", "US", history=boom).quadrant == "na"


def test_live_uses_history_for_ticker_and_proxy() -> None:
    calls: list[str] = []

    def hist(sym: str) -> list[float]:
        calls.append(sym)
        return _series(100, 2.0) if sym == "NVDA" else _series(100, 0.5)

    res = relative_strength_live("NVDA", "US", history=hist)
    assert res.quadrant == "leading"
    assert "NVDA" in calls and "^GSPC" in calls  # 銘柄とproxyの両方を引く
