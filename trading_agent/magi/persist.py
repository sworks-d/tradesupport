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
from trading_agent.mcp_tools.base import MCPToolInput, MCPToolOutput
from trading_agent.mcp_tools.fundamentals import FundamentalsInput
from trading_agent.mcp_tools.llm_call import LLMCallTool
from trading_agent.mcp_tools.news import NewsInput
from trading_agent.mcp_tools.technicals import TechnicalsInput
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import CommanderRec, JudgeVerdict, SplitPattern, Verification
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("magi_persist")

# ticker → (3審判, 割れ方, 防御結果, 碇推奨)
JudgeBundle = tuple[list[JudgeVerdict], SplitResult, VerificationResult, CommanderResult]
JudgeFn = Callable[[str], Awaitable[JudgeBundle]]
CallTool = Callable[[str, MCPToolInput], Awaitable[MCPToolOutput]]


def materialize_decisions(
    engine: Engine, candidates: list[str], *, action: str = "buy"
) -> list[int]:
    """候補ティッカー → 当日の `Decision(status="verifying")`。同日同銘柄は再利用（冪等）。"""
    today = utcnow().date()
    ids: list[int] = []
    with Session(engine, expire_on_commit=False) as session:
        for ticker in candidates:
            existing = session.exec(
                select(Decision)
                .where(col(Decision.date) == today)
                .where(col(Decision.ticker) == ticker)
                .where(col(Decision.action) == action)
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
    today = utcnow().date()
    with Session(engine) as session:
        rows = session.exec(
            select(Decision)
            .where(col(Decision.date) == today)
            .where(col(Decision.status).in_(statuses))
        ).all()
    return [r.id for r in rows if r.id is not None]


def derive_gendo_stance(verdicts: list[JudgeVerdict], *, default_hold: bool) -> str:
    """碇の構え。割れ/保留は弱める（推奨はするが決めない）。"""
    actionable = [v for v in verdicts if v.verdict != "na"]
    buys = sum(1 for v in actionable if v.verdict == "buy")
    if not actionable:
        return "静観"
    if buys == len(verdicts) and not default_hold:
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


async def magi_verify(engine: Engine, decision_ids: list[int], judge_fn: JudgeFn) -> dict[str, int]:
    """各 decision で MAGI を回し、結果を永続化する。1件の失敗で全体を止めない。"""
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
        counts["held" if default_hold else "verified"] += 1
    return counts


def make_live_judge_fn(call_tool: CallTool, *, llm_tool: LLMCallTool | None = None) -> JudgeFn:
    """MCP（call_tool）で素材を集め、3審判→防御→統合→碇を回す judge_fn を作る。

    llm_tool を渡すと CASPER のみ Sonnet 解釈に格上げ（既定はOFF＝決定論・コスト0・再現可能）。
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

        split = classify_split(verdicts)
        vr = verify(verdicts)
        cmd = command(verdicts, split, vr)
        return verdicts, split, vr, cmd

    return judge
