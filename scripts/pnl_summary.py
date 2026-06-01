"""損益サマリ + 分布表示 CLI（v2.10 P5/P7）。

使い方:
  .venv/bin/python scripts/pnl_summary.py                # paper / live 別 + 各軸の集計
  .venv/bin/python scripts/pnl_summary.py --histogram    # 損益分布ヒストグラムも
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.reporting.pnl_analytics import (
    aggregate_pnl,
    distribution_buckets,
)


def main() -> int:
    p = argparse.ArgumentParser(description="損益サマリ（P5/P7）")
    p.add_argument("--histogram", action="store_true", help="損益分布ヒストグラム")
    args = p.parse_args()

    engine = get_engine(Path("data") / "trading.sqlite")

    for broker_mode in ("paper", "live"):
        print(f"\n=== {broker_mode.upper()} モード ===")
        for group_by in ("personality", "strategy_category", "sector"):
            summaries = aggregate_pnl(engine, broker_mode=broker_mode, group_by=group_by)
            if not summaries:
                continue
            print(f"\n  ▸ {group_by} 別:")
            print(f"    {'label':<20} {'active':>7} {'closed':>7} {'勝/負':>10} {'勝率':>7} {'累積 P/L':>12} {'平均%':>8}")
            for label, sm in sorted(summaries.items(), key=lambda x: -x[1].realized_pnl_jpy):
                wl = f"{sm.win_count}/{sm.loss_count}"
                sign = "+" if sm.realized_pnl_jpy >= 0 else ""
                print(
                    f"    {label[:20]:<20} {sm.active_count:>7} {sm.closed_count:>7} "
                    f"{wl:>10} {sm.win_rate*100:>6.0f}% "
                    f"{sign}¥{int(sm.realized_pnl_jpy):>10,} {sm.avg_return_pct*100:>7.2f}%"
                )

        if args.histogram:
            # 全 closed のリターン分布
            all_returns: list[float] = []
            for sm in aggregate_pnl(engine, broker_mode=broker_mode, group_by="personality").values():
                all_returns.extend(sm.returns)
            if all_returns:
                print(f"\n  ▸ {broker_mode} 損益分布ヒストグラム (n={len(all_returns)}):")
                buckets = distribution_buckets(all_returns, n_buckets=10)
                max_count = max(b[2] for b in buckets)
                for lo, hi, count in buckets:
                    bar = "█" * int(count / max_count * 30) if max_count > 0 else ""
                    print(f"    [{lo*100:>+6.1f}% 〜 {hi*100:>+6.1f}%] {count:>4} {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
