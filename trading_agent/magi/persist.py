"""A-4：MAGI判断を実行経路（DAG）に貫通させ、検証結果を永続化する。

候補 → `Decision(status="verifying")` 生成（materialize_decisions）→ 各候補で
3審判→防御→統合→碇を回し、judge_verdict×3 / split_pattern / verification / commander_rec を
**decision_id 付きで保存**（magi_verify）。完了で status を verifying→awaiting に進め、
`verification.default_hold` を碇の構え（gendo_stance）に反映する。

設計：
- データ取得は `call_tool`（MCPHost 経由）を注入＝本番は実ツール／テストはモックで同経路。
- 判定は既定で**決定論**（run_judges＝コスト0・再現可能）。CASPER の Sonnet 解釈は
  llm_tool 注入時のみオプトインで上書き（バッチ既定はOFF。UIは build_snapshot 側で格上げ）。
- 冪等：同日同銘柄の Decision は再利用し、検証4表は decision_id 単位で作り直す（再実行で非重複）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, delete, select

from trading_agent.magi import classify_split, command, run_judges, verify
from trading_agent.magi.casper_llm import casper_llm
from trading_agent.magi.commander import CommanderResult
from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.integration import SplitResult
from trading_agent.magi.policy import voting
from trading_agent.mcp_tools.base import MCPToolInput, MCPToolOutput
from trading_agent.mcp_tools.disclosure import DisclosureInput
from trading_agent.mcp_tools.fundamentals import FundamentalsInput
from trading_agent.mcp_tools.llm_call import LLMCallTool
from trading_agent.mcp_tools.news import NewsInput
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import CommanderRec, JudgeVerdict, SplitPattern, Verification
from trading_agent.screening import (
    Financials,
    assess_credibility,
    derive_earnings_signal_tags,
    melchior_accrual_counter,
    melchior_credibility_counter,
)
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import today_jst, utcnow

_log = get_logger("magi_persist")

# ticker → (3審判, 割れ方, 防御結果, 碇推奨)
JudgeBundle = tuple[list[JudgeVerdict], SplitResult, VerificationResult, CommanderResult]
JudgeFn = Callable[[str], Awaitable[JudgeBundle]]
CallTool = Callable[[str, MCPToolInput], Awaitable[MCPToolOutput]]
FinancialsFetcher = Callable[[str], Financials | None]


def materialize_decisions(
    engine: Engine, candidates: list[str], *, action: str = "buy"
) -> list[int]:
    """候補ティッカー → 当日の `Decision(status="verifying")`。同日同銘柄は再利用（冪等）。

    v2.10: cancelled な過去 Decision は再利用しない（cleanup_for_fresh_run 後の真の fresh run 実現）。
    """
    today = today_jst()
    ids: list[int] = []
    with Session(engine, expire_on_commit=False) as session:
        for ticker in candidates:
            existing = session.exec(
                select(Decision)
                .where(col(Decision.date) == today)
                .where(col(Decision.ticker) == ticker)
                .where(col(Decision.action) == action)
                .where(col(Decision.status) != "cancelled")
            ).first()
            if existing is not None:
                if existing.id is not None:
                    ids.append(existing.id)
                continue
            row = Decision(date=today, ticker=ticker, action=action, status="verifying")
            session.add(row)
            session.commit()
            session.refresh(row)
            if row.id is not None:
                ids.append(row.id)
    return ids


def pending_decision_ids(
    engine: Engine, *, statuses: tuple[str, ...] = ("verifying",)
) -> list[int]:
    """当日の未検証 decision の id（DAG ハンドオフ用＝DB経由）。"""
    today = today_jst()
    with Session(engine) as session:
        rows = session.exec(
            select(Decision)
            .where(col(Decision.date) == today)
            .where(col(Decision.status).in_(statuses))
        ).all()
    return [r.id for r in rows if r.id is not None]


def derive_gendo_stance(verdicts: list[JudgeVerdict], *, default_hold: bool) -> str:
    """碇の構え。合意・確信度は投票審判（業績・文脈）のみで数える（株価=投票外）。割れ/保留は弱める。"""
    voting_v = voting(verdicts)
    actionable = [v for v in voting_v if v.verdict != "na"]
    buys = sum(1 for v in actionable if v.verdict == "buy")
    if not actionable:
        return "静観"
    if buys == len(voting_v) and not default_hold:
        return "推し"
    if buys >= 1:
        return "要検討"
    return "静観"


def persist_bundle(engine: Engine, decision_id: int, ticker: str, bundle: JudgeBundle) -> bool:
    """検証4表を decision_id 付きで作り直し、decision を verifying→awaiting に進める。

    Returns:
        default_hold（決裁既定が保留か）。
    """
    verdicts, split, vr, cmd = bundle
    asof = [v.data_asof for v in verdicts if v.data_asof is not None]
    data_asof = max(asof) if asof else None

    with Session(engine, expire_on_commit=False) as session:
        # 冪等：この decision の既存検証行を消してから入れ直す
        for model in (JudgeVerdict, SplitPattern, Verification, CommanderRec):
            session.exec(delete(model).where(col(model.decision_id) == decision_id))

        for v in verdicts:
            v.id = None
            v.decision_id = decision_id
            session.add(v)
        session.add(
            SplitPattern(
                decision_id=decision_id,
                ticker=ticker,
                agree_count=split.agree_count,
                total=split.total,
                label=split.label,
                interpretation=split.interpretation,
            )
        )
        session.add(
            Verification(
                decision_id=decision_id,
                ticker=ticker,
                figures_checked=vr.figures_checked,
                unverified_claims=vr.unverified_claims,
                credibility_flag=vr.credibility_flag,
                time_ok=vr.time_ok,
                gendo_compliant=vr.gendo_compliant,
                default_hold=vr.default_hold,
                data_asof=data_asof,
            )
        )
        session.add(
            CommanderRec(
                decision_id=decision_id,
                ticker=ticker,
                recommendation=cmd.recommendation,
                counter_argument=cmd.counter_argument,
                magi_compliant=cmd.magi_compliant,
                src_note=cmd.src_note,
            )
        )

        decision = session.get(Decision, decision_id)
        if decision is not None:
            decision.status = "awaiting"  # 検証完了→人間の決裁待ち
            decision.gendo_stance = derive_gendo_stance(verdicts, default_hold=vr.default_hold)
            decision.verified_at = utcnow()
            session.add(decision)
        session.commit()
    return vr.default_hold


async def magi_verify(
    engine: Engine,
    decision_ids: list[int],
    judge_fn: JudgeFn,
    *,
    earnings_sink: dict[str, tuple[list[str], dict]] | None = None,
) -> dict[str, int]:
    """各 decision で MAGI を回し、結果を永続化する。1件の失敗で全体を止めない。

    A prime: earnings_sink（judge が J-Quants `fin` 再利用で積んだ earnings 系タグ）があれば、
    その decision の entry_signal_tags に record-only でマージし、証拠を signal_tag_sources に残す。
    **verify 時点（fill 前・PIT 正）に刻む。過去 decision への backfill はしない**（codex 条件 #1）。
    """
    counts = {"verified": 0, "held": 0, "failed": 0}
    for decision_id in decision_ids:
        with Session(engine) as session:
            decision = session.get(Decision, decision_id)
            ticker = decision.ticker if decision is not None else None
        if ticker is None:
            counts["failed"] += 1
            continue
        try:
            bundle = await judge_fn(ticker)
        except Exception as exc:  # 取得/判定の失敗は1件スキップ
            _log.warning("magi_verify_failed", ticker=ticker, error=str(exc))
            counts["failed"] += 1
            continue
        default_hold = persist_bundle(engine, decision_id, ticker, bundle)
        if earnings_sink is not None and ticker in earnings_sink:
            _apply_earnings_tags(engine, decision_id, earnings_sink[ticker])
        counts["held" if default_hold else "verified"] += 1
    return counts


def _apply_earnings_tags(
    engine: Engine, decision_id: int, earnings: tuple[list[str], dict]
) -> None:
    """A prime: earnings 系 signal_tags を Decision に record-only マージ（売買は変えない）。

    entry_signal_tags は union merge（既存 ZEELE タグ等を消さない）。signal_tag_sources に証拠を残す。
    """
    e_tags, e_evidence = earnings
    if not e_tags:
        return
    with Session(engine, expire_on_commit=False) as session:
        d = session.get(Decision, decision_id)
        if d is None:
            return
        existing = list(d.entry_signal_tags or [])
        merged = existing + [t for t in e_tags if t not in existing]
        if merged != existing:
            d.entry_signal_tags = merged
        if e_evidence:
            # codex B: 再verify で根拠が変わらないよう「最初に刻んだ証拠」を固定する。
            # setdefault で既存 tag の evidence は上書きしない + captured_at を付与。
            sources = dict(d.signal_tag_sources or {})
            stamped = utcnow().isoformat()
            changed = False
            for tag, ev in e_evidence.items():
                if tag not in sources:
                    sources[tag] = {**ev, "captured_at": stamped}
                    changed = True
            if changed:
                d.signal_tag_sources = sources
        session.add(d)
        session.commit()


def make_live_judge_fn(
    call_tool: CallTool,
    *,
    llm_tool: LLMCallTool | None = None,
    financials_fetcher: FinancialsFetcher | None = None,
    sector_lookup: Callable[[str], str | None] | None = None,
    earnings_sink: dict[str, tuple[list[str], dict]] | None = None,
) -> JudgeFn:
    """MCP（call_tool）で素材を集め、3審判→防御→統合→碇を回す judge_fn を作る。

    - llm_tool：CASPER を Sonnet 解釈に格上げ（既定OFF＝決定論・コスト0）。
    - financials_fetcher：2期財務で信用性(S5)を判定→ credibility_flag と MELCHIOR反証(S6) に反映
      （既定OFF＝ネット非依存・テストで注入）。粉飾/倒産疑いは default_hold へ寄せる。
    - earnings_sink：A prime。credibility 用に引いた J-Quants `fin` を再利用して導出した
      earnings 系 signal_tags（{ticker: (tags, evidence)}）をここに積む。magi_verify が
      Decision.entry_signal_tags に record-only でマージする（¥0・売買は変えない）。
    """

    async def judge(ticker: str) -> JudgeBundle:
        fund = await call_tool("fundamentals", FundamentalsInput(ticker=ticker))
        tech = await call_tool("technicals", TechnicalsInput(ticker=ticker))
        try:
            news = await call_tool("news", NewsInput(tickers=[ticker]))
        except Exception as exc:
            _log.warning("magi_news_failed", ticker=ticker, error=str(exc))
            news = None

        verdicts = run_judges(ticker, fundamentals=fund, technicals=tech, news=news)
        if llm_tool is not None:
            upgraded = await casper_llm(ticker, news=news, llm_tool=llm_tool)
            verdicts = [upgraded if v.judge == "CASPER" else v for v in verdicts]

        # S5b/S6：信用性フィルタを MELCHIOR 反証と防御層 credibility_flag に反映
        credibility_flag = "ok"
        if financials_fetcher is not None:
            sector = sector_lookup(ticker) if sector_lookup is not None else None
            disclosures = await _fetch_disclosures(call_tool, ticker)  # S4b：開示レッドフラグ
            credibility_flag, e_tags, e_evidence = _apply_credibility(
                ticker, verdicts, financials_fetcher, sector, disclosures
            )
            # A prime: earnings 系 signal_tags を sink に積む（magi_verify が Decision に record-only 付与）。
            if earnings_sink is not None and e_tags:
                earnings_sink[ticker] = (e_tags, e_evidence)

        split = classify_split(verdicts)
        vr = verify(verdicts, credibility_flag=credibility_flag)
        cmd = command(verdicts, split, vr)
        return verdicts, split, vr, cmd

    return judge


async def _fetch_disclosures(call_tool: CallTool, ticker: str) -> list[dict] | None:
    """開示（TDnet/EDINET）を best-effort 取得（D-14 レッドフラグ用・S4b）。失敗は None。"""
    try:
        out = await call_tool("disclosure", DisclosureInput(tickers=[ticker]))
    except Exception as exc:
        _log.warning("magi_disclosure_failed", ticker=ticker, error=str(exc))
        return None
    return getattr(out, "disclosures", None)


def _apply_credibility(
    ticker: str,
    verdicts: list[JudgeVerdict],
    fetcher: FinancialsFetcher,
    sector: str | None,
    disclosures: list[dict] | None = None,
) -> tuple[str, list[str], dict]:
    """2期財務＋開示→信用性。MELCHIOR の counter_within_domain を更新する。

    Returns: (credibility_flag, earnings_tags, earnings_evidence)。
    A prime: 既に引いている J-Quants `fin` を再利用して earnings 系 signal_tags を導出（¥0・新規fetch無し）。
    取得失敗時は ("ok", [], {})。
    """
    try:
        fin = fetcher(ticker)
    except Exception as exc:  # 取得失敗は信用性スキップ（ok・graceful）
        _log.warning("credibility_fetch_failed", ticker=ticker, error=str(exc))
        return "ok", [], {}
    if fin is None:
        return "ok", [], {}
    cred = assess_credibility(fin, sector=sector, disclosures=disclosures)
    # 信用性ゾーン由来＋利益の質(accrual)由来の反証を MELCHIOR に併記（S6）
    counter = [*melchior_credibility_counter(cred), *melchior_accrual_counter(fin)]
    if counter:
        for v in verdicts:
            if v.judge == "MELCHIOR":
                v.counter_within_domain = [*v.counter_within_domain, *counter]
    # A prime: 同じ fin から earnings 系 signal_tags を導出（record-only・売買は変えない）。
    e_tags, e_evidence = derive_earnings_signal_tags(fin)
    return cred.credibility_flag, e_tags, e_evidence
