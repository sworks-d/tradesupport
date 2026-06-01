"""sell-recommender の単体テスト（出口設計 B' = 2026-05-24 改訂）。

利確でサイズを刻むのは廃止。サイズを減らす出口は固定stopと保有期限(time-exit)のみ。
日付依存を避けるため、buy_date/target_date は utcnow 基準の相対日付で組む。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.sell_recommender import (
    SellRecommenderAgent,
    SellRecommenderInput,
    determine_sell_recommendation,
    discipline_reasons,
    loss_magnitude,
    status_from_health,
    stop_loss_score,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost, MCPTool
from trading_agent.mcp_tools.llm_call import LLMCallInput, LLMCallOutput
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataOutput
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsOutput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import Scenario, SellSignal
from trading_agent.utils.time_utils import utcnow


class TestComponents:
    def test_loss_magnitude(self) -> None:
        # v2.1 TASK-SZ4: stop_loss_pct は正値で統一（旧 -0.08 → 新 0.08）
        assert loss_magnitude(92, 100, 0.08) == pytest.approx(1.0)
        assert loss_magnitude(96, 100, 0.08) == pytest.approx(0.5)
        # 含み益や損失ゼロは 0.0
        assert loss_magnitude(105, 100, 0.08) == pytest.approx(0.0)
        # 後方互換性: 負値が渡された時は 0 を返す（古い呼び出し側からの安全装置）
        assert loss_magnitude(92, 100, -0.08) == pytest.approx(0.0)

    def test_stop_score(self) -> None:
        assert stop_loss_score(0.5, 0.5, 0.5, 0.5) == pytest.approx(50.0)

    def test_status(self) -> None:
        assert status_from_health(0.8) == "intact"
        assert status_from_health(0.5) == "weakening"
        assert status_from_health(0.3) == "broken"

    def test_recommendation_full_exit(self) -> None:
        # B'：利確で刻まない。stop_loss=全量成行、time_exit=全量指値。半量は存在しない。
        sl = determine_sell_recommendation("stop_loss", 10, 100)
        assert sl["type"] == "market"
        assert sl["qty"] == 10
        assert sl["qty_label"] == "全量（成行）"
        te = determine_sell_recommendation("time_exit", 10, 100)
        assert te["qty"] == 10
        assert te["qty_label"] == "全量"

    def test_discipline_reasons(self) -> None:
        rs = discipline_reasons(100, -0.08, 90)
        assert len(rs) == 2
        assert all(r["priority"] == "規律" for r in rs)


class _MD(MCPTool[MarketDataInput]):
    name = "market_data"

    def __init__(self, price: float) -> None:
        self._price = price

    async def _execute(self, tool_input: MarketDataInput) -> MarketDataOutput:
        return MarketDataOutput(
            success=True, data={t: {"current_price": self._price} for t in tool_input.tickers}
        )


class _TECH(MCPTool[TechnicalsInput]):
    name = "technicals"

    async def _execute(self, tool_input: TechnicalsInput) -> TechnicalsOutput:
        return TechnicalsOutput(success=True, data={"rsi": 50.0}, signals=[])


class _LLM(MCPTool[LLMCallInput]):
    name = "llm_call"

    def __init__(self, health: float) -> None:
        self._health = health

    async def _execute(self, tool_input: LLMCallInput) -> LLMCallOutput:
        return LLMCallOutput(
            success=True,
            response=json.dumps({"overall_health": self._health, "ai_confidence": 0.5}),
        )


def _engine(tmp_path: Path, *, buy_offset_days: int, target_offset_days: int):
    """utcnow 基準の相対日付で 1 銘柄の保有を作る（日付依存を避ける）。"""
    today = utcnow().date()
    eng = get_engine(tmp_path / "sell.sqlite")
    create_all(eng)
    with Session(eng) as s:
        s.add(
            Portfolio(
                ticker="AAPL",
                buy_date=today - dt.timedelta(days=buy_offset_days),
                buy_price=100.0,
                qty=10,
                currency="USD",
                strategy_category="中期",
                target_period_days=90,
                target_pct=0.2,
                stop_loss_pct=0.08,  # v2.1 TASK-SZ4: 正値で統一
                target_date=today + dt.timedelta(days=target_offset_days),
                thesis="t",
                status="active",
            )
        )
        s.commit()
    return eng


def _ctx(
    tmp_path: Path,
    price: float,
    health: float,
    *,
    buy_offset_days: int = 30,
    target_offset_days: int = 90,
):
    engine = _engine(
        tmp_path, buy_offset_days=buy_offset_days, target_offset_days=target_offset_days
    )
    host = MCPHost()
    host.register(_MD(price))
    host.register(_TECH())
    host.register(_LLM(health))
    return AgentContext(host=host, engine=engine, invocation_id="inv")


class TestAgent:
    async def test_winner_held_not_trimmed(self, tmp_path: Path) -> None:
        # 含み益・固定stop未到達・期限前 → 利確で刻まない＝売りシグナル無し（勝ち放任）
        ctx = _ctx(tmp_path, price=130.0, health=0.6)
        out = await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        assert out.success is True
        assert out.sell_signals == []
        with Session(ctx.engine) as s:
            assert s.exec(select(SellSignal).where(col(SellSignal.is_active))).all() == []

    async def test_time_exit_at_target_date(self, tmp_path: Path) -> None:
        # 保有期限到達（target_date 過去）→ 全量手仕舞い（time_exit）
        ctx = _ctx(tmp_path, price=130.0, health=0.6, target_offset_days=-1)
        await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        with Session(ctx.engine) as s:
            sig = s.exec(select(SellSignal).where(col(SellSignal.is_active))).one()
        assert sig.signal_type == "time_exit"
        assert sig.recommended_action["qty_label"] == "全量"
        assert sig.recommended_action["qty"] == 10

    async def test_stop_loss_discipline(self, tmp_path: Path) -> None:
        # 固定stop到達（-10% <= -8%）→ 全量・成行・規律メッセージ
        ctx = _ctx(tmp_path, price=90.0, health=0.05)
        await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        with Session(ctx.engine) as s:
            sig = s.exec(select(SellSignal).where(col(SellSignal.is_active))).one()
        assert sig.signal_type == "stop_loss"
        assert sig.score >= 70
        assert sig.recommended_action["type"] == "market"
        assert any(r.get("priority") == "規律" for r in sig.reasons)

    async def test_small_loss_held(self, tmp_path: Path) -> None:
        # 含み損だが固定stop未到達（-3% > -8%）・期限前 → 売らずに保有（早すぎる損切りをしない）
        ctx = _ctx(tmp_path, price=97.0, health=0.5)
        out = await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        assert out.sell_signals == []

    async def test_scenario_saved(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, price=130.0, health=0.6)
        await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        with Session(ctx.engine) as s:
            scn = s.exec(select(Scenario).where(col(Scenario.ticker) == "AAPL")).one()
        assert scn.scenario_status == "weakening"  # health 0.6

    async def test_recently_bought_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, price=130.0, health=0.6, buy_offset_days=1)
        out = await SellRecommenderAgent(ctx).execute(SellRecommenderInput(invocation_id="inv"))
        assert out.sell_signals == []
        assert out.scenario_updates == []
