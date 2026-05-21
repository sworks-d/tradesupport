"""batch_states テーブル（朝バッチの実行状態）。ORCHESTRATION.md §9.1。"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class BatchState(SQLModel, table=True):
    """1回のバッチ実行の状態（成否・ノード別状況）。"""

    __tablename__ = "batch_states"

    invocation_id: str = Field(primary_key=True)
    batch_type: str  # "morning" / "manual_refresh" 等
    status: str  # "running" / "success" / "failed" / "partial"
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    node_status: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str = ""
    errors: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
