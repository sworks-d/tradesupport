"""manual-input-analyst エージェント（AGENT_SPECS.md §5）。

ユーザー投入テキスト（X発言・記事・コメント）を LLM で即時解釈し、保有・買い候補への影響を判定。
manual_inputs に保存し、トピックス化候補を生成する。

- Phase 1 はテキストのみ（URL 取得は Phase 2-）。
- LLM 未登録/失敗時は中立で縮退（result_summary=入力先頭、direction=neutral）。
- テキストが短すぎる（10文字未満）場合は処理せずエラー（§5.6）。
"""

from __future__ import annotations

from trading_agent.llm.json_extract import extract_json
from typing import Any

from pydantic import Field
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import BuySignal
from trading_agent.models.topics import ManualInput
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_MIN_TEXT_LEN = 10


class ManualInputAnalystInput(AgentInput):
    text: str
    url: str | None = None
    user_hint: dict[str, Any] | None = None


class ManualInputAnalystOutput(AgentOutput):
    result_summary: str = ""
    affected_tickers: list[str] = Field(default_factory=list)
    impact_direction: str = "neutral"
    impact_magnitude: str = "small"
    recommended_actions: list[str] = Field(default_factory=list)
    suggested_topic: dict[str, Any] | None = None


class ManualInputAnalystAgent(Agent[ManualInputAnalystInput]):
    """手動投入テキストの即時解釈エージェント。"""

    name = "manual_input_analyst"
    description = "投入テキストを解釈し、保有・候補への影響と推奨アクションを返す。"
    required_tools = ["llm_call"]
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: ManualInputAnalystInput) -> AgentOutput:
        text = agent_input.text.strip()
        if len(text) < _MIN_TEXT_LEN:
            return AgentOutput(
                success=False,
                invocation_id=agent_input.invocation_id,
                error="text too short",
                summary="テキストが短すぎます（10文字以上が必要）",
            )

        portfolio, candidates = self._load_universe_sets(self._ctx)
        parsed = await self._interpret(text, portfolio, candidates)

        known = portfolio | candidates
        affected = [t for t in parsed.get("affected_tickers", []) if not known or t in known]
        direction = str(parsed.get("overall_direction", "neutral"))
        magnitude = str(parsed.get("overall_magnitude", "small"))
        summary = str(parsed.get("result_summary") or text[:120])
        actions = [str(a) for a in parsed.get("recommended_actions", [])]

        if not affected:
            summary = summary or "投資判断に影響しないと判定"

        suggested_topic = {
            "headline": text[:80],
            "summary": summary,
            "affected_tickers": affected,
            "importance": "medium" if affected else "low",
        }

        if not agent_input.dry_run:
            self._save(agent_input, summary, affected, direction, magnitude, parsed)

        return ManualInputAnalystOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=summary,
            result_summary=summary,
            affected_tickers=affected,
            impact_direction=direction,
            impact_magnitude=magnitude,
            recommended_actions=actions,
            suggested_topic=suggested_topic,
        )

    def _load_universe_sets(self, ctx: AgentContext) -> tuple[set[str], set[str]]:
        with Session(ctx.engine) as session:
            portfolio = {
                p.ticker
                for p in session.exec(select(Portfolio).where(Portfolio.status == "active"))
            }
            candidates = {
                b.ticker for b in session.exec(select(BuySignal).where(col(BuySignal.is_active)))
            }
        return portfolio, candidates

    async def _interpret(
        self, text: str, portfolio: set[str], candidates: set[str]
    ) -> dict[str, Any]:
        prompt = (
            "次の投入情報がユーザーの保有・買い候補にどう影響するか JSON で返してください。\n"
            f"保有: {sorted(portfolio)}\n買い候補: {sorted(candidates)}\n投入: {text}\n"
            "キー: result_summary, affected_tickers[], overall_direction"
            "(positive/negative/neutral/mixed), overall_magnitude(large/medium/small), "
            "recommended_actions[], confidence(0-1)。"
        )
        try:
            out = await self._ctx.call_tool(
                "llm_call",
                LLMCallInput(
                    prompt=prompt,
                    purpose="manual_input_analysis",
                    routing_hint="hot",
                    agent=self.name,
                    invocation_id=self._ctx.invocation_id,
                ),
            )
            if out.success:
                return extract_json(getattr(out, "response", None))
        except Exception as exc:
            self._log.warning("manual_input_llm_failed", error=str(exc))
        return {}

    def _save(
        self,
        agent_input: ManualInputAnalystInput,
        summary: str,
        affected: list[str],
        direction: str,
        magnitude: str,
        parsed: dict[str, Any],
    ) -> None:
        with Session(self._ctx.engine) as session:
            session.add(
                ManualInput(
                    input_text=agent_input.text,
                    input_url=agent_input.url,
                    input_type="url" if agent_input.url else "text",
                    analyzed_at=utcnow(),
                    result_summary=summary,
                    affected_tickers=affected,
                    impact_direction=direction,
                    impact_magnitude=magnitude,
                    recommended_action="; ".join(
                        str(a) for a in parsed.get("recommended_actions", [])
                    ),
                    llm_model=str(parsed.get("_model", "n/a")),
                    llm_cost_jpy=0.0,
                )
            )
            session.commit()
