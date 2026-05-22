"""portfolio-builder エージェント（AGENT_SPECS.md §4）。

初回構築（initial）と毎朝の配分チェック（review）。Phase 1 はルールベースで決定論的に実装
（配分の戦略的 LLM 判断は後日。§4.5 スコープ：review/initial を実装、rebalance は Phase 2-）。

Core-Satellite：strategy_category に「中期」を含めばコア、それ以外（長期/短期）はサテライト
（STEP_A：コア=中期 80% / サテライト=長期・短期 20%）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import BuySignal
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_SECTOR_CONCENTRATION_LIMIT = 0.5
_RATIO_TOLERANCE = 0.15
_MAX_CORE = 5
_MAX_SATELLITE = 2


def is_core(strategy_category: str) -> bool:
    return "中期" in strategy_category


class PortfolioBuilderInput(AgentInput):
    mode: str = "review"  # "initial" / "review" / "rebalance"
    available_cash_jpy: int = 0
    candidates: list[str] = Field(default_factory=list)
    target_allocation: dict[str, float] = Field(
        default_factory=lambda: {"core": 0.8, "satellite": 0.2}
    )


class PortfolioBuilderOutput(AgentOutput):
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    rebalance_actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PortfolioBuilderAgent(Agent[PortfolioBuilderInput]):
    """ポートフォリオ構築・配分チェックエージェント。"""

    name = "portfolio_builder"
    description = "初回構築（initial）と毎朝の配分チェック（review）を行う。"
    required_tools = ["market_data", "llm_call"]
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: PortfolioBuilderInput) -> AgentOutput:
        if agent_input.mode == "initial":
            return self._initial(agent_input)
        if agent_input.mode == "review":
            return self._review(agent_input)
        # rebalance は Phase 2-
        return PortfolioBuilderOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary="rebalance モードは Phase 2 以降",
        )

    def _initial(self, agent_input: PortfolioBuilderInput) -> PortfolioBuilderOutput:
        signals = self._load_buy_signals(self._ctx.engine, agent_input.candidates)
        core = [s for s in signals if is_core(s.strategy_category)][:_MAX_CORE]
        satellite = [s for s in signals if not is_core(s.strategy_category)][:_MAX_SATELLITE]

        cash = max(agent_input.available_cash_jpy, 0)
        core_budget = cash * agent_input.target_allocation.get("core", 0.8)
        sat_budget = cash * agent_input.target_allocation.get("satellite", 0.2)
        per_core = core_budget / len(core) if core else 0.0
        per_sat = sat_budget / len(satellite) if satellite else 0.0

        recs: list[dict[str, Any]] = []
        for sig in core:
            recs.append(_rec(sig.ticker, per_core, cash, "コア（中期）"))
        for sig in satellite:
            recs.append(_rec(sig.ticker, per_sat, cash, "サテライト"))

        return PortfolioBuilderOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=f"初回案 {len(recs)}銘柄（コア{len(core)}/サテ{len(satellite)}）",
            recommendations=recs,
        )

    def _review(self, agent_input: PortfolioBuilderInput) -> PortfolioBuilderOutput:
        holdings, sectors = self._load_holdings(self._ctx.engine)
        warnings: list[str] = []

        total = sum(h.buy_price * h.qty for h in holdings)
        if total > 0:
            core_val = sum(h.buy_price * h.qty for h in holdings if is_core(h.strategy_category))
            core_ratio = core_val / total
            target_core = agent_input.target_allocation.get("core", 0.8)
            if core_ratio < target_core - _RATIO_TOLERANCE:
                warnings.append(f"コア比率が低い（{core_ratio:.0%} < 目標 {target_core:.0%}）")
            elif core_ratio > target_core + _RATIO_TOLERANCE:
                warnings.append(f"サテライト比率が高い（コア {core_ratio:.0%}）")

            # セクター集中
            sector_val: dict[str, float] = {}
            for h in holdings:
                sec = sectors.get(h.ticker, "unknown")
                sector_val[sec] = sector_val.get(sec, 0.0) + h.buy_price * h.qty
            for sec, val in sector_val.items():
                if val / total > _SECTOR_CONCENTRATION_LIMIT:
                    warnings.append(f"セクター集中：{sec} が {val / total:.0%}")

        return PortfolioBuilderOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=f"配分チェック完了（保有 {len(holdings)} 銘柄、警告 {len(warnings)} 件）",
            warnings=warnings,
        )

    def _load_buy_signals(self, engine: Engine, candidates: list[str]) -> list[BuySignal]:
        with Session(engine) as session:
            signals = list(
                session.exec(
                    select(BuySignal)
                    .where(col(BuySignal.is_active))
                    .order_by(col(BuySignal.score).desc())
                )
            )
        if candidates:
            signals = [s for s in signals if s.ticker in candidates]
        return signals

    def _load_holdings(self, engine: Engine) -> tuple[list[Portfolio], dict[str, str]]:
        with Session(engine) as session:
            holdings = list(session.exec(select(Portfolio).where(Portfolio.status == "active")))
            sectors = {u.ticker: u.sector for u in session.exec(select(Universe))}
        return holdings, sectors


def _rec(ticker: str, amount: float, cash: float, label: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "action": "buy_new",
        "rationale": f"{label}枠の新規候補",
        "target_allocation_pct": round(amount / cash, 4) if cash else 0.0,
        "target_amount_jpy": int(amount),
    }
