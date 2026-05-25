"""GENDO 推奨（初心者コーチ）。MAGI出力＋規律から「推奨アクション＋ガードレール」を出す。

決めない（決裁は人間・原則2）が、初心者を支えるため**推奨アクションまで**出す。**守り主導**：
- 守りが赤（credibility warn）→ 見送り（攻めに関わらず触らない＝実証済みの「外す」最優先）。
- 投票審判（業績MELCHIOR・文脈CASPER）が揃い守りが青 → コアに積み増し（質分散・勝ち放任）。
- 攻めが強いが合意は弱く守り青 → サテライトで小さく試す（死ぬサイズ・**昇格前は枠0=情報のみ**）。
- 割れ/na/未照合 → 静観。保有が固定stop/期限に達したら → 撤退。

攻めはエッジ未実証＝確信度は灰色。新しい数値・事実は加えない（MAGI/規律の範囲内＝magi_compliant）。
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.magi.commander import CommanderResult
from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.integration import SplitResult
from trading_agent.magi.policy import voting
from trading_agent.models.magi import JudgeVerdict
from trading_agent.portfolio.sizing import SizeRec

# 推奨アクションの語彙（初心者向けに少数・明確）
ACTIONS = ("積み増し", "小さく試す", "静観", "見送り", "撤退")

_LEARN = {
    "積み増し": "稼ぎは所有×時間×複利。当てにいかず持ち続けるほど勝ちやすい。",
    "小さく試す": "攻めは未実証の賭け。死ぬサイズに隔離すればコアは無傷。",
    "静観": "動かないのも規律。割れている時に賭けない。",
    "見送り": "「外す選定」は効く。買わない判断が一番効く局面。",
    "撤退": "事前に決めた線で機械的に降りる。塩漬けが最大の傷。",
}


@dataclass
class GendoCard:
    action: str  # ACTIONS のいずれか（推奨アクション）
    sleeve: str  # "core" / "satellite" / "—"
    reason: str  # やさしい理由（守り主導）
    counter: str  # 反対するなら（commander 由来）
    guardrail: str  # サイズ・枠・stop の具体
    defense_confidence: str  # 守りの確信（実証）
    offense_confidence: str  # 攻めの確信（較正前＝灰色）
    learn_note: str  # 学べる一言
    magi_compliant: bool = True


def _guardrail(action: str, sleeve: str, sizing: SizeRec | None) -> str:
    if action in ("見送り", "静観"):
        return "現金で待つ（無理に動かない）。"
    if action == "撤退":
        return "全量・機械的に手仕舞い（固定stop/保有期限）。"
    if sizing is None or sizing.amount_jpy <= 0:
        base = "コア枠で少額・分割" if sleeve == "core" else "サテライト枠（死ぬサイズ・昇格前は0）"
        return f"{base}。利確しない（勝ち放任）／損切りは固定stop。"
    where = "コア枠" if sleeve == "core" else "サテライト枠（死ぬサイズ・昇格前は0）"
    return (
        f"{where} ¥{sizing.amount_jpy:,.0f}（{sizing.shares:g}株/{sizing.weight:.0%}）"
        f"／損切り ¥{sizing.stop_price_jpy:,.0f}／利確しない（勝ち放任）。"
    )


def gendo_recommend(
    verdicts: list[JudgeVerdict],
    split: SplitResult,
    verification: VerificationResult,
    commander: CommanderResult,
    *,
    credibility_flag: str = "ok",
    offense_strong: bool = False,
    sizing: SizeRec | None = None,
    holding_exit: str | None = None,
) -> GendoCard:
    """MAGI出力＋規律から GENDO 推奨カードを作る（守り主導・決定論・新事実を加えない）。"""
    voting_v = voting(verdicts)
    buys = [v for v in voting_v if v.verdict == "buy"]
    unanimous_buy = bool(voting_v) and len(buys) == len(voting_v)
    red = credibility_flag == "warn"

    if holding_exit in ("stop", "time"):
        action, sleeve = "撤退", "—"
        why = "固定stop到達" if holding_exit == "stop" else "保有期限到達"
        reason = f"守りの規律：{why}。機械的に降りる（攻めの希望で覆さない）。"
    elif red:
        action, sleeve = "見送り", "—"
        reason = "守り＝赤（信用性の警戒＝粉飾/倒産の疑い）。攻めが何を言おうと地雷は触らない。"
    elif unanimous_buy and not verification.default_hold:
        action, sleeve = "積み増し", "core"
        reason = "守り＝安全（赤なし）＋業績・文脈が揃う。質コアとして淡々と積む。"
    elif offense_strong and not red:
        action, sleeve = "小さく試す", "satellite"
        reason = (
            "守りは青だが業績・文脈は揃わず。攻めは未実証＝サテライトで死ぬサイズだけ"
            "（昇格前は枠0＝情報のみ）。"
        )
    else:
        action, sleeve = "静観", "—"
        reason = "割れ/未照合で決め手なし。今は動かない（保留が既定）。"

    defense_conf = "●●●（実証＝外す選定は効く）" if credibility_flag in ("ok", "warn") else "●●"
    offense_conf = "○○（較正前＝参考・賭けない）" if offense_strong else "○（弱い/材料薄）"

    return GendoCard(
        action=action,
        sleeve=sleeve,
        reason=reason,
        counter=commander.counter_argument,
        guardrail=_guardrail(action, sleeve, sizing),
        defense_confidence=defense_conf,
        offense_confidence=offense_conf,
        learn_note=_LEARN[action],
        magi_compliant=commander.magi_compliant,
    )
