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


def _vc(judge: str, verdict: str, claim: str) -> JudgeVerdict:
    """反証（counter_within_domain）付きの審判判定。"""
    v = _v(judge, verdict)
    v.counter_within_domain = [{"claim": claim, "source_refs": [{"source": "computed"}]}]
    return v


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


# --- B-5 統合：全会一致でも内在不安 -------------------------------------------
def test_split_unanimous_buy_with_internal_unease() -> None:
    verdicts = [
        _vc("MELCHIOR", "buy", "PERが割高"),
        _vc("BALTHASAR", "buy", "RSI過熱"),
        _v("CASPER", "buy"),
    ]
    s = classify_split(verdicts)
    assert s.agree_count == 3
    assert "内在不安" in s.interpretation


def test_split_unanimous_buy_single_counter_stays_confident() -> None:
    # 反証が1審判だけなら従来どおり（内在不安にはしない）
    verdicts = [_vc("BALTHASAR", "buy", "RSI過熱"), _v("MELCHIOR", "buy"), _v("CASPER", "buy")]
    s = classify_split(verdicts)
    assert "内在不安" not in s.interpretation
    assert "確信度は高い" in s.interpretation


# --- B-4 碇が各審判の反証を束ねる -------------------------------------------
def test_commander_aggregates_counters() -> None:
    verdicts = [
        _vc("MELCHIOR", "buy", "営業赤字"),
        _vc("BALTHASAR", "buy", "RSI過熱"),
        _v("CASPER", "buy"),
    ]
    s = classify_split(verdicts)
    vr = verify(verdicts)
    rec = command(verdicts, s, vr)
    assert "内在反証＝" in rec.counter_argument
    assert "RSI過熱" in rec.counter_argument
    assert "営業赤字" in rec.counter_argument
    assert rec.magi_compliant is True  # 審判の摘出を束ねるだけ＝MAGI内


def test_commander_no_counter_suffix_when_none() -> None:
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")]
    s = classify_split(verdicts)
    vr = verify(verdicts)
    rec = command(verdicts, s, vr)
    assert "内在反証＝" not in rec.counter_argument
