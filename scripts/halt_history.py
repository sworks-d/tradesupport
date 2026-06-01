"""HALT 発火・解除履歴の可視化 CLI（v2.10 P17）。

`~/.trading-agent/HALT.history.jsonl` から HALT 履歴を読み出し、タイムラインで表示。
HALT ファイル自体は最新状態のみだが、history.jsonl に過去発火・解除が記録される。

使い方:
  .venv/bin/python scripts/halt_history.py
  .venv/bin/python scripts/halt_history.py --recent 20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HISTORY_PATH = Path("~/.trading-agent/HALT.history.jsonl").expanduser()


def main() -> int:
    p = argparse.ArgumentParser(description="HALT 履歴可視化（P17）")
    p.add_argument("--recent", type=int, default=20, help="表示する直近件数")
    args = p.parse_args()

    if not HISTORY_PATH.exists():
        print(f"HALT 履歴ファイルが存在しません: {HISTORY_PATH}")
        print("HALT がまだ発火していない、または履歴記録が無効化されている可能性。")
        return 0

    events = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        print(f"HALT 履歴: 0 件")
        return 0

    recent = events[-args.recent :]
    print(f"=== HALT 履歴（直近 {len(recent)} 件 / 全 {len(events)} 件） ===")
    for ev in recent:
        action = ev.get("action", "?")
        ts = ev.get("at", "")
        reason = ev.get("reason", "")
        source = ev.get("source", "")
        icon = "🔴" if action == "triggered" else "🟢"
        print(f"  {icon} [{ts}] {action} (source={source})")
        if reason:
            print(f"     reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
