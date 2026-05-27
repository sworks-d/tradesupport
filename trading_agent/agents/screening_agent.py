"""screening-agent（AGENT_SPECS.md §1）。

universe から候補を絞り込む。各銘柄について MCP ツールでデータを集約し、screening ツール
（採点ロジック）に委譲してランク付けする。

Phase 1 の制約：90日高安・四半期 EPS・銘柄別ニュース件数はツール側の追加配線が必要なため、
本エージェントは現状取得できるフィールド（現在値 / RSI / MACD / 増収率 / PER・PBR）を渡す。
不足フィールドの採点コンポーネントは screening 側でスキップ（部分スコア）。配線拡充は後日。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.agents.theme_context import (
    SectorReturnCache,
    keyword_match_counts,
)
from trading_agent.mcp_tools.fundamentals import FundamentalsInput
from trading_agent.mcp_tools.market_data import MarketDataInput
from trading_agent.mcp_tools.screening import ScreeningInput, ScreeningTickerData
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.universe import Universe
from trading_agent.screening import (
    Financials,
    assess_credibility,
    assess_turnaround,
    relative_strength_live,
)
from trading_agent.utils.logger import get_logger

# 品質エンリッチ用の注入フェッチャ（テストはスタブ・ライブは yfinance）
FinancialsFetcher = Callable[[str], Financials | None]
PriceHistory = Callable[[str], list[float]]
_CREDIBILITY_PENALTY = 0.7  # 信用性warn の composite 減点率


def enrich_candidates(
    results: list[dict[str, Any]],
    *,
    financials_fetcher: FinancialsFetcher,
    price_history: PriceHistory,
) -> list[dict[str, Any]]:
    """上位候補に 信用性(S5)/V字(S7a)/相対力(S7c) を付与し、信用性warnは減点して再ランク。

    弾を実スクリーニングに乗せる（純粋関数・注入でテスト可能）。各銘柄の取得失敗は graceful。
    """
    for r in results:
        ticker = str(r.get("ticker", ""))
        market = str(r.get("market") or "US")
        sector = r.get("sector")
        try:
            fin = financials_fetcher(ticker)
        except Exception:
            fin = None
        if fin is not None:
            cred = assess_credibility(fin, sector=sector)
            r["credibility_flag"] = cred.credibility_flag
            r["credibility_warnings"] = cred.warnings
            r["turnaround_zone"] = assess_turnaround(fin, signals=[], credibility=cred).zone
            if cred.credibility_flag == "warn":
                base = r.get("composite_score", 0.0)
                r["composite_score"] = round(base * _CREDIBILITY_PENALTY, 1)
                r["quality_penalty"] = "信用性warn→減点"
        try:
            rs = relative_strength_live(ticker, market, history=price_history)
            r["rs_quadrant"] = rs.quadrant
        except Exception:
            r["rs_quadrant"] = "na"

    results.sort(
        key=lambda r: (r.get("composite_score", 0.0), r.get("market_cap") or 0.0), reverse=True
    )
    return results


class ScreeningAgentInput(AgentInput):
    universe_size: int = 500
    strategies: list[str] = Field(default_factory=lambda: ["v_shape", "theme"])
    # ペーパーテスト中の暫定値：本来 50.0 だが、yfinance の JP 四半期 EPS データが
    # 薄く composite が現実的に 50 に届かないため、観察可能な水準まで一時的に下げる。
    # 入力データ層が整備されたら（JQuants 切替等）50.0 に戻す（→ docs/MORNING_REVIEW.md
    # の閾値見直し論点）。
    min_score: float = 20.0
    max_results: int = 30


class ScreeningAgentOutput(AgentOutput):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    total_screened: int = 0
    strategy_breakdown: dict[str, int] = Field(default_factory=dict)


class ScreeningAgent(Agent[ScreeningAgentInput]):
    """V字 / テーマ候補のスクリーニングエージェント。"""

    name = "screening_agent"
    description = "universe からデータを集約し、screening ツールで候補を絞り込む。"
    required_tools = ["screening", "market_data", "technicals", "fundamentals"]
    default_routing = "hot"

    def __init__(
        self,
        context: AgentContext,
        *,
        financials_fetcher: FinancialsFetcher | None = None,
        price_history: PriceHistory | None = None,
        sector_cache: SectorReturnCache | None = None,
    ) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)
        # 渡されたら 信用性/V字/相対力 で候補をエンリッチ（既定OFF＝ネット非依存・テスト用）
        self._financials_fetcher = financials_fetcher
        self._price_history = price_history
        # セクター/市場 30日リターンの取得キャッシュ（テスト時はスタブ注入）
        self._sector_cache = sector_cache or SectorReturnCache()

    async def execute(self, agent_input: ScreeningAgentInput) -> AgentOutput:
        import asyncio

        universe = self._load_universe(self._ctx.engine, agent_input.universe_size)
        # テーマスコア用：topics 表から各銘柄の言及件数を一括取得（DB1往復）
        keyword_counts = keyword_match_counts(
            self._ctx.engine, [u.ticker for u in universe]
        )
        # universe が大きい場合に直列処理だと timeout（universe=500 で 5 分超え）。
        # asyncio.Semaphore で yfinance rate limit を考慮しつつ並列化。
        sem = asyncio.Semaphore(16)

        async def _gather_with_limit(u: Universe) -> ScreeningTickerData:
            async with sem:
                return await self._gather(u, keyword_count=keyword_counts.get(u.ticker, 0))

        tickers_data = list(
            await asyncio.gather(*[_gather_with_limit(u) for u in universe])
        )

        sout = await self._ctx.call_tool(
            "screening",
            ScreeningInput(
                tickers_data=tickers_data,
                strategies=agent_input.strategies,
                min_score=agent_input.min_score,
                max_results=agent_input.max_results,
                persist=not agent_input.dry_run,
            ),
        )
        results: list[dict[str, Any]] = getattr(sout, "results", []) or []
        total: int = getattr(sout, "total_screened", 0)

        # 弾を実スクリーニングに乗せる（フェッチャがある時のみ・上位候補のみ＝負荷限定）
        if self._financials_fetcher is not None and self._price_history is not None:
            results = enrich_candidates(
                results,
                financials_fetcher=self._financials_fetcher,
                price_history=self._price_history,
            )

        breakdown: Counter[str] = Counter()
        for r in results:
            for strat in r.get("matched_strategies", []):
                breakdown[strat] += 1

        return ScreeningAgentOutput(
            success=sout.success,
            invocation_id=agent_input.invocation_id,
            summary=f"{len(results)} 候補を抽出（{total} 銘柄スクリーニング）",
            candidates=results,
            total_screened=total,
            strategy_breakdown=dict(breakdown),
        )

    def _load_universe(self, engine: Engine, limit: int) -> list[Universe]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(Universe)
                    .where(col(Universe.is_active))
                    .order_by(col(Universe.market_cap_jpy).desc())
                    .limit(limit)
                )
            )

    async def _gather(self, u: Universe, *, keyword_count: int = 0) -> ScreeningTickerData:
        data = ScreeningTickerData(
            ticker=u.ticker,
            name=u.name,
            market=u.market,
            sector=u.sector,
            market_cap=u.market_cap_jpy,
            keyword_match_count=keyword_count,
        )
        await self._fill_market_data(u.ticker, data)
        await self._fill_technicals(u.ticker, data)
        await self._fill_fundamentals(u.ticker, data)
        # テーマスコア：セクター/市場 30日リターン（ETF 経由）
        data.market_return_30d = self._sector_cache.market_return(u.market)
        data.sector_return_30d = self._sector_cache.sector_return(u.market, u.sector)
        return data

    async def _fill_market_data(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool("market_data", MarketDataInput(tickers=[ticker]))
            payload = getattr(out, "data", None)
            if out.success and payload:
                data.current_price = payload.get(ticker, {}).get("current_price")
        except Exception as exc:
            self._log.warning("screening_market_data_failed", ticker=ticker, error=str(exc))

    async def _fill_technicals(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool("technicals", TechnicalsInput(ticker=ticker))
            payload = getattr(out, "data", None)
            signals = getattr(out, "signals", []) or []
            if out.success and payload:
                rsi = payload.get("rsi")
                if isinstance(rsi, int | float):
                    data.rsi = float(rsi)
                data.macd_cross_recent = "golden_cross" in signals or "macd_bullish" in signals
                # S7 株価底打ち判定用：90日終値の高安（近似）
                min_p = payload.get("min_price_90d")
                if isinstance(min_p, int | float):
                    data.min_price_90d = float(min_p)
                max_p = payload.get("max_price_90d")
                if isinstance(max_p, int | float):
                    data.max_price_90d = float(max_p)
        except Exception as exc:
            self._log.warning("screening_technicals_failed", ticker=ticker, error=str(exc))

    async def _fill_fundamentals(self, ticker: str, data: ScreeningTickerData) -> None:
        try:
            out = await self._ctx.call_tool(
                "fundamentals",
                FundamentalsInput(
                    ticker=ticker,
                    fields=[
                        "revenue_growth",
                        "per",
                        "pbr",
                        # S7 業績反転判定用：四半期 EPS と YoY 成長率
                        "eps_latest_q",
                        "eps_prev_prev_q",
                        "eps_growth_latest_q",
                        "eps_growth_prev_prev_q",
                    ],
                ),
            )
            payload = getattr(out, "data", None)
            if out.success and payload:
                growth = payload.get("revenue_growth")
                if isinstance(growth, int | float):
                    data.revenue_growth_latest_q = float(growth)
                for key in (
                    "eps_latest_q",
                    "eps_prev_prev_q",
                    "eps_growth_latest_q",
                    "eps_growth_prev_prev_q",
                ):
                    v = payload.get(key)
                    if isinstance(v, int | float):
                        setattr(data, key, float(v))
        except Exception as exc:
            self._log.warning("screening_fundamentals_failed", ticker=ticker, error=str(exc))
