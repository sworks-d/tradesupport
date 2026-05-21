"""analysis_logs / cost_logs / health_checks テーブル（運用カテゴリ）。

SYSTEM_DESIGN.md §2.3。
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class AnalysisLog(SQLModel, table=True):
    """エージェント実行ログ（invocation_id でトレース）。"""

    __tablename__ = "analysis_logs"

    id: int | None = Field(default=None, primary_key=True)
    agent: str
    invocation_id: str = Field(index=True)  # グルーピング用

    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    duration_ms: int | None = None

    input_summary: str
    input_full: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    output_summary: str
    output_full: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    status: str  # "running" / "success" / "failure" / "timeout"
    error_msg: str | None = None

    llm_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_jpy: float = 0.0


class CostLog(SQLModel, table=True):
    """API 消費ログ（LLM 呼び出しごと）。"""

    __tablename__ = "cost_logs"

    id: int | None = Field(default=None, primary_key=True)
    timestamp: dt.datetime = Field(default_factory=utcnow, index=True)
    date: dt.date = Field(index=True)

    model: str  # "claude-sonnet-4-6" / "claude-opus-4-7" / "ollama:llama3.1" 等
    agent: str
    purpose: str  # "screening" / "analysis" / "summarization" 等

    tokens_in: int
    tokens_out: int
    cost_usd: float
    cost_jpy: float

    invocation_id: str | None = Field(default=None, index=True)


class HealthCheck(SQLModel, table=True):
    """健康チェック結果（5分ごと）。"""

    __tablename__ = "health_checks"

    id: int | None = Field(default=None, primary_key=True)
    component: str  # "moomoo_opend" / "anthropic_api" / "ollama" 等
    status: str  # "ok" / "degraded" / "down"
    checked_at: dt.datetime = Field(default_factory=utcnow, index=True)
    response_time_ms: int | None = None
    error_msg: str | None = None
