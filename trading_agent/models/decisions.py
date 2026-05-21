"""decisions テーブル（レコメンド + 採否 + 評価の履歴）。SYSTEM_DESIGN.md §2.3。"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class Decision(SQLModel, table=True):
    """1つのレコメンドとその後の追跡（採否・評価）。"""

    __tablename__ = "decisions"

    id: int | None = Field(default=None, primary_key=True)

    # 何のレコメンドか
    date: dt.date = Field(index=True)
    ticker: str = Field(index=True)
    action: str  # "buy" / "sell_profit" / "sell_loss"
    source_signal_id: int | None = None  # buy_signals.id or sell_signals.id

    # レコメンド時のスナップショット
    score: int
    expected_return: float
    target_period_days: int
    scenarios: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    thesis_at_decision: str

    # ユーザーの反応
    user_action: str | None = None  # "adopted" / "skipped" / "modified" / "deferred"
    user_acted_at: dt.datetime | None = None
    user_note: str | None = None

    # 評価
    evaluation_date: dt.date  # date + target_period_days
    actual_return: float | None = None
    hit_or_miss: str = Field(default="pending", index=True)  # "hit"/"miss"/"neutral"/"pending"
    evaluated_at: dt.datetime | None = None

    # 紐付いたトピックス
    supporting_topic_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: dt.datetime = Field(default_factory=utcnow)
