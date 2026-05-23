"""朝バッチ実行 CLI（実運用の入口）。

パイプラインを端から端まで回す：
  pre_check→topics→screening→market_analyst→sell→portfolio→materialize→magi_verify→…
MAGI候補を decision として永続化し、信用性(S5)/EDINET開示(S4b)/反証(B群) を効かせる。

  .venv/bin/python scripts/run_morning_batch.py             # 弾ON（信用性/EDINET・実データ）
  .venv/bin/python scripts/run_morning_batch.py --no-quality  # 弾OFF（決定論のみ・低コスト/高速）
  .venv/bin/python scripts/run_morning_batch.py --dry-run     # 永続化を抑制（試走）

前提：data/trading.sqlite に universe 投入済（scripts/load_universe.py）。
build_host が settings（ANTHROPIC等）を読むため .env 必須。LLM/ネット使用＝コスト注意。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.orchestrator.morning_batch import run_morning_batch
from trading_agent.screening import fetch_financials


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    quality = "--no-quality" not in sys.argv

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    # 弾ON＝信用性フィルタ(S5)＋EDINET開示(S4b)を magi_verify に効かせる
    financials_fetcher = fetch_financials if quality else None

    print(f"=== morning batch (quality={'ON' if quality else 'OFF'}, dry_run={dry_run}) ===")
    batch = await run_morning_batch(engine, dry_run=dry_run, financials_fetcher=financials_fetcher)

    print(f"\nstatus: {batch.status}")
    for node, st in batch.node_status.items():
        mark = "✓" if st == "success" else "✗"
        print(f"  {mark} {node}: {st}")
    print(f"\n{batch.summary}")

    # 当日の決裁待ち decision（MAGIの判定つき）を表示
    today = batch.invocation_id.removeprefix("morning_")
    with Session(engine) as session:
        decisions = session.exec(
            select(Decision)
            .where(col(Decision.status) == "awaiting")
            .where(col(Decision.date) == today)
        ).all()
    print(f"\n決裁待ち decision: {len(decisions)} 件")
    for d in decisions:
        print(f"  {d.ticker}  status={d.status}  碇の構え={d.gendo_stance}")
    print("\n→ 決裁は moomoo 画面で手動。承認後の発注リストは portfolio.build_order_list を使用。")


if __name__ == "__main__":
    asyncio.run(main())
