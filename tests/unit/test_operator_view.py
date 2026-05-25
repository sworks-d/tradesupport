"""オペレーター・ビュー（永続MAGI→GENDO推奨カード）の単体テスト。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trading_agent.db import create_all, get_engine
from trading_agent.magi.commander import CommanderResult
from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.integration import SplitResult
from trading_agent.magi.persist import JudgeBundle, magi_verify, materialize_decisions
from trading_agent.models.magi import JudgeVerdict
from trading_agent.portfolio.operator_view import operator_cards, render_card


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ov.sqlite")
    create_all(eng)
    return eng


def _verdict(judge: str, verdict: str) -> JudgeVerdict:
    return JudgeVerdict(
        ticker="7203", judge=judge, verdict=verdict, confidence="中", reason="r",
        source_refs=[], data_asof=datetime(2026, 5, 25),
    )


def _bundle(*, default_hold: bool = False, credibility: str = "ok") -> JudgeBundle:
    vs = [_verdict("MELCHIOR", "buy"), _verdict("BALTHASAR", "buy"), _verdict("CASPER", "buy")]
    split = SplitResult(agree_count=2, total=2, label="2/2 買い・一致", interpretation="一致")
    vr = VerificationResult(
        figures_checked=True, credibility_flag=credibility, time_ok=True,
        gendo_compliant=None, unverified_claims=[], default_hold=default_hold,
    )
    cmd = CommanderResult(
        recommendation="買い", counter_argument="反対するなら：…",
        magi_compliant=True, src_note="n",
    )
    return vs, split, vr, cmd


async def _persist_awaiting(
    engine, *, default_hold: bool = False, credibility: str = "ok"
) -> int:
    ids = materialize_decisions(engine, ["7203"])

    async def judge_fn(_t: str) -> JudgeBundle:
        return _bundle(default_hold=default_hold, credibility=credibility)

    await magi_verify(engine, ids, judge_fn)
    return ids[0]


class TestOperatorCards:
    async def test_unanimous_safe_is_core_add(self, engine) -> None:
        await _persist_awaiting(engine)
        cards = operator_cards(
            engine, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert len(cards) == 1
        oc = cards[0]
        assert oc.ticker == "7203"
        assert oc.card.action == "積み増し"
        assert oc.card.sleeve == "core"
        # ガードレールにサイズが具体化されている
        assert "¥" in oc.card.guardrail

    async def test_credibility_warn_is_avoid(self, engine) -> None:
        await _persist_awaiting(engine, default_hold=True, credibility="warn")
        cards = operator_cards(
            engine, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert cards[0].card.action == "見送り"

    async def test_render_card_has_essentials(self, engine) -> None:
        await _persist_awaiting(engine)
        cards = operator_cards(
            engine, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        txt = render_card(cards[0])
        assert "7203" in txt
        assert "GENDOの推奨" in txt
        assert "決めるのはあなた" in txt

    async def test_no_cards_when_not_awaiting(self, engine) -> None:
        # materialize だけ（verifying のまま）→ awaiting でない → カード無し
        materialize_decisions(engine, ["7203"])
        cards = operator_cards(
            engine, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert cards == []
