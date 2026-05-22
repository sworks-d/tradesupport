"""朝バッチのオーケストレーション（ORCHESTRATION.md §2.4 / §9）。

6エージェントを DAG に束ねて実行し、結果を batch_states に記録する。
エージェント間のデータ受け渡しは DB 経由（§A-4）。ノード失敗は捕捉して継続（§A-3）。

実行順（§2.4）:
    pre_check → topics_collector / universe_refresh → screening → market_analyst
    → sell_recommender → portfolio_builder → link_topics → summary → notify

universe_refresh / link_topics / notify は Phase 1 では軽量（no-op / ログ）。
"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import execute_agent
from trading_agent.agents.context import AgentContext
from trading_agent.agents.market_analyst import MarketAnalystAgent, MarketAnalystInput
from trading_agent.agents.portfolio_builder import PortfolioBuilderAgent, PortfolioBuilderInput
from trading_agent.agents.screening_agent import ScreeningAgent, ScreeningAgentInput
from trading_agent.agents.sell_recommender import SellRecommenderAgent, SellRecommenderInput
from trading_agent.agents.topics_collector import TopicsCollectorAgent, TopicsCollectorInput
from trading_agent.mcp_tools.base import MCPHost
from trading_agent.mcp_tools.disclosure import DisclosureTool
from trading_agent.mcp_tools.fundamentals import FundamentalsTool
from trading_agent.mcp_tools.llm_call import LLMCallTool
from trading_agent.mcp_tools.market_data import MarketDataTool
from trading_agent.mcp_tools.news import NewsTool
from trading_agent.mcp_tools.screening import ScreeningTool
from trading_agent.mcp_tools.technicals import TechnicalsTool
from trading_agent.models.batch import BatchState
from trading_agent.models.signals import BuySignal, ScreeningResult, SellSignal
from trading_agent.models.topics import Topic
from trading_agent.orchestrator.dag import DAGExecutor, DAGNode, overall_status
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("orchestrator")


def build_host(engine: Engine) -> MCPHost:
    """全 MCP ツールを登録した MCPHost を構築する（実行用）。"""
    from trading_agent.config import get_settings
    from trading_agent.llm.anthropic_client import AnthropicClient
    from trading_agent.llm.ollama_client import OllamaClient

    settings = get_settings()
    host = MCPHost()
    host.register(MarketDataTool(engine))
    host.register(FundamentalsTool())
    host.register(NewsTool())
    host.register(DisclosureTool())
    host.register(TechnicalsTool())
    host.register(ScreeningTool(engine))
    host.register(
        LLMCallTool(
            engine,
            anthropic_client=AnthropicClient(settings.anthropic_api_key),
            ollama_client=OllamaClient(settings.ollama_host, settings.ollama_model),
        )
    )
    return host


def _candidate_tickers(engine: Engine, limit: int = 10) -> list[str]:
    """直近スクリーニングの合格候補（composite 上位）を返す。"""
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(ScreeningResult)
                .where(col(ScreeningResult.screening_passed))
                .order_by(col(ScreeningResult.composite_score).desc())
            )
        )
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        if r.ticker not in seen:
            seen.add(r.ticker)
            out.append(r.ticker)
        if len(out) >= limit:
            break
    return out


def _summary(engine: Engine) -> str:
    today = utcnow().date()
    with Session(engine) as session:
        buys = len(list(session.exec(select(BuySignal).where(col(BuySignal.is_active)))))
        sells = len(list(session.exec(select(SellSignal).where(col(SellSignal.is_active)))))
        topics = len([t for t in session.exec(select(Topic)) if t.collected_at.date() == today])
    return f"買い推奨 {buys} / 売り推奨 {sells} / 本日トピックス {topics}"


async def run_morning_batch(
    engine: Engine, *, host: MCPHost | None = None, dry_run: bool = False
) -> BatchState:
    """朝バッチを DAG で実行し、BatchState を保存して返す。"""
    invocation_id = f"morning_{utcnow().date().isoformat()}"
    resolved_host = host if host is not None else build_host(engine)
    ctx = AgentContext(host=resolved_host, engine=engine, invocation_id=invocation_id)
    started_at = utcnow()

    async def pre_check() -> dict[str, bool]:
        return {"ok": True}

    async def run_topics() -> object:
        return await execute_agent(
            TopicsCollectorAgent(ctx),
            TopicsCollectorInput(invocation_id=invocation_id, dry_run=dry_run),
            engine,
        )

    async def universe_refresh() -> dict[str, bool]:
        return {"skipped": True}  # Phase 1：universe は別タスクで投入

    async def run_screening() -> object:
        return await execute_agent(
            ScreeningAgent(ctx),
            ScreeningAgentInput(invocation_id=invocation_id, dry_run=dry_run),
            engine,
        )

    async def run_market_analyst() -> object:
        candidates = _candidate_tickers(engine)
        return await execute_agent(
            MarketAnalystAgent(ctx),
            MarketAnalystInput(invocation_id=invocation_id, tickers=candidates, dry_run=dry_run),
            engine,
        )

    async def run_sell() -> object:
        return await execute_agent(
            SellRecommenderAgent(ctx),
            SellRecommenderInput(invocation_id=invocation_id, dry_run=dry_run),
            engine,
        )

    async def run_portfolio() -> object:
        return await execute_agent(
            PortfolioBuilderAgent(ctx),
            PortfolioBuilderInput(invocation_id=invocation_id, mode="review", dry_run=dry_run),
            engine,
        )

    async def link_topics() -> dict[str, bool]:
        return {"skipped": True}  # Phase 1：トピックス↔decisions 紐付けは後日

    async def summary() -> str:
        return _summary(engine)

    async def notify() -> dict[str, bool]:
        _log.info("morning_batch_notify", invocation_id=invocation_id)  # 実通知は Phase 1.7
        return {"notified": False}

    nodes = [
        DAGNode("pre_check", pre_check, timeout_s=30),
        DAGNode("topics_collector", run_topics, depends_on=["pre_check"], timeout_s=600),
        DAGNode("universe_refresh", universe_refresh, depends_on=["pre_check"], timeout_s=300),
        DAGNode("screening", run_screening, depends_on=["topics_collector"], timeout_s=300),
        DAGNode("market_analyst", run_market_analyst, depends_on=["screening"], timeout_s=600),
        DAGNode("sell_recommender", run_sell, depends_on=["market_analyst"], timeout_s=300),
        DAGNode("portfolio_builder", run_portfolio, depends_on=["sell_recommender"], timeout_s=60),
        DAGNode("link_topics", link_topics, depends_on=["portfolio_builder"], timeout_s=60),
        DAGNode("summary", summary, depends_on=["link_topics"], timeout_s=30),
        DAGNode("notify", notify, depends_on=["summary"], timeout_s=30),
    ]

    results = await DAGExecutor(nodes).execute()
    ended_at = utcnow()

    node_status = {name: nr.status for name, nr in results.items()}
    errors = [
        {"node": nr.name, "error": nr.error}
        for nr in results.values()
        if nr.status != "success" and nr.error
    ]
    summary_node = results.get("summary")
    summary_text = (
        str(summary_node.result) if summary_node and summary_node.result else "朝バッチ完了"
    )

    batch = BatchState(
        invocation_id=invocation_id,
        batch_type="morning",
        status=overall_status(results),
        started_at=started_at,
        ended_at=ended_at,
        node_status=node_status,
        summary=summary_text,
        errors=errors,
    )
    _save_batch_state(engine, batch)
    return batch


def _save_batch_state(engine: Engine, batch: BatchState) -> None:
    with Session(engine, expire_on_commit=False) as session:
        existing = session.get(BatchState, batch.invocation_id)
        if existing is not None:
            session.delete(existing)
            session.commit()
        session.add(batch)
        session.commit()
