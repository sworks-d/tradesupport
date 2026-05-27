"""朝バッチのオーケストレーション（ORCHESTRATION.md §2.4 / §9）。

6エージェントを DAG に束ねて実行し、結果を batch_states に記録する。
エージェント間のデータ受け渡しは DB 経由（§A-4）。ノード失敗は捕捉して継続（§A-3）。

実行順（§2.4）:
    pre_check → topics_collector / universe_refresh → screening → market_analyst
    → sell_recommender → portfolio_builder → link_topics → summary → notify

universe_refresh / link_topics / notify は Phase 1 では軽量（no-op / ログ）。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import execute_agent
from trading_agent.agents.context import AgentContext
from trading_agent.agents.market_analyst import MarketAnalystAgent, MarketAnalystInput
from trading_agent.agents.portfolio_builder import PortfolioBuilderAgent, PortfolioBuilderInput
from trading_agent.agents.screening_agent import ScreeningAgent, ScreeningAgentInput
from trading_agent.agents.sell_recommender import SellRecommenderAgent, SellRecommenderInput
from trading_agent.agents.topics_collector import TopicsCollectorAgent, TopicsCollectorInput
from trading_agent.agents.zeele_curator import ZeeleCuratorAgent, ZeeleCuratorInput
from trading_agent.magi.persist import (
    FinancialsFetcher,
    magi_verify,
    make_live_judge_fn,
    materialize_decisions,
    pending_decision_ids,
)
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
from trading_agent.models.universe import Universe
from trading_agent.orchestrator.dag import DAGExecutor, DAGNode, overall_status
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("orchestrator")


def build_host(engine: Engine) -> MCPHost:
    """全 MCP ツールを登録した MCPHost を構築する（実行用）。"""
    from trading_agent.config import get_settings
    from trading_agent.llm.anthropic_client import AnthropicClient
    from trading_agent.llm.haiku_fallback import HaikuFallbackClient

    settings = get_settings()
    host = MCPHost()
    host.register(MarketDataTool(engine))
    host.register(FundamentalsTool())
    host.register(NewsTool())
    host.register(DisclosureTool())
    host.register(TechnicalsTool())
    host.register(ScreeningTool(engine))
    # Cold Path（Ollama）は未インストール環境のため Anthropic Haiku で代用する。
    # Ollama を将来導入する場合は HaikuFallbackClient を OllamaClient(...) に戻す。
    host.register(
        LLMCallTool(
            engine,
            anthropic_client=AnthropicClient(settings.anthropic_api_key),
            ollama_client=HaikuFallbackClient(settings.anthropic_api_key),
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


def _buy_candidate_tickers(engine: Engine, limit: int = 10) -> list[str]:
    """MAGI 検証にかける買い候補。active な buy_signals を優先し、無ければ screening 上位で代替。

    （IMPROVEMENT_PLAN A-4：「active な buy/sell signals (or screening上位)」）。
    """
    with Session(engine) as session:
        buys = list(
            session.exec(
                select(BuySignal)
                .where(col(BuySignal.is_active))
                .order_by(col(BuySignal.score).desc())
            )
        )
    seen: set[str] = set()
    out: list[str] = []
    for b in buys:
        if b.ticker not in seen:
            seen.add(b.ticker)
            out.append(b.ticker)
        if len(out) >= limit:
            return out
    if out:
        return out

    # フォールバック：screening 上位（passed 問わず・composite 降順）。MAGIが深く検証する。
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(ScreeningResult).order_by(col(ScreeningResult.composite_score).desc())
            )
        )
    for r in rows:
        if r.ticker not in seen:
            seen.add(r.ticker)
            out.append(r.ticker)
        if len(out) >= limit:
            break
    return out


def _sector_lookup(engine: Engine) -> Callable[[str], str | None]:
    """ticker→セクター（信用性フィルタの業種除外用）。universe を1回読んで辞書化。"""
    with Session(engine) as session:
        sectors = {u.ticker: u.sector for u in session.exec(select(Universe))}
    return lambda ticker: sectors.get(ticker)


def _summary(engine: Engine) -> str:
    today = utcnow().date()
    with Session(engine) as session:
        buys = len(list(session.exec(select(BuySignal).where(col(BuySignal.is_active)))))
        sells = len(list(session.exec(select(SellSignal).where(col(SellSignal.is_active)))))
        topics = len([t for t in session.exec(select(Topic)) if t.collected_at.date() == today])
    return f"買い推奨 {buys} / 売り推奨 {sells} / 本日トピックス {topics}"


async def run_morning_batch(
    engine: Engine,
    *,
    host: MCPHost | None = None,
    dry_run: bool = False,
    financials_fetcher: FinancialsFetcher | None = None,
) -> BatchState:
    """朝バッチを DAG で実行し、BatchState を保存して返す。

    `financials_fetcher` を渡すと magi_verify で信用性フィルタ(S5)を効かせる
    （MELCHIOR反証＋credibility_flag）。既定 None＝OFF（テストはネット非依存）。実運用は
    `fetch_financials` を渡す。業種除外は universe の sector を引く。
    """
    invocation_id = f"morning_{utcnow().date().isoformat()}"
    resolved_host = host if host is not None else build_host(engine)
    ctx = AgentContext(host=resolved_host, engine=engine, invocation_id=invocation_id)
    started_at = utcnow()
    sector_of = _sector_lookup(engine) if financials_fetcher is not None else None

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

    async def run_zeele_curator() -> object:
        # screening_results を読んで「3週連続入賞」を ZeeleState に upsert する。
        # 日次で走ること自体は冪等（同日に複数回走らせても結果は同じ）。
        return await execute_agent(
            ZeeleCuratorAgent(ctx),
            ZeeleCuratorInput(invocation_id=invocation_id, dry_run=dry_run),
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

    async def run_materialize() -> dict[str, int]:
        # 買い候補を Decision(status="verifying") として保存（A-4）
        ids = materialize_decisions(engine, _buy_candidate_tickers(engine))
        return {"decisions": len(ids)}

    async def run_magi_verify() -> dict[str, int]:
        # 当日の未検証 decision に 3審判→防御→統合→碇 を回して保存（決定論・コスト0）
        # financials_fetcher があれば信用性フィルタ(S5)も効かせる（MELCHIOR反証＋credibility）
        ids = pending_decision_ids(engine)
        judge_fn = make_live_judge_fn(
            ctx.call_tool, financials_fetcher=financials_fetcher, sector_lookup=sector_of
        )
        return await magi_verify(engine, ids, judge_fn)

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
        DAGNode("zeele_curator", run_zeele_curator, depends_on=["screening"], timeout_s=60),
        DAGNode("market_analyst", run_market_analyst, depends_on=["screening"], timeout_s=600),
        DAGNode("sell_recommender", run_sell, depends_on=["market_analyst"], timeout_s=300),
        DAGNode("portfolio_builder", run_portfolio, depends_on=["sell_recommender"], timeout_s=60),
        DAGNode(
            "materialize_decisions",
            run_materialize,
            depends_on=["portfolio_builder"],
            timeout_s=60,
        ),
        DAGNode(
            "magi_verify",
            run_magi_verify,
            depends_on=["materialize_decisions"],
            timeout_s=600,
        ),
        DAGNode("link_topics", link_topics, depends_on=["magi_verify"], timeout_s=60),
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
