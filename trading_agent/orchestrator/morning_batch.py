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
from trading_agent.utils.time_utils import today_jst, utcnow

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
    # Cold Path（要約・分類）は Anthropic Haiku（HaikuFallbackClient）。Hot/Critical は Sonnet/Opus。
    host.register(
        LLMCallTool(
            engine,
            anthropic_client=AnthropicClient(settings.anthropic_api_key),
            cold_client=HaikuFallbackClient(settings.anthropic_api_key),
        )
    )
    return host


def _candidate_tickers(engine: Engine, limit: int = 10) -> list[str]:
    """直近スクリーニングの合格候補（composite 上位）を返す。

    v2.10: composite_score 同点時は市場規模昇順（小型優先）で「成長銘柄を追う」設計。
    旧: 同点時順序が SQLite 任せで大型偏向していた。
    """
    # v2.10 ハルシネーション防壁: Universe.is_active=True に限定
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(ScreeningResult)
                .join(Universe, col(Universe.ticker) == col(ScreeningResult.ticker))
                .where(col(ScreeningResult.screening_passed))
                .where(col(Universe.is_active))
                .order_by(
                    col(ScreeningResult.composite_score).desc(),
                    col(Universe.market_cap_jpy).asc().nullslast(),
                )
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


def _filter_affordable_tickers(
    tickers: list[str], available_jpy: float, *, pool_multiplier: int = 4
) -> list[str]:
    """予算内で買える銘柄を優先して並べ替え（v2.10 少額運用対応）。

    available_jpy が指定された場合、yfinance で株価取得して
    単元株コスト ≤ available_jpy の銘柄を前に並べる。
    予算外の銘柄も末尾に残す（universe に予算内が少ない場合の fallback）。

    pool_multiplier: 候補リストの何倍まで価格取得するか（API コスト制御）

    Returns:
        並べ替え後の ticker リスト。順序: 予算内（元順序）→ 予算外（元順序）。
    """
    if available_jpy <= 0 or not tickers:
        return tickers

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    from trading_agent.utils.lot_size import get_lot_size

    affordable: list[str] = []
    unaffordable: list[str] = []
    # 上位 N 件だけ価格取得（API コスト制御）
    check_limit = min(len(tickers), pool_multiplier * 10)
    try:
        import yfinance as yf
    except ImportError:
        return tickers

    for t in tickers[:check_limit]:
        try:
            sym = to_yfinance_symbol(t)
            price = float(yf.Ticker(sym).fast_info.last_price or 0)
            if price <= 0:
                # 価格取れない → 予算外扱い（推測しない）
                unaffordable.append(t)
                continue
            lot = get_lot_size(t)
            unit_cost = price * lot
            if unit_cost <= available_jpy:
                affordable.append(t)
            else:
                unaffordable.append(t)
        except Exception as exc:
            _log.warning(
                "affordability_check_failed",
                ticker=t,
                error_type=type(exc).__name__,
            )
            unaffordable.append(t)

    # 残りの未チェック分（pool_multiplier×10 を超えた分）も末尾に
    unchecked = tickers[check_limit:]
    return affordable + unaffordable + unchecked


