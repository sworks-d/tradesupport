"""増額ゲート⑥の現況を表示する CLI（勝ち定義の単一判定・コスト0）。

  .venv/bin/python scripts/check_gate.py

official_gate_evaluation を呼び、各要件の達成/未達を隠さず表示する。
増額（tier 昇格）の可否はこの判定だけを根拠にする。
"""

from __future__ import annotations

from pathlib import Path

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.gate import official_gate_evaluation


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    result = official_gate_evaluation(engine)
    print(result.summary())
    if not result.passed:
        print("\n→ 未通過の要件を満たすまで増額しない（フォワード実績を貯め続ける）。")


if __name__ == "__main__":
    main()
