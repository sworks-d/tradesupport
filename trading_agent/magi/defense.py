"""防御層（ハルシネーション機械照合・決裁前ゲート）。B3 / MAGI_REQUIREMENTS 2H。

原則：数値はコードが照合した実データのみ。ゼロ化は狙わず、混入を前提に**決裁の直前に検出・遮断**。
3審判の判定に付いた出典(source_refs)・時点(data_asof)をコードが機械照合し、
未照合・割れ・判定不能があれば**決裁の既定を「保留」に寄せる**（人間が赤を承知で承認は可能）。

機械照合の3本柱（LLMは関与しない）：
  1. 数値照合：判定に出る数値が出典付きの実データに紐づくか（出典なし＝未照合＝赤）
  2. 出典実在：出典(source_refs)が存在するか（最低限 ref が付くか。URL生存確認は将来）
  3. 時点照合：各判定に as-of が付き、極端に古くないか
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trading_agent.magi.policy import voting
from trading_agent.models.magi import JudgeVerdict
from trading_agent.utils.time_utils import utcnow


@dataclass
class VerificationResult:
    figures_checked: bool  # 全 actionable 判定が出典・時点付きで照合できたか
    credibility_flag: str  # ok / warn / unknown（信用性フィルタ D-14）
    time_ok: bool  # 全 actionable 判定に as-of が付くか
    gendo_compliant: bool | None  # 碇MAGI準拠（B5。未実装は None）
    unverified_claims: list[str] = field(default_factory=list)
    default_hold: bool = True  # 決裁の既定を「保留」に寄せるか
    # v2.5 TASK-M12: default_hold の理由を分解（複雑 OR 式を可視化）
    default_hold_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# v2.1 TASK-M5: ジャッジ別の最大データ齢
# 旧: max_age_days=400 一律（1 年超でも通る）
# 新: 業績(四半期=最大90日)・株価(1週間)・文脈(2週間)
_MAX_AGE_BY_JUDGE = {
    "MELCHIOR": 100,  # 業績：四半期報告サイクル
    "BALTHASAR": 7,   # 株価：1 週間
    "CASPER": 14,     # ニュース：2 週間
}


def verify(
    verdicts: list[JudgeVerdict],
    *,
    now: datetime | None = None,
    max_age_days: int = 90,  # 既定を 400→90 に短縮（fallback 用）
    credibility_flag: str = "ok",
) -> VerificationResult:
    """3審判の判定を機械照合し、決裁前ゲート（既定保留）を判定する。

    `credibility_flag`＝信用性フィルタ(S5)の結果（ok/warn）。warn（粉飾/倒産の疑い）は
    決裁の既定を「保留」に寄せる（D-17：不正企業＝ゼロ化への保守側）。
    """
    now = now or utcnow()
    unverified: list[str] = []
    notes: list[str] = []
    actionable: list[JudgeVerdict] = []

    for v in verdicts:
        if v.verdict == "na":
            notes.append(f"{v.judge}:判定不能(na)")
            continue
        actionable.append(v)
        # 数値照合＝出典(provenance)が付いているか
        if not v.source_refs:
            unverified.append(f"{v.judge}:出典なし（未照合）")
        # 時点照合＝as-of が付いているか／極端に古くないか（v2.1: ジャッジ別の閾値）
        judge_max_age = _MAX_AGE_BY_JUDGE.get(v.judge, max_age_days)
        if v.data_asof is None:
            unverified.append(f"{v.judge}:時点なし")
        elif (now - v.data_asof) > timedelta(days=judge_max_age):
            notes.append(
                f"{v.judge}:データが古い（{v.data_asof.date()}・{judge_max_age}日超）"
            )

    figures_checked = len(unverified) == 0
    time_ok = all(v.data_asof is not None for v in actionable)
    gendo_compliant = None  # 碇司令(B5)未実装
    if credibility_flag == "warn":
        notes.append("信用性フィルタ警戒（粉飾/倒産の疑い）")
    elif credibility_flag == "unknown":
        # v2.5 TASK-P12: credibility 検証不能は保留側に寄せる
        notes.append("信用性フィルタ未検証（データ欠損で算定不能）")

    # 決裁前ゲート：合意・確信度は投票審判（業績MELCHIOR・文脈CASPER）のみで判定する。
    # BALTHASAR（株価＝コイン投げ）の方向票は既定保留の判定から外す（票の水増し/過剰ブロック防止）。
    # 未照合・時点欠落の照合は全 actionable に対し保守的に維持（透明性）。
    voting_v = voting(verdicts)
    has_na = any(v.verdict == "na" for v in voting_v)
    unanimous_buy = bool(voting_v) and all(v.verdict == "buy" for v in voting_v)
    # v2.1 TASK-M4: 「強い片方 buy + もう一方が hold/warn でない」も許容（推し枯渇緩和）
    # 推し（最強）= 全員 buy
    # 強い要検討 = 1 つ buy + もう 1 つ hold（warn は弾く）
    strong_partial_buy = (
        bool(voting_v)
        and any(v.verdict == "buy" for v in voting_v)
        and not any(v.verdict == "warn" for v in voting_v)
        and not has_na
    )
    # v2.5 TASK-M12: default_hold の理由を構造化
    default_hold_reasons: list[str] = []
    if not figures_checked:
        default_hold_reasons.append("数値照合 NG（出典/時点欠落）")
    if has_na:
        default_hold_reasons.append("判定不能(na)を含む")
    if not unanimous_buy and not strong_partial_buy:
        default_hold_reasons.append("買い合意なし（投票審判の合意不足）")
    if credibility_flag == "warn":
        default_hold_reasons.append("信用性 warn（粉飾/倒産疑い）")
    elif credibility_flag == "unknown":
        default_hold_reasons.append("信用性 unknown（検証不能）")
    default_hold = bool(default_hold_reasons)

    return VerificationResult(
        figures_checked=figures_checked,
        credibility_flag=credibility_flag,
        time_ok=time_ok,
        gendo_compliant=gendo_compliant,
        unverified_claims=unverified,
        default_hold=default_hold,
        default_hold_reasons=default_hold_reasons,
        notes=notes,
    )
