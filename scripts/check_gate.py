"""増額ゲート⑥の現況を表示する CLI（勝ち定義の単一判定・コスト0）。

  .venv/bin/python scripts/check_gate.py            # paper + live + combined(参考)
  .venv/bin/python scripts/check_gate.py --paper    # paper のみ
  .venv/bin/python scripts/check_gate.py --live      # live のみ

broker_mode 別に official_gate_evaluation を呼び、各要件の達成/未達を隠さず表示する。
増額（tier 昇格）の可否は **broker_mode 別の判定** だけを根拠にする。
combined は paper(edge検証)+live(実運用)の混在＝参考のみ・増額不可（codex 指摘）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.gate import (
    combined_gate_reference,
    official_gate_evaluation,
)


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    only_paper = "--paper" in sys.argv
    only_live = "--live" in sys.argv
    show_all = not (only_paper or only_live)

    if only_paper or show_all:
        r = official_gate_evaluation(engine, broker_mode="paper")
        print(r.summary())
        if not r.passed:
            print("→ paper: 未通過の要件を満たすまで増額しない（フォワード実績を貯め続ける）。")
        print()

    if only_live or show_all:
        r = official_gate_evaluation(engine, broker_mode="live")
        print(r.summary())
        if not r.passed:
            print("→ live: 未通過の要件を満たすまで増額しない。")
        print()

    if show_all:
        print(combined_gate_reference(engine).summary())


if __name__ == "__main__":
    main()