def _buy_candidate_tickers(
    engine: Engine, limit: int = 10, *, available_jpy: float | None = None
) -> list[str]:
    """MAGI 検証にかける買い候補。active な buy_signals を優先し、無ければ screening 上位で代替。

    （IMPROVEMENT_PLAN A-4：「active な buy/sell signals (or screening上位)」）。

    v2.10: available_jpy が指定されたら、予算内で買える銘柄を優先する
    （少額運用でも分散投資できるよう、株価 × 単元株 ≤ available_jpy の銘柄を上位に）。
    """
    # v2.10 ハルシネーション防壁: BuySignal も Universe.is_active=True に限定
    # （inactive 銘柄が awaiting で残ってもユーザーに推奨しない）
    with Session(engine) as session:
        buys = list(
            session.exec(
                select(BuySignal)
                .join(Universe, col(Universe.ticker) == col(BuySignal.ticker))
                .where(col(BuySignal.is_active))
                .where(col(Universe.is_active))
                .order_by(col(BuySignal.score).desc())
            )
        )
    seen: set[str] = set()
    out: list[str] = []
    for b in buys:
        if b.ticker not in seen:
            seen.add(b.ticker)
            out.append(b.ticker)
    if not out:
        # フォールバック：screening 上位（passed 問わず）。MAGIが深く検証する。
        # v2.10: composite_score 降順 + market_cap 昇順（小型優先・成長銘柄を追う）
        # ハルシネーション防壁: Universe.is_active=True に限定
        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(ScreeningResult)
                    .join(Universe, col(Universe.ticker) == col(ScreeningResult.ticker))
                    .where(col(Universe.is_active))
                    .order_by(
                        col(ScreeningResult.composite_score).desc(),
                        col(Universe.market_cap_jpy).asc().nullslast(),
                    )
                )
            )
        for r in rows:
            if r.ticker not in seen:
                seen.add(r.ticker)
                out.append(r.ticker)

    # v2.10: 予算内で買える銘柄を優先（少額運用対応）
    if available_jpy is not None and available_jpy > 0:
        out = _filter_affordable_tickers(out, available_jpy)

    return out[:limit]


def _sector_lookup(engine: Engine) -> Callable[[str], str | None]:
    """ticker→セクター（信用性フィルタの業種除外用）。universe を1回読んで辞書化。"""
    with Session(engine) as session:
        sectors = {u.ticker: u.sector for u in session.exec(select(Universe))}
    return lambda ticker: sectors.get(ticker)


