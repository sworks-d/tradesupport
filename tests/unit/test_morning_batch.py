"""朝バッチ（DAG オーケストレーション）の統合テスト（Task 1.5.2/1.5.3）。

全 MCP ツールをモックした host を注入し、6エージェントをエンドツーエンドで実走させる。
ネットワーク/キー不要。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.disclosure import DisclosureInput, DisclosureOutput
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, FundamentalsOutput
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataOutput
from trading_agent.mcp_tools.news import NewsInput, NewsOutput
from trading_agent.mcp_tools.screening import ScreeningTool
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsOutput
from trading_agent.models.batch import BatchState
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import CommanderRec, JudgeVerdict, SplitPattern, Verification
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.models.signals import Scenario, ScreeningResult, SellSignal
from trading_agent.models.topics import Topic
from trading_agent.models.universe import Universe
from trading_agent.orchestrator.morning_batch import run_morning_batch
from trading_agent.utils.time_utils import utcnow


class _MD(MCPTool[MarketDataInput]):
    name = "market_data"

    async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
        return MarketDataOutput(
            success=True, data={t: {"current_price": 130.0} for t in tool_input.tickers}
        )


class _FUND(MCPTool[FundamentalsInput]):
    name = "fundamentals"

    async def _execute(self, tool_input: FundamentalsInput) -> FundamentalsOutput:
        return FundamentalsOutput(
            success=True, data={"per": 15.0, "revenue_growth": 0.2, "roe": 0.16}
        )


class _TECH(MCPTool[TechnicalsInput]):
    name = "technicals"

    async def _execute(self, tool_input: TechnicalsInput) -> TechnicalsOutput:
        return TechnicalsOutput(success=True, data={"rsi": 40.0}, signals=["golden_cross"])


class _NEWS(MCPTool[NewsInput]):
    name = "news"

    async def _execute(self, tool_input: NewsInput) -> NewsOutput:
        return NewsOutput(
            success=True,
            articles=[
                {
                    "title": "FRB 利下げ観測",
                    "summary": "ハイテクに追い風",
                    "url": "http://n/1",
                    "source": "BB",
                    "published_at": "",
                }
            ],
        )


class _DISC(MCPTool[DisclosureInput]):
    name = "disclosure"

    async def _execute(self, tool_input: DisclosureInput) -> DisclosureOutput:
        return DisclosureOutput(success=True, disclosures=[])


class _LLM(MCPTool[LLMCallInput]):
    name = "llm_call"

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        payload = {
            "importance": "medium",
            "overall_health": 0.6,
            "overall_status": "weakening",
            "ai_confidence": 0.7,
            "scenarios": [{"type": "base", "target_price": 120, "return_pct": 0.2, "prob": 1.0}],
            "thesis_checklist": [],
            "reasons": [],
            "risks": [],
            "checklist_progress": [],
        }
        return LLMCallOutput(success=True, response=json.dumps(payload))


def _mock_host(engine) -> MCPHost:
    host = MCPHost()
    host.register(_MD())
    host.register(_FUND())
    host.register(_TECH())
    host.register(_NEWS())
    host.register(_DISC())
    host.register(ScreeningTool(engine))  # 実採点
    host.register(_LLM())
    return host


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "batch.sqlite")
    create_all(eng)
    with Session(eng) as s:
        for ticker in ("AAPL", "NVDA"):
            s.add(
                Universe(
                    ticker=ticker,
                    name=ticker,
                    market="US",
                    sector="Tech",
                    market_cap=3e14,
                    market_cap_jpy=3e14,
                    avg_volume_30d=1e7,
                )
            )
        s.add(
            Portfolio(
                ticker="AAPL",
                buy_date=dt.date(2026, 3, 1),
                buy_price=100.0,
                qty=10,
                currency="USD",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.2,
                stop_loss_pct=-0.08,
                target_date=dt.date(2026, 6, 1),
                thesis="t",
                status="active",
            )
        )
        s.add(
            PortfolioSnapshot(
                date=dt.date(2026, 5, 22),
                total_assets_jpy=100000.0,
                cash_jpy=100000.0,
                us_stocks_value_jpy=0.0,
                jp_stocks_value_jpy=0.0,
                satellite_value_jpy=0.0,
                core_value_jpy=0.0,
                usd_jpy_rate=150.0,
                holding_count=1,
                daily_pnl_jpy=0.0,
            )
        )
        s.commit()
    return eng


class TestMorningBatch:
    async def test_runs_end_to_end(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        batch = await run_morning_batch(engine, host=_mock_host(engine))

        assert batch.status in {"success", "partial"}
        assert len(batch.node_status) == 12  # 全ノード実行（A-4で +materialize/+magi_verify）
        assert batch.node_status["screening"] == "success"
        assert batch.node_status["materialize_decisions"] == "success"
        assert batch.node_status["magi_verify"] == "success"

        with Session(engine) as s:
            assert len(list(s.exec(select(ScreeningResult)))) >= 2
            assert len([t for t in s.exec(select(Topic))]) >= 1
            assert len(list(s.exec(select(Scenario).where(col(Scenario.ticker) == "AAPL")))) == 1
            assert len(list(s.exec(select(SellSignal).where(col(SellSignal.is_active))))) >= 1

    async def test_magi_pipeline_persisted(self, tmp_path: Path) -> None:
        """A-4：MAGI が DAG を貫通し、decision＋検証4表が decision_id 付きで残る。"""
        engine = _engine(tmp_path)
        await run_morning_batch(engine, host=_mock_host(engine))
        with Session(engine) as s:
            decisions = list(s.exec(select(Decision)))
            assert decisions, "MAGI候補から decision が生成されていない"
            # 検証完了で verifying を脱している（awaiting）
            assert all(d.status == "awaiting" for d in decisions)
            assert all(d.verified_at is not None for d in decisions)
            assert all(d.gendo_stance for d in decisions)

            did = decisions[0].id
            verdicts = list(
                s.exec(select(JudgeVerdict).where(col(JudgeVerdict.decision_id) == did))
            )
            assert {v.judge for v in verdicts} == {"MELCHIOR", "BALTHASAR", "CASPER"}
            assert s.exec(select(SplitPattern).where(col(SplitPattern.decision_id) == did)).first()
            assert s.exec(select(Verification).where(col(Verification.decision_id) == did)).first()
            assert s.exec(select(CommanderRec).where(col(CommanderRec.decision_id) == did)).first()

    async def test_credibility_flows_when_financials_provided(self, tmp_path: Path) -> None:
        """run_morning_batch に financials_fetcher を渡すと信用性が MELCHIOR 反証に乗る（弾ON）。"""
        from trading_agent.screening.financials import Financials, PeriodFinancials

        def fin_stub(_ticker: str) -> Financials:
            # 倒産リスク域（Z risk）→ credibility warn → MELCHIOR反証
            cur = PeriodFinancials(
                period="2026", working_capital=-100.0, total_assets=1000.0,
                retained_earnings=50.0, ebit=10.0, total_liabilities=900.0, revenue=300.0,
            )
            return Financials(ticker="X", current=cur, prior=None, market_cap=100.0)

        engine = _engine(tmp_path)
        await run_morning_batch(
            engine, host=_mock_host(engine), financials_fetcher=fin_stub
        )
        with Session(engine) as s:
            mels = list(s.exec(select(JudgeVerdict).where(col(JudgeVerdict.judge) == "MELCHIOR")))
            assert mels, "MELCHIOR の判定が無い"
            assert any(m.counter_within_domain for m in mels)  # 信用性反証が乗る

    async def test_batch_state_persisted(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        await run_morning_batch(engine, host=_mock_host(engine))
        inv = f"morning_{utcnow().date().isoformat()}"
        with Session(engine) as s:
            row = s.get(BatchState, inv)
        assert row is not None
        assert row.batch_type == "morning"
        assert "買い推奨" in row.summary
