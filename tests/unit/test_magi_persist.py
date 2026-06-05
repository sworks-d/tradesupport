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

    async def test_earnings_sink_writes_signal_tags_record_only(self, engine) -> None:
        """A prime: earnings_sink の earnings_accel が Decision に record-only マージ + 証拠記録。"""
        ids = materialize_decisions(engine, ["NVDA"])
        sink: dict = {}

        async def judge_fn(ticker: str) -> JudgeBundle:
            # judge が J-Quants fin 再利用で earnings タグを sink に積む挙動を模す
            sink[ticker] = (["earnings_accel"], {"earnings_accel": {"source": "jquants", "asof": "2026"}})
            return _bundle(default_hold=False)

        await magi_verify(engine, ids, judge_fn, earnings_sink=sink)
        with Session(engine) as s:
            d = s.get(Decision, ids[0])
            assert d.entry_signal_tags == ["earnings_accel"]
            assert d.signal_tag_sources["earnings_accel"]["source"] == "jquants"
            # record-only: 売買ステータスは通常の verify 通り（タグは売買を変えない）
            assert d.status == "awaiting"

    async def test_counter_within_domain_persisted(self, engine) -> None:
        """B-1：審判の反証（counter_within_domain）が decision_id 付きで永続化される。"""
        ids = materialize_decisions(engine, ["NVDA"])
        bal = _verdict("BALTHASAR", "buy")
        bal.counter_within_domain = [{"claim": "RSI過熱", "source_refs": [{"source": "computed"}]}]

        async def judge_fn(_ticker: str) -> JudgeBundle:
            return _bundle(verdicts=[_verdict("MELCHIOR", "buy"), bal, _verdict("CASPER", "buy")])

        await magi_verify(engine, ids, judge_fn)
        with Session(engine) as s:
            row = s.exec(
                select(JudgeVerdict)
                .where(col(JudgeVerdict.decision_id) == ids[0])
                .where(col(JudgeVerdict.judge) == "BALTHASAR")
            ).one()
            assert row.counter_within_domain
            assert row.counter_within_domain[0]["claim"] == "RSI過熱"

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

    async def test_credibility_wiring_warns_and_rebuts(self, engine) -> None:
        """S5b/S6：financials_fetcher を渡すと credibility_flag=warn＋MELCHIOR反証が乗る。"""
        from trading_agent.magi.persist import make_live_judge_fn
        from trading_agent.mcp_tools.fundamentals import FundamentalsOutput
        from trading_agent.mcp_tools.news import NewsOutput
        from trading_agent.mcp_tools.technicals import TechnicalsOutput
        from trading_agent.screening.financials import Financials, PeriodFinancials

        async def call_tool(name: str, _inp):
            if name == "fundamentals":
                return FundamentalsOutput(
                    success=True, data={"revenue_growth": 0.2, "operating_margin": 0.2}
                )
            if name == "technicals":
                return TechnicalsOutput(success=True, data={"rsi": 50.0}, signals=[])
            return NewsOutput(success=True, articles=[])

        def fin_fetcher(_ticker: str) -> Financials:
            # 倒産リスク域（Z risk）の財務 → credibility warn
            cur = PeriodFinancials(
                period="2026", working_capital=-100.0, total_assets=1000.0,
                retained_earnings=50.0, ebit=10.0, total_liabilities=900.0, revenue=300.0,
            )
            return Financials(ticker="X", current=cur, prior=None, market_cap=100.0)

        # v2.2 TASK-Z7: sector 必須化のため sector_lookup を渡す
        judge = make_live_judge_fn(
            call_tool,
            financials_fetcher=fin_fetcher,
            sector_lookup=lambda _t: "Technology",
        )
        verdicts, _split, vr, _cmd = await judge("X")
        assert vr.credibility_flag == "warn"
        assert vr.default_hold is True  # 信用性warnは保留へ寄せる
        mel = next(v for v in verdicts if v.judge == "MELCHIOR")
        assert any("倒産リスク" in c["claim"] for c in mel.counter_within_domain)

    async def test_disclosure_red_flag_feeds_credibility(self, engine) -> None:
        """S4b：開示のGC注記が credibility warn＋MELCHIOR反証に乗る（call_tool経由）。"""
        from trading_agent.magi.persist import make_live_judge_fn
        from trading_agent.mcp_tools.disclosure import DisclosureOutput
        from trading_agent.mcp_tools.fundamentals import FundamentalsOutput
        from trading_agent.mcp_tools.news import NewsOutput
        from trading_agent.mcp_tools.technicals import TechnicalsOutput
        from trading_agent.screening.financials import Financials, PeriodFinancials

        async def call_tool(name: str, _inp):
            if name == "fundamentals":
                return FundamentalsOutput(
                    success=True, data={"revenue_growth": 0.2, "operating_margin": 0.2}
                )
            if name == "technicals":
                return TechnicalsOutput(success=True, data={"rsi": 50.0}, signals=[])
            if name == "disclosure":
                disc = [{"title": "継続企業の前提に関する注記", "description": ""}]
                return DisclosureOutput(success=True, disclosures=disc)
            return NewsOutput(success=True, articles=[])

        def fin_fetcher(_ticker: str) -> Financials:
            # 財務は健全（M/F/Z は warn でない）→ warn は開示由来のみ
            cur = PeriodFinancials(
                period="2026", working_capital=500.0, total_assets=1000.0,
                retained_earnings=600.0, ebit=300.0, total_liabilities=400.0, revenue=1000.0,
                net_income=200.0, operating_cashflow=205.0, current_assets=500.0,
                current_liabilities=300.0, receivables=120.0, ppe=300.0, gross_profit=600.0,
                shares=10.0, long_term_debt=100.0,
            )
            return Financials(ticker="X", current=cur, prior=None, market_cap=3000.0)

        judge = make_live_judge_fn(call_tool, financials_fetcher=fin_fetcher)
        verdicts, _s, vr, _c = await judge("X")
        assert vr.credibility_flag == "warn"  # 開示GC注記で warn
        mel = next(v for v in verdicts if v.judge == "MELCHIOR")
        assert any("開示レッドフラグ" in c["claim"] for c in mel.counter_within_domain)

    async def test_failure_is_isolated(self, engine) -> None:
        ids = materialize_decisions(engine, ["NVDA"])

        async def boom(_ticker: str) -> JudgeBundle:
            raise RuntimeError("judge down")

        counts = await magi_verify(engine, ids, boom)
        assert counts == {"verified": 0, "held": 0, "failed": 1}
        with Session(engine) as s:
            d = s.get(Decision, ids[0])
            assert d.status == "verifying"  # 失敗時は進めない
