"""screening_results / buy_signals / sell_signals / scenarios テーブル。

SYSTEM_DESIGN.md §2.3（エージェント出力カテゴリ）。
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class ScreeningResult(SQLModel, table=True):
    """スクリーニング結果（銘柄ごとの V字 / テーマスコア）。"""

    __tablename__ = "screening_results"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    screened_at: dt.datetime = Field(index=True)

    v_shape_score: float
    theme_score: float
    composite_score: float  # 上記2つの最大値
    screening_passed: bool

    v_shape_details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    theme_details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: dt.datetime = Field(default_factory=utcnow)


class BuySignal(SQLModel, table=True):
    """買いレコメンド（5軸スコア + 3シナリオ）。"""

    __tablename__ = "buy_signals"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    created_at: dt.datetime = Field(default_factory=utcnow, index=True)

    # スコア（5軸の総合）
    score: int  # 0-100
    fundamental_score: float
    technical_score: float
    news_sentiment_score: float
    strategy_fit_score: float
    ai_confidence: float

    # 予測
    expected_return: float
    win_rate: float
    target_period_days: int
    target_price: float
    entry_price: float
    stop_loss_price: float

    # 戦略
    strategy_category: str

    # 詳細（JSON）
    thesis_checklist: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    reasons: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    risks: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    scenarios: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # メタ
    recommended_amount_jpy: int
    is_active: bool = True  # 翌日には false、新しいバッチで上書き


class SellSignal(SQLModel, table=True):
    """売りレコメンド（利確 / 損切り）。"""

    __tablename__ = "sell_signals"

    id: int | None = Field(default=None, primary_key=True)
    # NOTE: portfolio.ticker は非ユニーク列。Phase 1 の SQLite は FK 非強制のため
    #       create/insert は通る（SYSTEM_DESIGN.md の定義に従う）。
    ticker: str = Field(foreign_key="portfolio.ticker", index=True)
    created_at: dt.datetime = Field(default_factory=utcnow, index=True)

    signal_type: str  # "stop_loss" / "time_exit"（利確トリムは廃止・B'）

    # スコア
    score: int  # 0-100
    # 利確
    target_achievement_score: float | None = None
    scenario_achievement_score: float | None = None
    technical_warning_score: float | None = None
    # 損切り
    scenario_break_score: float | None = None
    loss_magnitude_score: float | None = None
    negative_news_score: float | None = None
    # 共通
    ai_confidence: float

    # 詳細（JSON）
    reasons: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    risks: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    recommended_action: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    is_active: bool = True


class Scenario(SQLModel, table=True):
    """保有銘柄ごとのシナリオ進捗（1銘柄1行）。"""

    __tablename__ = "scenarios"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="portfolio.ticker", unique=True, index=True)

    scenario_health: float  # 0.0-1.0
    scenario_status: str  # "intact" / "weakening" / "broken"

    checklist_progress: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    evaluation_notes: str

    latest_update: dt.datetime = Field(default_factory=utcnow)
