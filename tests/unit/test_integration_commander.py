"""統合機構（B4 割れ方）と碇司令（B5 推奨＋反対論拠）の単体テスト。

いずれもコード生成（実費0・再現可能）。総合スコアを出さない／反対論拠を必ず併記する。
"""

from __future__ import annotations

from trading_agent.magi import classify_split, command, verify
from trading_agent.models.magi import JudgeVerdict


def _v(judge: str, verdict: str, reason: str = "x") -> JudgeVerdict:
    return JudgeVerdict(
        ticker="NVDA", judge=judge, verdict=verdict, confidence="中", reason=reason
    )


# --- B4 統合機構 ---------------------------------------------------------
def test_split_unanimous_buy() -> None:
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")])
    assert s.agree_count == 3
    assert "一致" in s.label
    assert "確信度は高い" in s.interpretation


def test_split_fundamental_ok_technical_no() -> None:
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "warn"), _v("CASPER", "buy")])
    assert "業績◯・株価✕" in s.interpretation


def test_split_technical_ok_fundamental_no() -> None:
    s = classify_split([_v("MELCHIOR", "warn"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")])
    assert "株価◯・業績✕" in s.interpretation


def test_split_context_weak() -> None:
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "na")])
    assert "文脈✕" in s.interpretation


# --- B5 碇司令 -----------------------------------------------------------
def test_commander_always_has_counter_argument() -> None:
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")]
    s = classify_split(verdicts)
    vr = verify(verdicts)
    rec = command(verdicts, s, vr)
    assert rec.recommendation
    assert rec.counter_argument.startswith("反対するなら：")  # 必ず併記
    assert rec.magi_compliant is True


def test_commander_holds_when_split_or_na() -> None:
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "hold"), _v("CASPER", "na")]
    s = classify_split(verdicts)
    vr = verify(verdicts)
    rec = command(verdicts, s, vr)
    assert "保留" in rec.recommendation
    assert rec.counter_argument.startswith("反対するなら：")