def _summary(engine: Engine) -> str:
    today = today_jst()
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
    invocation_id = f"morning_{today_jst().isoformat()}"
    resolved_host = host if host is not None else build_host(engine)
    ctx = AgentContext(host=resolved_host, engine=engine, invocation_id=invocation_id)
    started_at = utcnow()
    sector_of = _sector_lookup(engine) if financials_fetcher is not None else None

    async def pre_check() -> dict[str, object]:
        """v2.10: 3 データソース（J-Quants / NewsAPI / EDINET）の設定確認。

        ここでは軽量に "設定があるか" のみ確認する。実 API の疎通は各ノードで個別に
        行われる（失敗時は warning ログ + fallback / 空 list を返す設計）。
        朝バッチを止めない（halt しない）。
        """
        from trading_agent.config import load_settings
        from trading_agent.mcp_tools.jquants import get_default_client
        from trading_agent.utils.logger import get_logger

        _log = get_logger("orchestrator.pre_check")
        s = load_settings()
        result: dict[str, object] = {"ok": True}

        # J-Quants（JP 株の財務正本）
        jq_client = get_default_client()
        result["jquants_configured"] = jq_client is not None
        if jq_client is None:
            _log.warning("pre_check_jquants_not_configured")

        # NewsAPI（CASPER / topics_collector のニュース補強）
        result["newsapi_configured"] = bool(s.newsapi_key)
        if not s.newsapi_key:
            _log.warning("pre_check_newsapi_not_configured")

        # EDINET（disclosure フラグ・財務反証用）
        result["edinet_configured"] = bool(s.edinet_api_key)
        if not s.edinet_api_key:
            _log.warning("pre_check_edinet_not_configured")

        # Anthropic（必須）
        result["anthropic_configured"] = bool(s.anthropic_api_key)
        if not s.anthropic_api_key:
            _log.warning("pre_check_anthropic_not_configured")
            result["ok"] = False  # LLM 無しでは朝バッチは意味を成さない

        # v2.10: 前日以前の未完了 awaiting Decision を自動 cancel（翌日繰越処理）
        # ユーザーが楽天で発注しなかった Decision は今日の朝バッチで新たに評価し直す
        # （同じ銘柄が候補なら今日の Decision として再登録される）
        from trading_agent.models.decisions import Decision as _Decision

        today_now = today_jst()
        with Session(engine, expire_on_commit=False) as sess:
            stale = list(
                sess.exec(
                    select(_Decision)
                    .where(col(_Decision.status) == "awaiting")
                    .where(col(_Decision.date) < today_now)
                ).all()
            )
            for d in stale:
                d.status = "cancelled"
                d.thesis_at_decision = (
                    (d.thesis_at_decision or "") + " | 翌日繰越で auto-cancel"
                )
                sess.add(d)
            sess.commit()
            result["stale_awaiting_cancelled"] = len(stale)
            if stale:
                _log.info(
                    "pre_check_stale_awaiting_cancelled",
                    count=len(stale),
                    tickers=[d.ticker for d in stale[:5]],
                )

        return result

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
        # screening_results を読んで「N週連続入賞」を ZeeleState に upsert する。
        # 本来は 3週連続だが、ペーパーテスト期間は観察可能性を優先して 1週連続 まで
        # 緩和する（screening 側 min_score も併せて緩和済み）。データ層が整備
        # されたら ZeeleCuratorInput の qualification_weeks を 3 に戻す。
        return await execute_agent(
            ZeeleCuratorAgent(ctx),
            ZeeleCuratorInput(
                invocation_id=invocation_id,
                dry_run=dry_run,
                qualification_weeks=1,
            ),
            engine,
        )

    async def run_zeele_llm_scout() -> object:
        """PIPELINE v3 Phase 4-B: ZEELE LLM 探索（zeele_curator 後段）。

        universe.is_active=True で ZEELE プール外の銘柄を Haiku で preset 判定し
        upsert する。BudgetGuard で日次上限 ¥10 / 月次 ¥200。
        実 LLM 呼出が発生するため、構築完了前は実バッチで動かさない。
        """
        from trading_agent.agents.zeele_llm_scout import (
            ZeeleLLMScoutAgent,
            ZeeleLLMScoutInput,
        )

        return await execute_agent(
            ZeeleLLMScoutAgent(ctx),
            ZeeleLLMScoutInput(
                invocation_id=invocation_id,
                dry_run=dry_run,
                max_calls=30,
                daily_budget_jpy=10.0,
            ),
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

    async def run_trailing_check() -> dict:
        """v2.10 Phase 1A-Step2: 保有銘柄の真の trailing stop チェック。
        peak_pnl_pct を更新し、trail_price 到達銘柄に sell Decision を登録。"""
        from trading_agent.portfolio.trailing_check import (
            run_trailing_check as _rtc,
        )

        return _rtc(engine)

    async def run_pyramid_check() -> dict:
        """v2.10 Phase 1A-Step2: 含み益で planned_total_qty まで追加買付。"""
        from trading_agent.portfolio.pyramid_check import (
            run_pyramid_check as _rpc,
        )

        return _rpc(engine)

    async def run_auto_fill() -> dict:
        """v2.10 Phase J + G-1: 自動売買モードのみ朝バッチで buy Decision を即 fill。

        manual モード: skip（人間が misato_dispatch.py で手動執行）
        auto モード: status=approved の buy Decision を即 fill
        I-10 ガード: HALT 中は強制 skip
        H-7 ガード: 累計 DD ≤ -10% で新規 buy 抑制
        H-6 ガード: 保有銘柄と相関 |r| ≥ 0.7 の銘柄は skipped 化
        """
        from sqlmodel import Session, col, select

        from trading_agent.models.decisions import Decision
        from trading_agent.portfolio.anomaly_detector import check_dd_brake, is_halted
        from trading_agent.portfolio.correlation import assess_new_buy_correlation
        from trading_agent.utils.lot_size import is_auto_mode

        if not is_auto_mode():
            return {"status": "skipped", "reason": "automation_mode=manual"}
        if is_halted():
            return {"status": "skipped", "reason": "halted"}

        from trading_agent.portfolio.misato import treasury_view
        from trading_agent.portfolio.paper_exec import paper_fill_approved
        from trading_agent.utils.lot_size import get_broker_mode

        broker_mode = get_broker_mode()

        # v2.10 Phase H-7: ポートフォリオ DD ブレーキ
        dd_check = check_dd_brake(engine)
        if dd_check["brake_active"]:
            return {
                "status": "skipped",
                "reason": "dd_brake",
                "dd_check": dd_check,
            }

        # v2.10 Phase H-6: 保有銘柄と高相関の buy Decision を skipped 化
        # perf 改善: 保有銘柄リターンは候補ループ前後で 1 回しか取得しないよう共有 cache
        blocked_correlations: list[dict] = []
        returns_cache: dict[str, list[float] | None] = {}
        with Session(engine) as s:
            approved_buys = list(
                s.exec(
                    select(Decision)
                    .where(col(Decision.action) == "buy")
                    .where(col(Decision.status) == "approved")
                ).all()
            )
            for d in approved_buys:
                # ピラミッディング Decision は H-6 対象外（既保有 = 相関判定不要）
                if "ピラミッディング" in (d.thesis_at_decision or ""):
                    continue
                corr_check = assess_new_buy_correlation(
                    engine,
                    d.ticker,
                    broker_mode=broker_mode,
                    returns_cache=returns_cache,
                )
                if corr_check["blocked"]:
                    d.status = "skipped"
                    d.thesis_at_decision = (
                        (d.thesis_at_decision or "")
                        + f" | H-6 ブロック: {corr_check['reason']}"
                    )
                    s.add(d)
                    blocked_correlations.append(
                        {"ticker": d.ticker, **corr_check}
                    )
            s.commit()

        tv = treasury_view(engine, broker_mode)
        cash = float(tv.get("available_jpy") or 0)
        if cash <= 0:
            return {
                "status": "skipped",
                "reason": "no_cash_available",
                "h6_blocked": blocked_correlations,
            }

        def _price_lookup(ticker: str) -> float | None:
            try:
                import yfinance as yf

                from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

                symbol = to_yfinance_symbol(ticker)
                p = float(yf.Ticker(symbol).fast_info.last_price or 0)
                return p if p > 0 else None
            except Exception:
                return None

        def _is_jp_lookup(ticker: str) -> bool:
            return len(ticker) == 4 and ticker.isdigit() or (
                len(ticker) == 4 and ticker[:3].isdigit() and ticker[3].isalpha()
            )

        result = paper_fill_approved(
            engine,
            price_lookup=_price_lookup,
            is_jp_lookup=_is_jp_lookup,
            cash_jpy=cash,
            broker_mode=broker_mode,  # codex: 明示渡しで環境モード混線を防ぐ（cap も効く）
        )
        return {
            "status": "active",
            "fills": len(result.fills),
            "skipped": len(result.skipped),
            "cash_after": result.cash_after,
            "h6_blocked": blocked_correlations,
            "dd_check": dd_check,
        }

    async def run_close_due() -> dict:
        """v2.10 Phase 1A-Step2 修正 (致命 1) + Phase J: trailing 由来の sell は両モードで実行。

        設計判断 (致命候補 3 修正):
          - trailing は機械判定 (stop に届いた) → 執行も機械で問題ない
          - manual モードは「buy 候補の判定」を人間が承認するための機構であり、
            stop loss まで人間任せにすると損失拡大リスク
          - したがって両モードで close_due を実行
        I-10 ガード: HALT 中は強制 skip
        """
        from trading_agent.portfolio.anomaly_detector import is_halted

        if is_halted():
            return {"status": "skipped", "reason": "halted"}

        from trading_agent.portfolio.paper_exec import paper_close_approved

        def _price_lookup(ticker: str) -> float | None:
            try:
                import yfinance as yf

                from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

                symbol = to_yfinance_symbol(ticker)
                p = float(yf.Ticker(symbol).fast_info.last_price or 0)
                return p if p > 0 else None
            except Exception:
                return None

        return paper_close_approved(engine, price_lookup=_price_lookup)

    async def run_materialize() -> dict[str, int]:
        # 買い候補を Decision(status="verifying") として保存（A-4）
        # PIPELINE v3: 質優先 (B 式) に統一。予算フィルタは下流 (DS Scout / opportunity_fill)
        # で効かせる。
        #   旧: 上位 10 件のうち予算内銘柄を優先で並び替え → 予算外の質高銘柄が消える
        #   新: 質スコア降順そのまま上位 10 件 → 予算拡張時にも既存 Decision を活用可能
        # Treasury 残高は観察用に取得・記録のみ。
        try:
            from trading_agent.portfolio.misato import treasury_view
            from trading_agent.utils.lot_size import get_broker_mode

            tv = treasury_view(engine, get_broker_mode())
            available = float(tv.get("available_jpy") or 0)
        except Exception:
            available = 0.0
        candidates = _buy_candidate_tickers(engine)  # 質優先・予算フィルタなし
        ids = materialize_decisions(engine, candidates)
        return {"decisions": len(ids), "available_jpy": available}

    async def run_magi_verify() -> dict[str, int]:
        # 当日の未検証 decision に 3審判→防御→統合→碇 を回して保存（決定論・コスト0）
        # financials_fetcher があれば信用性フィルタ(S5)も効かせる（MELCHIOR反証＋credibility）
        # A prime: 同じ J-Quants fin から earnings 系 signal_tags を sink に集め、Decision に
        #          record-only でマージ（新規 fetch 0・売買は変えない・shadow 計測用）。
        ids = pending_decision_ids(engine)
        earnings_sink: dict[str, tuple[list[str], dict]] = {}
        judge_fn = make_live_judge_fn(
            ctx.call_tool,
            financials_fetcher=financials_fetcher,
            sector_lookup=sector_of,
            earnings_sink=earnings_sink,
        )
        return await magi_verify(engine, ids, judge_fn, earnings_sink=earnings_sink)

    async def run_katsuragi_dispatch() -> dict:
        """PIPELINE v3 Phase 1 M1.1 + M1.2 + N2: KATSURAGI 統合ノード。

        portfolio/misato.py:dispatch() を approve=False で呼び、DispatchPlan を生成。
        Assignment（pilot 割当・source・score・picked）を Decision に反映して永続化する。

        N2: Assignment.assigned_to (機名) から personality (horizon_days / stop_loss_pct)
            を引き、Decision.target_period_days / stop_pct を書き込む（旧 fallback 撤去
            の準備段階）。
        """
        from trading_agent.models.decisions import Decision as _Decision
        from trading_agent.portfolio.misato import dispatch as _dispatch
        from trading_agent.portfolio.personality import PERSONALITIES

        try:
            plan = _dispatch(
                engine=engine,
                total_budget_jpy=None,  # treasury から自動取得
                approve=False,           # 朝バッチでは dry-run
            )
        except Exception as exc:
            _log.warning(
                "katsuragi_dispatch_failed",
                error_type=type(exc).__name__,
                msg=str(exc)[:200],
            )
            return {"failed": True, "error_type": type(exc).__name__}

        # Assignment を Decision に反映
        today_now = today_jst()
        updated = 0
        params_filled = 0
        assigned_ids: set[int] = set()
        with Session(engine, expire_on_commit=False) as sess:
            for a in plan.assignments:
                # ZEELE 由来（real_decision=None）は decision_id が負値（-1000 -...）
                if a.decision_id < 0:
                    continue
                d = sess.get(_Decision, a.decision_id)
                if d is None or d.date != today_now:
                    continue
                assigned_ids.add(a.decision_id)
                # N5: KATSURAGI 情報を thesis 先頭に置く
                #     HTML が thesis.split("|")[0] で先頭セクションを表示するため
                parts = [
                    f"KATSURAGI:{a.assigned_to}",
                    f"source={a.source}",
                    f"score={a.score:.2f}",
                    f"picked={'✓' if a.picked else '−'}",
                ]
                if a.preset:
                    parts.append(f"preset={a.preset}")
                note = " ".join(parts)
                # 既存 thesis があれば末尾に保持、新しい KATSURAGI 情報を先頭へ
                existing = d.thesis_at_decision or ""
                if existing.startswith("KATSURAGI:"):
                    # 過去 KATSURAGI 情報を削除して新しいものに置換
                    rest = existing.split("|", 1)
                    existing = rest[1].strip(" |") if len(rest) > 1 else ""
                d.thesis_at_decision = (note + (" | " + existing if existing else "")).strip(" |")
                # N2: 機の personality から horizon / stop を Decision に書き込む
                pers = PERSONALITIES.get(a.assigned_to)
                if pers is not None:
                    if d.target_period_days is None:
                        d.target_period_days = pers.horizon_days
                    if d.stop_pct is None:
                        d.stop_pct = pers.stop_loss_pct
                    params_filled += 1
                sess.add(d)
                updated += 1

            # N5: Assignment 0 件 / 一部 Decision が KATSURAGI 対象外の場合に
            #     「候補プール外（DS scout 申請拒否 or opportunity_fill 質/予算未達）」
            #     を thesis 先頭に書き込み、HTML に透明性情報として表示する。
            untouched = 0
            decs_today = sess.exec(
                select(_Decision)
                .where(col(_Decision.date) == today_now)
                .where(col(_Decision.status) == "awaiting")
                .where(col(_Decision.action) == "buy")
            ).all()
            for d in decs_today:
                if d.id in assigned_ids:
                    continue
                existing = d.thesis_at_decision or ""
                if existing.startswith("KATSURAGI:"):
                    continue
                note = "KATSURAGI:候補プール外（DS未申請 or 質/予算未達）"
                d.thesis_at_decision = (
                    note + (" | " + existing if existing else "")
                ).strip(" |")
                sess.add(d)
                untouched += 1

            sess.commit()

        return {
            "halted": plan.halted,
            "halt_reason": plan.halt_reason or "",
            "total_budget_jpy": float(plan.total_budget_jpy or 0),
            "n_assignments": len(plan.assignments),
            "n_picked": sum(1 for a in plan.assignments if a.picked),
            "n_decisions_updated": updated,
            "n_decisions_params_filled": params_filled,
            "n_decisions_pool_out": untouched,
            "n_promotions": len(plan.promotions),
        }

    async def link_topics() -> dict[str, bool]:
        return {"skipped": True}  # Phase 1：トピックス↔decisions 紐付けは後日

    async def summary() -> str:
        return _summary(engine)

    async def notify() -> dict[str, bool | str | int]:
        _log.info("morning_batch_notify", invocation_id=invocation_id)
        result: dict[str, bool | str | int] = {"notified": False}
        # v2.10: 発注リスト HTML を生成（autoreport/orders/YYYY-MM-DD.html）
        try:
            from trading_agent.reporting.order_list import generate_order_list

            path = generate_order_list(engine)
            _log.info("order_list_generated", path=str(path))
            result["order_list_path"] = str(path)
        except Exception as exc:
            _log.warning(
                "order_list_generation_failed",
                error_type=type(exc).__name__,
            )
            result["order_list_path"] = ""

        # v2.10: 試験運用継続のため、paper モード自動 fill を実行
        # 「ユーザーが推奨通りに買った想定」で Portfolio を作成し、
        # 翌日の trailing_check / close_due が自動執行する流れを担保
        try:
            from trading_agent.evaluation.job import stamp_evaluation_fields
            from trading_agent.models.decisions import Decision as _Decision
            from trading_agent.models.portfolio import Portfolio as _Portfolio
            from trading_agent.portfolio.misato import (
                deployable_budget_jpy,
                treasury_view,
            )
            from trading_agent.reporting.order_list import build_order_items

            # codex B/#2: Phase C unlock 有効時は paper_auto を無効化する。
            # この直 fill は paper_fill_approved を通らず available(¥100万側) を直叩きするため、
            # 解放枠(¥10万) を無視して active exposure を膨らませ、公式 ds_dispatch の deployable を侵食する。
            # Phase C では公式フロー(dummy-system ds_dispatch)が fill を担うので、paper_auto は止める。
            _phase_c_active = deployable_budget_jpy(engine, "paper") is not None
            tv = treasury_view(engine, "paper")
            available = float(tv.get("available_jpy") or 0)
            if _phase_c_active:
                result["paper_auto_filled"] = 0
                result["paper_auto_skipped"] = "phase_c_unlock_active"
                _log.info("paper_auto_skipped_phase_c",
                          reason="unlock active: official ds_dispatch handles fills")
            elif available > 0:
                items = build_order_items(engine, available_jpy=available)
                items_by_decision = {it.decision_id: it for it in items}
                today_now = today_jst()
                # A3/A8: エントリ時点の trailing 相場局面を 1 回取得（ゲート⑥両局面判定）。
                try:
                    from trading_agent.wille.ritsuko import detect_market_cycle

                    entry_regime = str(
                        detect_market_cycle().get("cycle") or "unknown"
                    )
                except Exception:
                    entry_regime = "unknown"
                filled_count = 0
                with Session(engine, expire_on_commit=False) as sess:
                    decs = list(
                        sess.exec(
                            select(_Decision)
                            .where(col(_Decision.date) == today_now)
                            .where(col(_Decision.status) == "awaiting")
                            .where(col(_Decision.action) == "buy")
                        ).all()
                    )
                    import datetime as _dt

                    for d in decs:
                        it = items_by_decision.get(d.id)
                        if (
                            it is None
                            or it.recommended_shares == 0
                            or it.current_price is None
                        ):
                            continue
                        d.status = "filled"
                        d.entry_price = it.current_price
                        d.shares_filled = float(it.recommended_shares)
                        period = int(getattr(d, "target_period_days", None) or 90)
                        # P0: 評価前提フィールド（stop/target/評価期日）を刻む。
                        # これが無いと filled が evaluate_due_decisions に乗らず実績が貯まらない。
                        stamp_evaluation_fields(
                            d,
                            target_period_days=period,
                            on_date=today_now,
                            market_regime=entry_regime,
                            filled_via="paper_auto",
                        )
                        sess.add(d)
                        sess.add(
                            _Portfolio(
                                ticker=d.ticker,
                                buy_date=today_now,
                                buy_price=it.current_price,
                                qty=it.recommended_shares,
                                currency="JPY",
                                strategy_category=getattr(d, "strategy_category", None)
                                or "中期",
                                target_period_days=period,
                                target_pct=0.20,
                                stop_loss_pct=float(d.stop_pct or 0.10),
                                target_date=today_now + _dt.timedelta(days=period),
                                thesis=d.thesis_at_decision or "",
                                status="active",
                                broker_mode="paper",
                                planned_total_qty=it.recommended_shares,
                                decision_id=d.id,
                            )
                        )
                        filled_count += 1
                    sess.commit()
                result["paper_auto_filled"] = filled_count
                _log.info("paper_auto_filled", count=filled_count)
        except Exception as exc:
            _log.warning(
                "paper_auto_fill_failed",
                error_type=type(exc).__name__,
            )

        return result

    async def run_anomaly_check_node() -> dict:
        """v2.10 Phase I-10: 異常検知 + HALT。auto モードのみ HALT 発火。"""
        from trading_agent.portfolio.anomaly_detector import run_anomaly_check
        from trading_agent.utils.lot_size import get_broker_mode

        return run_anomaly_check(engine, broker_mode=get_broker_mode())

    nodes = [
        DAGNode("pre_check", pre_check, timeout_s=30),
        # v2.10 Phase I-10: 異常検知 + HALT を pre_check 直後に
        DAGNode("anomaly_check", run_anomaly_check_node, depends_on=["pre_check"], timeout_s=60),
        DAGNode("topics_collector", run_topics, depends_on=["pre_check"], timeout_s=600),
        DAGNode("universe_refresh", universe_refresh, depends_on=["pre_check"], timeout_s=300),
        DAGNode("screening", run_screening, depends_on=["topics_collector"], timeout_s=900),
        DAGNode("zeele_curator", run_zeele_curator, depends_on=["screening"], timeout_s=60),
        # PIPELINE v3 Phase 4-B: ZEELE LLM 探索 (zeele_curator 後段)
        # 決定論的入賞だけでなく、screening 漏れの V字/テーマ/攻め銘柄を Haiku で発掘。
        # BudgetGuard 日次 ¥10 / 月次 ¥200 で安全装置。
        DAGNode(
            "zeele_llm_scout",
            run_zeele_llm_scout,
            depends_on=["zeele_curator"],
            timeout_s=600,
        ),
        # PIPELINE v3 Phase 2 M2.1: market_analyst は AKAGI (wille/ritsuko) に役割移管予定。
        # 現状は news_sentiment_score の供給源として screening 後に並列保持（Phase 3 M3.1 で wille/ritsuko 経由に統合）。
        DAGNode("market_analyst", run_market_analyst, depends_on=["screening"], timeout_s=600),
        # PIPELINE v3 Phase 2 M2.1: materialize_decisions と magi_verify を screening 直後に移動。
        # 旧: auto_fill の後（事後検証）/ 新: katsuragi_dispatch の前（事前判定 → 候補プール入力）
        DAGNode(
            "materialize_decisions",
            run_materialize,
            depends_on=["market_analyst", "zeele_curator", "zeele_llm_scout"],
            timeout_s=60,
        ),
        DAGNode(
            "magi_verify",
            run_magi_verify,
            depends_on=["materialize_decisions"],
            timeout_s=600,
        ),
        # PIPELINE v3 Phase 1 M1.1+M1.2: KATSURAGI 統合ノード。
        # MAGI awaiting + ZEELE active を候補プールに、AKAGI Brief + DS scout + priority + opportunity fill 統合。
        DAGNode(
            "katsuragi_dispatch",
            run_katsuragi_dispatch,
            depends_on=["magi_verify"],
            timeout_s=900,
        ),
        DAGNode(
            "sell_recommender",
            run_sell,
            depends_on=["katsuragi_dispatch"],
            timeout_s=300,
        ),
        # PIPELINE v3 Phase 2 M2.2: portfolio_builder(review) を廃止。
        # 旧: warnings を返すだけで Decision に介入しない / 新: katsuragi_dispatch の Assignment 構築 + check_proposal_exceptions で代替。
        # （PortfolioBuilderAgent の initial mode は外部用に残置・朝バッチでは呼ばない）
        DAGNode(
            "trailing_check",
            run_trailing_check,
            depends_on=["sell_recommender"],
            timeout_s=300,
        ),
        # v2.10 Phase 1A-Step2 修正 (致命 1): trailing で登録された sell Decision
        # を即座に実行して Portfolio を close する（DB だけに残らないように）
        DAGNode(
            "close_due",
            run_close_due,
            depends_on=["trailing_check"],
            timeout_s=300,
        ),
        DAGNode(
            "pyramid_check",
            run_pyramid_check,
            depends_on=["close_due"],
            timeout_s=300,
        ),
        # v2.10 Phase J + G-1: auto モードのみ朝バッチで approved Decision を即 fill
        # 対象: trailing/pyramid が登録した status="approved" の Decision
        # 新規 buy 候補（materialize → magi_verify → katsuragi_dispatch 経由）は status="awaiting" のままで
        # 手動承認待ち（人間決済）または別ジョブで処理
        DAGNode(
            "auto_fill",
            run_auto_fill,
            depends_on=["pyramid_check"],
            timeout_s=600,
        ),
        DAGNode("link_topics", link_topics, depends_on=["auto_fill"], timeout_s=60),
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
