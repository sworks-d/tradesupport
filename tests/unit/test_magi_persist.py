"""A-4 MAGI永続化（magi/persist.py）の単体テスト。judge_fn を注入しネット非依存で検証。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.magi.commander import CommanderResult
from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.integration import SplitResult
from trading_agent.magi.persist import (
    JudgeBundle,
    derive_gendo_stance,
    magi_verify,
    materialize_decisions,
    pending_decision_ids,
)
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import CommanderRec, JudgeVerdict, SplitPattern, Verification


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "persist.sqlite")
    create_all(eng)
    return eng


def _verdict(judge: str, verdict: str) -> JudgeVerdict:
    return JudgeVerdict(
        ticker="NVDA",
        judge=judge,
        verdict=verdict,
        confidence="中",
        reason="r",
        source_refs=[],
        data_asof=datetime(2026, 5, 23),
    )


def _bundle(
    *, default_hold: bool = False, verdicts: list[JudgeVerdict] | None = None
) -> JudgeBundle:
    vs = verdicts or [
        _verdict("MELCHIOR", "buy"),
        _verdict("BALTHASAR", "buy"),
        _verdict("CASPER", "buy"),
    ]
    split = SplitResult(agree_count=3, total=3, label="3/3 買い・一致", interpretation="一致")
    vr = VerificationResult(
        figures_checked=True,
        credibility_flag="ok",
        time_ok=True,
        gendo_compliant=None,
        unverified_claims=[],
        default_hold=default_hold,
    )
    cmd = CommanderResult(
        recommendation="買い", counter_argument="反対するなら：…", magi_compliant=True, src_note="n"
    )
    return vs, split, vr, cmd


class TestMaterialize:
    def test_creates_verifying_decisions(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA", "AAPL"])
        assert len(ids) == 2
        with Session(engine) as s:
            rows = list(s.exec(select(Decision)))
            assert {r.ticker for r in rows} == {"NVDA", "AAPL"}
            assert all(r.status == "verifying" for r in rows)

    def test_idempotent_same_day(self, engine) -> None:
        ids1 = materialize_decisions(engine, ["NVDA"])
        ids2 = materialize_decisions(engine, ["NVDA"])
        assert ids1 == ids2  # 同日同銘柄は再利用
        with Session(engine) as s:
            assert len(list(s.exec(select(Decision)))) == 1

    def test_pending_ids(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA", "AAPL"])
        assert set(pending_decision_ids(engine)) == set(ids)


class TestGendoStance:
    def test_all_buy_is_oshi(self) -> None:
        vs = [_verdict("MELCHIOR", "buy"), _verdict("BALTHASAR", "buy"), _verdict("CASPER", "buy")]
        assert derive_gendo_stance(vs, default_hold=False) == "推し"

    def test_hold_downgrades_oshi(self) -> None:
        vs = [_verdict("MELCHIOR", "buy"), _verdict("BALTHASAR", "buy"), _verdict("CASPER", "buy")]
        assert derive_gendo_stance(vs, default_hold=True) == "要検討"

    def test_split_is_review(self) -> None:
        vs = [
            _verdict("MELCHIOR", "buy"),
            _verdict("BALTHASAR", "hold"),
            _verdict("CASPER", "warn"),
        ]
        assert derive_gendo_stance(vs, default_hold=True) == "要検討"

    def test_all_na_is_watch(self) -> None:
        vs = [_verdict("MELCHIOR", "na"), _verdict("BALTHASAR", "na"), _verdict("CASPER", "na")]
        assert derive_gendo_stance(vs, default_hold=True) == "静観"


class TestMagiVerify:
    async def test_persists_four_tables_and_advances_status(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA"])

        async def judge_fn(_ticker: str) -> JudgeBundle:
            return _bundle(default_hold=False)

        counts = await magi_verify(engine, ids, judge_fn)
        assert counts == {"verified": 1, "held": 0, "failed": 0}
        did = ids[0]
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.status == "awaiting"
            assert d.verified_at is not None
            assert d.gendo_stance == "推し"
            jv = list(s.exec(select(JudgeVerdict).where(col(JudgeVerdict.decision_id) == did)))
            assert {v.judge for v in jv} == {"MELCHIOR", "BALTHASAR", "CASPER"}
            assert all(v.decision_id == did for v in jv)
            assert s.exec(select(SplitPattern).where(col(SplitPattern.decision_id) == did)).first()
            assert s.exec(select(Verification).where(col(Verification.decision_id) == did)).first()
            assert s.exec(select(CommanderRec).where(col(CommanderRec.decision_id) == did)).first()

    async def test_default_hold_counts_as_held(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA"])

        async def judge_fn(_ticker: str) -> JudgeBundle:
            return _bundle(default_hold=True)

        counts = await magi_verify(engine, ids, judge_fn)
        assert counts["held"] == 1

    async def test_reverify_is_idempotent(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA"])

        async def judge_fn(_ticker: str) -> JudgeBundle:
            return _bundle()

        await magi_verify(engine, ids, judge_fn)
        await magi_verify(engine, ids, judge_fn)  # 2回目：作り直し（重複しない）
        did = ids[0]
        with Session(engine) as s:
            jv = list(s.exec(select(JudgeVerdict).where(col(JudgeVerdict.decision_id) == did)))
            assert len(jv) == 3  # 6 にならない

    async def test_failure_is_isolated(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA"])

        async def boom(_ticker: str) -> JudgeBundle:
            raise RuntimeError("judge down")

        counts = await magi_verify(engine, ids, boom)
        assert counts == {"verified": 0, "held": 0, "failed": 1}
        with Session(engine) as s:
            d = s.get(Decision, ids[0])
            assert d.status == "verifying"  # 失敗時は進めない
