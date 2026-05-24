"""MAGI 合意ポリシー：どの審判の『方向票』を合意・確信度に数えるか（2026-05-24）。

BALTHASAR（株価/テクニカル）は液体大型株でエントリー予測力ゼロ（勝率≈50%＝コイン投げ）と
実測済（offense-edge スカウト 2026-05-24）。よって **方向票（buy/hold/warn）は合意・確信度・
決裁前ゲートの判定から外す**＝コイン投げ票が「全会一致＝高確信」を水増しするのを防ぐ。

ただし BALTHASAR の `counter_within_domain`（過熱・弱気ダイバージェンス等の"事実の摘出"）は
反証として**残す**（事実は有用、占いの票は無用）。碇の反対論拠・内在不安はこの反証を使い続ける。
"""

from __future__ import annotations

from collections.abc import Iterable

from trading_agent.models.magi import JudgeVerdict

# 合意・確信度に数える審判（方向票が意味を持つ＝業績・文脈）。
VOTING_JUDGES: tuple[str, ...] = ("MELCHIOR", "CASPER")
# 方向票は数えない審判（事実摘出＝反証としてのみ使う）。
NON_VOTING_JUDGES: tuple[str, ...] = ("BALTHASAR",)


def voting(verdicts: Iterable[JudgeVerdict]) -> list[JudgeVerdict]:
    """合意・確信度に数える審判だけを返す（BALTHASAR を除く）。"""
    return [v for v in verdicts if v.judge in VOTING_JUDGES]
