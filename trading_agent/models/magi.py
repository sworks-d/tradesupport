"""MAGI 検証層のテーブル（judge_verdict ほか）。B0_DIFF_PLAN.md §3.2。

3審判の独立判定（judge_verdict）を保持する。
split_pattern / commander_rec / verification は B4/B5/B3 で追加する。
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class JudgeVerdict(SQLModel, table=True):
    """審判1人の独立検証結果（MELCHIOR / BALTHASAR / CASPER）。

    数値はコード取得の実データのみ、可否・確信度・根拠文もコードで決定する（この段はLLM不使用）。
    source_refs / data_asof は入力ソース（MCP）から引き継ぐ＝防御層(B3)の機械照合の土台。
    """

    __tablename__ = "judge_verdict"

    id: int | None = Field(default=None, primary_key=True)
    decision_id: int | None = Field(default=None, foreign_key="decisions.id", index=True)
    ticker: str = Field(index=True)

    judge: str  # MELCHIOR / BALTHASAR / CASPER
    verdict: str  # buy / sell / hold / warn / na
    confidence: str  # 高 / 中 / 低 / na（定性ラベル D-11。総合スコアは出さない）
    reason: str  # コード生成の根拠文（数値・時点ベース）

    source_refs: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    data_asof: dt.datetime | None = None

    created_at: dt.datetime = Field(default_factory=utcnow)
