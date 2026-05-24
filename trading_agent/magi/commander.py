"""碇司令（推奨役・決定しない）。B5 / MASTER §2.4。

MAGIの出力**だけ**を根拠に、推奨と反対論拠を**必ず両方**生成する。決定はしない（決裁は人間）。
この段では決定論生成（コスト0）。構造上 MAGI 外の新事実は加わらない（magi_compliant=True）。
LLMによる文面化は後日オプトイン（その時は碇MAGI準拠を機械照合する）。
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.integration import SplitResult
from trading_agent.magi.policy import voting
from trading_agent.models.magi import JudgeVerdict

_WORD = {"buy": "買い", "warn": "慎重", "hold": "中立", "sell": "売り", "na": "判定不能"}
_ROLE = {"MELCHIOR": "業績", "BALTHASAR": "株価", "CASPER": "文脈"}


@dataclass
class CommanderResult:
    recommendation: str
    counter_argument: str
    magi_compliant: bool
    src_note: str


def command(
    verdicts: list[JudgeVerdict],
    split: SplitResult,
    verification: VerificationResult,
) -> CommanderResult:
    """3審判＋割れ方＋防御層の結果だけから、推奨と反対論拠を生成する。"""
    # 合意・確信度は投票審判（業績・文脈）のみで数える。株価(BALTHASAR)は投票外（反証にのみ使う）。
    voting_v = voting(verdicts)
    actionable = [v for v in voting_v if v.verdict != "na"]
    buys = [v for v in actionable if v.verdict == "buy"]
    dissent = [v for v in actionable if v.verdict != "buy"]
    na_judges = [v.judge for v in voting_v if v.verdict == "na"]
    unanimous_buy = len(voting_v) > 0 and len(buys) == len(voting_v)

    # 推奨（MAGIの割れ方・防御層のみから）
    if unanimous_buy:
        rec = "私の推奨は買い（小さめに）。業績・文脈が揃い確信度は相対的に高い（株価=投票外）。"
    elif na_judges:
        rec = f"私の推奨は保留。{'・'.join(na_judges)}が判定不能で、確信を持てない。"
    elif verification.default_hold:
        rec = "私の推奨は保留（または極小）。3審判が割れており、強くは推せない。"
    else:
        rec = "私の推奨は中立。決め手に欠ける。"

    # 反対論拠（必ず併記）
    if buys and dissent:
        d = dissent[0]
        word = _WORD.get(d.verdict, d.verdict)
        counter = f"反対するなら：{d.judge}（{_ROLE.get(d.judge, '')}）が{word}。{d.reason}"
    elif unanimous_buy:
        extra = f"さらに{'・'.join(na_judges)}は判定不能で死角が残る。" if na_judges else ""
        counter = "反対するなら：業績・文脈の一致は両者が同方向に見落とす可能性も残す。" + extra
    elif not buys:
        counter = "反対するなら：判定は買いを支持しないが、見送れば上昇機会を逃す恐れもある。"
    else:
        counter = "反対するなら：判断材料が不足しており、確信のある反論も難しい。"

    # B-4：各審判が自領域内で摘出した「逆向きの事実」を束ねて反対論拠に足す（MAGI内・R5）
    counter += _aggregate_counters(verdicts)

    return CommanderResult(
        recommendation=rec,
        counter_argument=counter,
        magi_compliant=True,  # 決定論生成＝MAGI外の新事実なし（反証も審判の摘出を束ねるだけ）
        src_note="根拠：3審判の判定と各審判の内在反証のみ。新たな事実は加えていない。予測値は推奨の根拠にしていない。",
    )


def _aggregate_counters(verdicts: list[JudgeVerdict]) -> str:
    """各審判の counter_within_domain を束ねた一文（B-4）。無ければ空文字。"""
    facts: list[str] = []
    for v in verdicts:
        for c in v.counter_within_domain or []:
            claim = str(c.get("claim", "")).strip()
            if claim:
                facts.append(f"{_ROLE.get(v.judge, v.judge)}：{claim}")
    if not facts:
        return ""
    return " 各審判の内在反証＝" + "／".join(facts) + "。"
