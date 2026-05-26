"""theses テーブル（X-2A：投資テーゼ ライフサイクル管理）。

Inspired by tradermonty/claude-trading-skills/trader-memory-core (MIT).
Concept-only borrowing per D-21: schema translated to tradesupport's principles.

ライフサイクル：IDEA → ENTRY_READY → ACTIVE → CLOSED
（IDEA = screening 等から発議 / ENTRY_READY = プラン確定 / ACTIVE = 約定済 / CLOSED = 退場）

設計原則：
- **SCORE: NONE 構造的強制**：`target_price` フィールド自体が存在しない。
  bull/base/bear シナリオの数値を保存する場所がないので、原則違反の余地がコード上ゼロ。
- **D-23 8数値準拠**：`risk_r` 既定 ¥2,000（1R）／`stop_price` は10-15%以内が運用想定
- **D-02 / X-1 来歴必須**：`source_refs` と `data_asof` を全テーゼに付与
- **MAGI 結線**：`magi_decision_ids` で複数の MAGI 判定をテーゼに紐付け
- **append-only audit**：`lifecycle_log` で状態遷移の履歴を保持
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class ThesisStatus(StrEnum):
    """ライフサイクル状態（X-2A spec §2.3）。"""

    IDEA = "IDEA"
    ENTRY_READY = "ENTRY_READY"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ThesisType(StrEnum):
    """テーゼの型（claude-trading-skills の thesis_type を JP 文脈に適合）。"""

    DIVIDEND_INCOME = "dividend_income"
    GROWTH_MOMENTUM = "growth_momentum"
    MEAN_REVERSION = "mean_reversion"
    TURNAROUND = "turnaround"  # tradesupport-original（vs CTS の earnings_drift）
    PIVOT_BREAKOUT = "pivot_breakout"


# 順序関係（厳格な前進のみ・後戻り禁止）
THESIS_STATUS_ORDER: dict[ThesisStatus, int] = {
    ThesisStatus.IDEA: 0,
    ThesisStatus.ENTRY_READY: 1,
    ThesisStatus.ACTIVE: 2,
    ThesisStatus.CLOSED: 3,
}


class Thesis(SQLModel, table=True):
    """投資テーゼ（保有思想のライフサイクル）。"""

    __tablename__ = "theses"

    thesis_id: str = Field(primary_key=True)  # ULID or UUID
    ticker: str = Field(index=True)
    thesis_type: ThesisType
    thesis_statement: str
    status: ThesisStatus = Field(default=ThesisStatus.IDEA, index=True)

    # === Plan（D-23 8数値準拠）===
    # 注：target_price 列は意図的に存在しない（SCORE: NONE 構造的強制・D-06）
    entry_price: float | None = None
    stop_price: float | None = None
    risk_r: float | None = None  # 既定 ¥2,000（D-23 1R）
    size_shares: int = 0

    # === Actual ===
    actual_entry_price: float | None = None
    actual_entry_date: dt.date | None = None
    actual_shares: float | None = None  # 単元未満許可（moomoo 1株単位）
    actual_exit_price: float | None = None
    actual_exit_date: dt.date | None = None
    realized_return_pct: float | None = None

    # === Provenance（D-02 / X-1）===
    source_refs: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    data_asof: str = ""

    # === Audit trail（append-only）===
    lifecycle_log: list[dict] = Field(default_factory=list, sa_column=Column(JSON))

    # === MAGI 結線 ===
    magi_decision_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))

    # === レビュースケジュール ===
    review_due_date: dt.date | None = None
    review_count: int = 0

    # === Postmortem（CLOSED 時のみ・X-2D で本格運用）===
    postmortem_classification: str | None = None  # TRUE_POSITIVE/FALSE_POSITIVE/MISSED_OPPORTUNITY/REGIME_MISMATCH
    postmortem_lessons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


__all__ = [
    "Thesis",
    "ThesisStatus",
    "ThesisType",
    "THESIS_STATUS_ORDER",
]
