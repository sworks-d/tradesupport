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

from trading_agent.models.magi import JudgeVerdict
from trading_agent.utils.time_utils import utcnow


@dataclass
class VerificationResult:
    figures_checked: bool  # 全 actionable 判定が出典・時点付きで照合できたか
    credibility_flag: str  # ok / warn（信用性フィルタ D-14。未実装は ok）
    time_ok: bool  # 全 actionable 判定に as-of が付くか
    gendo_compliant: bool | None  # 碇MAGI準拠（B5。未実装は None）
    unverified_claims: list[str] = field(default_factory=list)
    default_hold: bool = True  # 決裁の既定を「保留」に寄せるか
    notes: list[str] = field(default_factory=list)


def verify(
    verdicts: list[JudgeVerdict],
    *,
    now: datetime | None = None,
    max_age_days: int = 400,
) -> VerificationResult:
    """3審判の判定を機械照合し、決裁前ゲート（既定保留）を判定する。"""
    now = now or utcnow()
    unverified: list[str] = []
    notes: list[str] = []
    has_na = False
    buy_count = 0
    actionable: list[JudgeVerdict] = []

    for v in verdicts:
        if v.verdict == "na":
            has_na = True
            notes.append(f"{v.judge}:判定不能(na)")
            continue
        actionable.append(v)
        if v.verdict == "buy":
            buy_count += 1
        # 数値照合＝出典(provenance)が付いているか
        if not v.source_refs:
            unverified.append(f"{v.judge}:出典なし（未照合）")
        # 時点照合＝as-of が付いているか／極端に古くないか
        if v.data_asof is None:
            unverified.append(f"{v.judge}:時点なし")
        elif (now - v.data_asof) > timedelta(days=max_age_days):
            notes.append(f"{v.judge}:データが古い（{v.data_asof.date()}）")

    figures_checked = len(unverified) == 0
    time_ok = all(v.data_asof is not None for v in actionable)
    credibility_flag = "ok"  # 信用性フィルタ(D-14)未実装 → 既定 ok
    gendo_compliant = None  # 碇司令(B5)未実装

    # 決裁前ゲート：未照合 or 判定不能 or 全会一致買いでない（割れ）→ 既定「保留」
    unanimous_buy = bool(actionable) and buy_count == len(verdicts)
    default_hold = (not figures_checked) or has_na or (not unanimous_buy)

    return VerificationResult(
        figures_checked=figures_checked,
        credibility_flag=credibility_flag,
        time_ok=time_ok,
        gendo_compliant=gendo_compliant,
        unverified_claims=unverified,
        default_hold=default_hold,
        notes=notes,
    )
