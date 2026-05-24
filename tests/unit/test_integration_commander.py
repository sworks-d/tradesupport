"""統合機構（B4 割れ方）と碇司令（B5 推奨＋反対論拠）の単体テスト。

2026-05-24 改訂：合意・確信度は**投票審判（業績MELCHIOR・文脈CASPER）のみ**で数える。
BALTHASAR（株価）は投票外＝方向票は数えないが、反証(counter_within_domain)・価格注記は残す。
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


# --- B4 統合機構（投票=業績・文脈の2審判） -----------------------------------
def test_split_voting_unanimous_buy() -> None:
    # 業績・文脈が買い → 一致。株価(warn)は投票外＝agree_count に数えない。
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "warn"), _v("CASPER", "buy")])
    assert s.agree_count == 2
    assert s.total == 2
    assert "一致" in s.label
    assert "確信度は相対的に高い" in s.interpretation
    assert "投票外" in s.interpretation  # 株価=参考の併記


def test_split_price_vote_does_not_inflate() -> None:
    # 株価だけ buy・業績は warn → 一致にならない（コイン投げ票で水増ししない）
    s = classify_split([_v("MELCHIOR", "warn"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")])
    assert s.agree_count == 1
    assert "文脈◯・業績✕" in s.interpretation


def test_split_fundamental_ok_context_no() -> None:
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "warn")])
    assert "業績◯・文脈✕" in s.interpretation


def test_split_context_na_is_not_agreement() -> None:
    # 文脈 na → 投票では一致しない（業績◯・文脈✕）。株価buyは数えない。
    s = classify_split([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "na")])
    assert s.agree_count == 1
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


def test_commander_buy_when_voting_unanimous_even_if_price_dissents() -> None:
    # 業績・文脈が買い、株価 hold（投票外）→ 推奨は買い（株価票でブロックしない）
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "hold"), _v("CASPER", "buy")]
    rec = command(verdicts, classify_split(verdicts), verify(verdicts))
    assert "買い" in rec.recommendation


def test_commander_holds_when_voting_na() -> None:
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "hold"), _v("CASPER", "na")]
    rec = command(verdicts, classify_split(verdicts), verify(verdicts))
    assert "保留" in rec.recommendation
    assert rec.counter_argument.startswith("反対するなら：")


# --- B-5 統合：全会一致でも内在不安（反証は BALTHASAR も数える） ----------------
def test_split_unanimous_buy_with_internal_unease() -> None:
    verdicts = [
        _vc("MELCHIOR", "buy", "PERが割高"),
        _vc("BALTHASAR", "buy", "RSI過熱"),
        _v("CASPER", "buy"),
    ]
    s = classify_split(verdicts)
    assert s.agree_count == 2  # 投票=業績・文脈
    assert "内在不安" in s.interpretation


def test_split_unanimous_buy_single_counter_stays_confident() -> None:
    # 反証が1審判だけなら内在不安にしない
    verdicts = [_vc("BALTHASAR", "buy", "RSI過熱"), _v("MELCHIOR", "buy"), _v("CASPER", "buy")]
    s = classify_split(verdicts)
    assert "内在不安" not in s.interpretation
    assert "確信度は相対的に高い" in s.interpretation


# --- B-4 碇が各審判の反証を束ねる（BALTHASARの事実も束ねる） -------------------
def test_commander_aggregates_counters() -> None:
    verdicts = [
        _vc("MELCHIOR", "buy", "営業赤字"),
        _vc("BALTHASAR", "buy", "RSI過熱"),
        _v("CASPER", "buy"),
    ]
    rec = command(verdicts, classify_split(verdicts), verify(verdicts))
    assert "内在反証＝" in rec.counter_argument
    assert "RSI過熱" in rec.counter_argument  # 株価の事実は反証として残す
    assert "営業赤字" in rec.counter_argument
    assert rec.magi_compliant is True


def test_commander_no_counter_suffix_when_none() -> None:
    verdicts = [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")]
    rec = command(verdicts, classify_split(verdicts), verify(verdicts))
    assert "内在反証＝" not in rec.counter_argument
