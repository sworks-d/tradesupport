"""統合機構（割れ方の類型化）。B4 / MASTER §2.3。

判断しない・採点しない・推奨しない。3審判の見解を並置し、割れ方を類型化するだけ。
総合スコアは出さない（SCORE: NONE）。4類型（MASTER §2.3）に当てはめる。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from trading_agent.models.magi import JudgeVerdict

_WORD = {"buy": "買い", "warn": "慎重", "hold": "中立", "sell": "売り", "na": "判定不能"}


@dataclass
class SplitResult:
    agree_count: int
    total: int
    label: str  # 例「2/3 買い・割れ」
    interpretation: str  # 割れ方の解釈（推奨はしない）


def classify_split(verdicts: list[JudgeVerdict]) -> SplitResult:
    total = len(verdicts)
    actionable = [v for v in verdicts if v.verdict != "na"]
    by = {v.judge: v for v in verdicts}
    m, b, c = by.get("MELCHIOR"), by.get("BALTHASAR"), by.get("CASPER")

    if actionable:
        top, n = Counter(v.verdict for v in actionable).most_common(1)[0]
    else:
        top, n = "na", 0
    word = _WORD.get(top, top)
    agree = "一致" if n == total else "割れ"
    label = f"{n}/{total} {word}・{agree}"

    def is_buy(v: JudgeVerdict | None) -> bool:
        return v is not None and v.verdict == "buy"

    def not_buy(v: JudgeVerdict | None) -> bool:
        return v is not None and v.verdict != "buy"

    # B-5：全会一致でも複数審判が自領域内に逆向きの事実を摘出＝内在不安
    judges_with_counter = sum(1 for v in verdicts if v.counter_within_domain)
    internal_unease = n == total and judges_with_counter >= 2

    if actionable and n == total and top == "buy":
        if internal_unease:
            interp = (
                "3審判一致（買い）だが、複数審判が自領域内に逆向きの事実を摘出＝内在不安。"
                "全会一致でも確信度は割り引くべき。"
            )
        else:
            interp = "3審判一致（買い）。確信度は高い。ただし全員が同方向に誤った可能性も残る。"
    elif is_buy(m) and not_buy(b):
        interp = "業績◯・株価✕。業績は良いが株価の勢いがない/下落中。タイミング尚早の疑い。"
    elif is_buy(b) and not_buy(m):
        interp = "株価◯・業績✕。株価は動くが業績裏付けが弱い。需給先行の疑い。"
    elif c is not None and c.verdict in ("warn", "na") and (is_buy(m) or is_buy(b)):
        interp = "文脈✕。数字は良いが上昇の文脈が弱い/判定材料不足。織り込み済みの疑い。"
    elif n == total:
        interp = f"3審判一致（{word}）。"
    else:
        interp = "割れ。判断は分かれている（統合機構は推奨を出さない）。"

    return SplitResult(agree_count=n, total=total, label=label, interpretation=interp)
