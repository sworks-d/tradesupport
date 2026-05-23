"""P6-a：評価メトリクス（R-multiple / hit-miss / Track Record）。RESEARCH_METHODS 領域4。

- **R-multiple**：成績を「1R（取ったリスク）の何倍取れたか」で測る（Van Tharp）。
  R = 実リターン ÷ 損切り%（=（exit−entry）/（entry×stop））。リスク調整後の共通単位。
- **hit/miss/neutral**：target到達=hit／stop到達=miss／中間=neutral。
- **Track Record**：命中率・平均R・平均リターン・対ベンチマーク超過。

バイアス回避（領域4）：評価は**評価期日の実価格**で行う＝先読み(look-ahead)しない（呼び出し側がdata_asof厳守）。
最低サンプル数に満たない間は「暫定」を明示。**全てコード計算**（LLM非関与）。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

# Track Record を「確定」とみなす最低サンプル数（領域4：少数の好成績を信じない）
MIN_SAMPLE = 30


@dataclass
class EvalResult:
    actual_return: float  # (exit-entry)/entry
    r_multiple: float  # actual_return / stop_pct（1Rの何倍か）
    outcome: str  # hit / miss / neutral
    benchmark_return: float | None = None  # 同期間のベンチマーク（任意）

    def excess_return(self) -> float | None:
        return None if self.benchmark_return is None else self.actual_return - self.benchmark_return


@dataclass
class TrackRecord:
    n: int
    hit_rate: float | None  # hit/(hit+miss)。actionableが無ければ None
    avg_return: float
    avg_r: float
    avg_excess: float | None  # 対ベンチマーク平均超過
    provisional: bool  # n < MIN_SAMPLE


def evaluate_position(
    *,
    entry_price: float,
    exit_price: float,
    target_return: float,
    stop_pct: float,
    benchmark_return: float | None = None,
) -> EvalResult:
    """エントリー価格と評価日の実価格から成績を出す。stop到達=miss・target到達=hit。"""
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    actual = (exit_price - entry_price) / entry_price
    r = actual / stop_pct if stop_pct > 0 else 0.0
    if actual <= -stop_pct:
        outcome = "miss"  # 損切りライン到達
    elif actual >= target_return:
        outcome = "hit"  # 目標到達
    else:
        outcome = "neutral"
    return EvalResult(
        actual_return=round(actual, 4),
        r_multiple=round(r, 3),
        outcome=outcome,
        benchmark_return=benchmark_return,
    )


def build_track_record(results: list[EvalResult]) -> TrackRecord:
    """評価済み結果群を集計（命中率・平均R・平均リターン・超過）。空は0埋め。"""
    n = len(results)
    if n == 0:
        return TrackRecord(0, None, 0.0, 0.0, None, provisional=True)
    hits = sum(1 for r in results if r.outcome == "hit")
    misses = sum(1 for r in results if r.outcome == "miss")
    decided = hits + misses
    hit_rate = (hits / decided) if decided > 0 else None
    excess = [r.excess_return() for r in results if r.excess_return() is not None]
    return TrackRecord(
        n=n,
        hit_rate=round(hit_rate, 3) if hit_rate is not None else None,
        avg_return=round(fmean(r.actual_return for r in results), 4),
        avg_r=round(fmean(r.r_multiple for r in results), 3),
        avg_excess=round(fmean(excess), 4) if excess else None,
        provisional=n < MIN_SAMPLE,
    )
