"""予算管理とコスト記録（SYSTEM_DESIGN.md §5.5）。

cost_logs から当日/当月の消費を集計し、settings の上限と比較する。critical は警告のみで通す。
全 LLM 呼び出しを cost_logs に記録する。
"""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from trading_agent.llm.router import estimate_cost_jpy
from trading_agent.models.analytics import CostLog
from trading_agent.models.settings import Setting
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

# 【たたき台】USD 換算用の固定レート（cost_usd 算出用。Phase 1 簡易）
USD_JPY_FALLBACK = 150.0

_DEFAULT_DAILY_JPY = 500
_DEFAULT_MONTHLY_JPY = 5000


def _get_int_setting(session: Session, key: str, default: int) -> int:
    row = session.get(Setting, key)
    if row is None:
        return default
    try:
        return int(json.loads(row.value))
    except (ValueError, TypeError):
        return default


class BudgetGuard:
    """LLM 呼び出し前の予算チェック。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def today_cost_jpy(self) -> float:
        today = utcnow().date()
        with Session(self._engine) as session:
            stmt = select(func.coalesce(func.sum(CostLog.cost_jpy), 0.0)).where(
                CostLog.date == today
            )
            return float(session.exec(stmt).one())

    def month_cost_jpy(self) -> float:
        month_start = utcnow().date().replace(day=1)
        with Session(self._engine) as session:
            stmt = select(func.coalesce(func.sum(CostLog.cost_jpy), 0.0)).where(
                CostLog.date >= month_start
            )
            return float(session.exec(stmt).one())

    def limits_jpy(self) -> tuple[int, int]:
        with Session(self._engine) as session:
            daily = _get_int_setting(session, "daily_budget_jpy", _DEFAULT_DAILY_JPY)
            monthly = _get_int_setting(session, "monthly_budget_jpy", _DEFAULT_MONTHLY_JPY)
        return daily, monthly

    def can_proceed(self, estimated_jpy: float, routing_hint: str | None) -> tuple[bool, str]:
        """予算内かを判定する。critical は警告のみで通す（SYSTEM_DESIGN §5.5）。"""
        daily, monthly = self.limits_jpy()
        today = self.today_cost_jpy()
        month = self.month_cost_jpy()

        if routing_hint == "critical":
            if today + estimated_jpy > daily:
                get_logger("llm").warning(
                    "budget_exceeded_critical_bypass", today=today, estimated=estimated_jpy
                )
            return True, "critical_bypass"

        if today + estimated_jpy > daily:
            return False, f"daily budget exceeded (¥{today:.1f}+¥{estimated_jpy:.1f} > ¥{daily})"
        if month + estimated_jpy > monthly:
            return (
                False,
                f"monthly budget exceeded (¥{month:.1f}+¥{estimated_jpy:.1f} > ¥{monthly})",
            )
        return True, "ok"


def record_cost(
    engine: Engine,
    *,
    model: str,
    agent: str,
    purpose: str,
    tokens_in: int,
    tokens_out: int,
    invocation_id: str | None = None,
) -> float:
    """1回の LLM 呼び出しを cost_logs に記録し、コスト（円）を返す。"""
    now = utcnow()
    cost_jpy = estimate_cost_jpy(model, tokens_in, tokens_out)
    row = CostLog(
        timestamp=now,
        date=now.date(),
        model=model,
        agent=agent,
        purpose=purpose,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_jpy / USD_JPY_FALLBACK,
        cost_jpy=cost_jpy,
        invocation_id=invocation_id,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
    return cost_jpy
