"""エージェント基底クラスと実行ラッパー（AGENT_SPECS.md §0・§7）。

- ``Agent``：全エージェントの基底。``execute`` に中核ロジックを実装する。
- ``execute_agent``：共通の前後処理（HALT チェック・予算チェック・analysis_logs 記録・
  例外ハンドリング）を担うラッパー。エージェントは必ずこれ経由で実行する。

設計判断（Task 1.3.3）：LangChain の自律ツールループは採用せず、エージェントは
MCP ツール + llm_call を明示シーケンスで呼ぶ（決定論・コスト管理・テスト容易性を優先。
§A-1 からの逸脱として PROGRESS に明記、差替可）。ツールアクセスは AgentContext 経由。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.llm.budget import BudgetGuard
from trading_agent.mcp_tools.base import MCPToolInput, MCPToolOutput
from trading_agent.models.analytics import AnalysisLog
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow


class AgentInput(MCPToolInput):
    """エージェント入力の基底。"""

    invocation_id: str
    dry_run: bool = False  # True なら結果を DB に書かない


class AgentOutput(MCPToolOutput):
    """エージェント出力の基底。"""

    invocation_id: str = ""
    summary: str = ""
    duration_ms: int = 0
    llm_cost_jpy: float = 0.0


class Agent[TIn: AgentInput](ABC):
    """全エージェントの基底クラス。"""

    name: str = ""
    description: str = ""
    required_tools: list[str] = []
    default_routing: str = "hot"  # "hot" / "cold" / "critical"

    @abstractmethod
    async def execute(self, agent_input: TIn) -> AgentOutput:
        """エージェントの中核ロジック。"""

    async def pre_check(self) -> bool:
        """実行前チェック（必要なツールが使えるか等）。既定 True。"""
        return True


def _abort(invocation_id: str, error: str, summary: str) -> AgentOutput:
    return AgentOutput(
        success=False,
        invocation_id=invocation_id,
        error=error,
        summary=summary,
        duration_ms=0,
        llm_cost_jpy=0.0,
    )


def _input_summary(agent_input: AgentInput) -> str:
    return str(agent_input.model_dump())[:500]


def _log_start(engine: Engine, agent_name: str, invocation_id: str, input_summary: str) -> None:
    with Session(engine) as session:
        session.add(
            AnalysisLog(
                agent=agent_name,
                invocation_id=invocation_id,
                started_at=utcnow(),
                input_summary=input_summary,
                output_summary="",
                status="running",
            )
        )
        session.commit()


def _log_end(engine: Engine, invocation_id: str, output: AgentOutput, status: str) -> None:
    with Session(engine) as session:
        row = session.exec(
            select(AnalysisLog).where(col(AnalysisLog.invocation_id) == invocation_id)
        ).first()
        if row is None:
            return
        row.ended_at = utcnow()
        row.duration_ms = output.duration_ms
        row.status = status
        row.output_summary = output.summary
        row.error_msg = output.error
        row.total_cost_jpy = output.llm_cost_jpy
        session.add(row)
        session.commit()


async def execute_agent[TIn: AgentInput](
    agent: Agent[TIn],
    agent_input: TIn,
    engine: Engine,
    *,
    halt_file: Path | None = None,
) -> AgentOutput:
    """共通処理付きでエージェントを実行する（AGENT_SPECS §7）。"""
    log = get_logger("agent").bind(agent=agent.name, invocation_id=agent_input.invocation_id)
    invocation_id = agent_input.invocation_id
    start = time.time()

    # v2.2 TASK-AB2: dry_run なら HALT も予算も bypass（DB に書かない実行なので安全）
    is_dry_run = bool(getattr(agent_input, "dry_run", False))

    # 緊急停止チェック（dry_run は通す）
    halt = halt_file if halt_file is not None else _settings_halt_file()
    if not is_dry_run and halt is not None and halt.exists():
        log.warning("agent_aborted_halt")
        return _abort(invocation_id, "HALT file present, agent execution aborted", "緊急停止中")
    if is_dry_run and halt is not None and halt.exists():
        log.info("agent_dry_run_bypass_halt")

    # 予算チェック（既に上限超過なら拒否。critical はバイパス。dry_run も bypass）
    # v2.5 TASK-AB1: ここでは 0.0（事前見積もりなし）で「現状超過してるかだけ」確認。
    # 個別 LLM 呼出の予算予測は llm_call.py で別途行う（v2.4 TASK-LC1）。
    if not is_dry_run:
        ok, reason = BudgetGuard(engine).can_proceed(0.0, agent.default_routing)
        if not ok:
            log.warning("agent_aborted_budget", reason=reason)
            return _abort(invocation_id, f"Budget exceeded: {reason}", "予算超過")

    _log_start(engine, agent.name, invocation_id, _input_summary(agent_input))

    try:
        output = await agent.execute(agent_input)
        output.invocation_id = invocation_id
        output.duration_ms = int((time.time() - start) * 1000)
        status = "success" if output.success else "failure"
    except Exception as exc:
        log.exception("agent_failed", error=str(exc))
        output = _abort(invocation_id, str(exc), f"実行失敗: {agent.name}")
        output.duration_ms = int((time.time() - start) * 1000)
        status = "failure"
    finally:
        # v2.5 TASK-AB3: 例外が出ても必ず AnalysisLog を閉じる（status="running" 残留防止）
        # try ブロック内で output が生成されない場合は abort 済みの output を使う
        if "output" not in locals():
            output = _abort(invocation_id, "unknown failure", f"未知の実行失敗: {agent.name}")
            output.duration_ms = int((time.time() - start) * 1000)
            status = "failure"

    _log_end(engine, invocation_id, output, status)
    return output


def _settings_halt_file() -> Path | None:
    try:
        from trading_agent.config import get_settings

        return get_settings().halt_file
    except Exception:
        return None
