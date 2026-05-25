"""GENDO 推奨（初心者コーチ・守り主導）の単体テスト。決定論・新事実を加えない。"""

from __future__ import annotations

from datetime import datetime

from trading_agent.magi import classify_split, command, verify
from trading_agent.magi.gendo import ACTIONS, gendo_recommend
from trading_agent.models.magi import JudgeVerdict


def _v(judge: str, verdict: str) -> JudgeVerdict:
    return JudgeVerdict(
        ticker="X", judge=judge, verdict=verdict, confidence="中", reason="r",
        source_refs=[{"source": "yfinance", "ref": "X"}], data_asof=datetime(2026, 5, 25),
    )


def _card(verdicts, *, credibility="ok", offense=False, exit_=None):
    s = classify_split(verdicts)
    vr = verify(verdicts, credibility_flag=credibility)
    cmd = command(verdicts, s, vr)
    return gendo_recommend(
        verdicts, s, vr, cmd, credibility_flag=credibility,
        offense_strong=offense, holding_exit=exit_,
    )


def test_action_vocab_is_bounded() -> None:
    c = _card([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")])
    assert c.action in ACTIONS


def test_credibility_red_is_avoid() -> None:
    # 守りが赤 → 攻めが揃っていても見送り（最優先）
    c = _card(
        [_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")],
        credibility="warn",
    )
    assert c.action == "見送り"
    assert c.sleeve == "—"
    assert "赤" in c.reason


def test_voting_unanimous_safe_is_core_add() -> None:
    # 業績・文脈が揃い守り青＋照合済 → コアに積み増し
    c = _card([_v("MELCHIOR", "buy"), _v("BALTHASAR", "warn"), _v("CASPER", "buy")])
    assert c.action == "積み増し"
    assert c.sleeve == "core"


def test_offense_strong_is_satellite_small() -> None:
    # 合意は弱いが守り青＋攻め強 → サテライトで小さく試す（昇格前は枠0）
    c = _card([_v("MELCHIOR", "warn"), _v("BALTHASAR", "buy"), _v("CASPER", "hold")], offense=True)
    assert c.action == "小さく試す"
    assert c.sleeve == "satellite"
    assert "サテライト" in c.guardrail and "昇格前" in c.guardrail


def test_split_is_watch() -> None:
    c = _card([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "warn")])
    assert c.action == "静観"


def test_holding_exit_is_retreat() -> None:
    c = _card([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")], exit_="stop")
    assert c.action == "撤退"


def test_always_has_counter_and_learn_and_compliant() -> None:
    c = _card([_v("MELCHIOR", "buy"), _v("BALTHASAR", "buy"), _v("CASPER", "buy")])
    assert c.counter.startswith("反対するなら：")  # 必ず反対論拠
    assert c.learn_note  # 学べる一言
    assert c.offense_confidence.startswith("○")  # 攻めは灰色
    assert c.magi_compliant is True
