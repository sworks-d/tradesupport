"""決定を「見送り」= cancelled にする（買わなかった awaiting Decision を畳む）。

楽天には一切触れない・手元DBのみ。status を "cancelled" にするのは
misato.cleanup_for_fresh_run / morning_batch pre_check と同じ既存パターンに従う
（独自 SQL UPDATE ではなく ORM 経由）。materialize_decisions は cancelled を再利用しない設計。

使い方:
    .venv/bin/python scripts/skip_decision.py --decision-id 96
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision


def main() -> int:
    p = argparse.ArgumentParser(description="決定を見送り（cancelled）にする")
    p.add_argument("--decision-id", type=int, required=True)
    args = p.parse_args()

    engine = get_engine(Path("data") / "trading.sqlite")
    with Session(engine) as s:
        d = s.get(Decision, args.decision_id)
        if d is None:
            print(f"Decision id={args.decision_id} 見つからず")
            return 1
        if d.status != "awaiting":
            print(f"Decision id={d.id} status={d.status}（awaiting でない、見送り不可）")
            return 1
        ticker = d.ticker
        d.status = "cancelled"
        s.add(d)
        s.commit()
    print(f"✓ Decision id={args.decision_id} {ticker} を見送り（cancelled）にしました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
