"""topics / manual_inputs テーブル（情報カテゴリ）。SYSTEM_DESIGN.md §2.3。"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class Topic(SQLModel, table=True):
    """収集ニュース（AI 判断の根拠となる透明性データ）。"""

    __tablename__ = "topics"

    id: int | None = Field(default=None, primary_key=True)
    collected_at: dt.datetime = Field(default_factory=utcnow, index=True)

    # ソース
    source: str  # "Bloomberg" / "Reuters" / "EDINET" 等
    source_url: str
    additional_sources: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # 内容
    category: str = Field(index=True)  # "macro" / "sector" / "stock"
    importance: str = Field(index=True)  # "high" / "medium" / "low"
    headline: str
    summary: str
    original_text: str | None = None
    original_text_hash: str  # デデュープ用

    # 影響
    affected_tickers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    impact_text: str
    linked_decisions: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    # メタ
    fetched_by: str  # "morning_batch" / "manual_input"
    importance_judged_by: str  # "rule" / "llm" / "user"

    is_archived: bool = Field(default=False, index=True)


class ManualInput(SQLModel, table=True):
    """手動投入テキストとその分析結果（X 代替）。"""

    __tablename__ = "manual_inputs"

    id: int | None = Field(default=None, primary_key=True)
    submitted_at: dt.datetime = Field(default_factory=utcnow)

    input_text: str
    input_url: str | None = None
    input_type: str  # "text" / "url" / "ticker"

    # 分析結果
    analyzed_at: dt.datetime
    result_summary: str
    affected_tickers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    impact_direction: str  # "positive" / "negative" / "neutral"
    impact_magnitude: str  # "large" / "medium" / "small"
    recommended_action: str | None = None

    # ユーザーアクション
    added_to_topics: bool = False
    topic_id: int | None = None

    # メタ
    llm_model: str
    llm_cost_jpy: float
