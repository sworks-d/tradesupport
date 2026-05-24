"""統合機構（割れ方の類型化）。B4 / MASTER §2.3。

判断しない・採点しない・推奨しない。3審判の見解を並置し、割れ方を類型化するだけ。
総合スコアは出さない（SCORE: NONE）。4類型（MASTER §2.3）に当てはめる。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from trading_agent.magi.policy import voting
from trading_agent.models.magi import JudgeVerdict

_WORD = {"buy": "買い", "warn": "慎重", "hold": "中立", "sell": "売り", "na": "判定不能"}


@dataclass
class SplitResult:
    agree_count: int
    total: int
    label: str  # 例「2/3 買い・割れ」
    interpretation: str  # 割れ方の解釈（推奨はしない）


def classify_split(verdicts: list[JudgeVerdict]) -> SplitResult:
    """割れ方を類型化する。合意・確信度は**投票審判（業績MELCHIOR・文脈CASPER）だけ**で数える。

    BALTHASAR（株価）は方向票を数えない（コイン投げ＝確信度を水増しするため）。ただし
    価格状態は「参考・投票外」として併記し、反証（counter_within_domain）は内在不安に使う。
    """
    voting_v = voting(verdicts)  # MELCHIOR・CASPER のみ
    total = len(voting_v)
    actionable = [v for v in voting_v if v.verdict != "na"]
    by = {v.judge: v for v in verdicts}
    m, c, b = by.get("MELCHIOR"), by.get("CASPER"), by.get("BALTHASAR")

    if actionable:
        top, n = Counter(v.verdict for v in actionable).most_common(1)[0]
    else:
        top, n = "na", 0
    word = _WORD.get(top, top)
    agree = "一致" if n == total else "割れ"
    label = f"{n}/{total} {word}・{agree}（株価=投票外）"

    def is_buy(v: JudgeVerdict | None) -> bool:
        return v is not None and v.verdict == "buy"

    def not_buy(v: JudgeVerdict | None) -> bool:
        return v is not None and v.verdict != "buy"

    # B-5：全会一致でも複数審判が自領域内に逆向きの事実を摘出＝内在不安（BALTHASARの事実も数える）
    judges_with_counter = sum(1 for v in verdicts if v.counter_within_domain)
    internal_unease = total > 0 and n == total and judges_with_counter >= 2

    # 価格は投票外だが「事実」として併記（票には数えない）
    price_note = ""
    if b is not None and b.verdict != "na":
        price_note = f" 株価(参考・投票外)：{_WORD.get(b.verdict, b.verdict)}。"

    if actionable and n == total and top == "buy":
        if internal_unease:
            interp = (
                "業績・文脈が一致（買い）だが、複数審判が自領域内に逆向きの事実を摘出＝内在不安。"
                "全会一致でも確信度は割り引くべき。"
            )
        else:
            interp = "業績・文脈が一致（買い）。確信度は相対的に高い（同方向に誤る可能性は残る）。"
    elif is_buy(m) and not_buy(c):
        interp = "業績◯・文脈✕。数字は良いが上昇の文脈が弱い/材料不足。織り込み済みの疑い。"
    elif is_buy(c) and not_buy(m):
        interp = "文脈◯・業績✕。材料はあるが業績の裏付けが弱い。"
    elif total > 0 and n == total:
        interp = f"業績・文脈が一致（{word}）。"
    else:
        interp = "割れ。判断は分かれている（統合機構は推奨を出さない）。"

    return SplitResult(agree_count=n, total=total, label=label, interpretation=interp + price_note)
