"""portfolio / portfolio_snapshots テーブル。SYSTEM_DESIGN.md §2.3。"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class Portfolio(SQLModel, table=True):
    """現在の保有銘柄。moomoo と 5分ごとに同期。"""

    __tablename__ = "portfolio"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)

    # 取得情報
    buy_date: dt.date
    buy_price: float  # 元通貨
    qty: int
    currency: str  # "USD" / "JPY"

    # 戦略情報（買い推奨採用時にコピー）
    strategy_category: str  # "中期" / "長期" / "中期-長期" / "短期"
    target_period_days: int
    target_pct: float
    stop_loss_pct: float
    target_date: dt.date
    thesis: str
    thesis_checklist: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # ステータス
    status: str = Field(index=True)  # "active" / "closed" / "watching"
    closed_at: dt.datetime | None = None
    closed_price: float | None = None
    closed_reason: str | None = None  # "profit_taking" / "stop_loss" / "manual"

    # 同期情報
    moomoo_position_id: str | None = None
    last_synced_at: dt.datetime = Field(default_factory=utcnow)

    # ペーパー検証用：性格別 portfolio 振り分け（defender / aggressor / balanced）。
    # None は単一 portfolio 運用（旧挙動）として扱う。
    personality: str | None = Field(default=None, index=True)
    # v2.8: broker_mode 別管理（Paper / Live 並行運用）
    broker_mode: str = Field(default="paper", index=True)

    # v2.10 Phase 1A-Step2: ピラミッディング（段階エントリー）
    # planned_total_qty: 機別 initial_alloc 適用後の「予定総量」（None = 旧挙動・一括 fill）
    # current_alloc = qty / planned_total_qty で「現在の累積比率」を計算する
    planned_total_qty: int | None = None
    # v2.10 Phase 1A-Step2: 真の trailing stop 用の含み益ピーク（小数表記）
    # peak_pnl_pct = max(過去日次の (current - entry) / entry)
    # None = 未初期化（初回 trailing_check で現在値で初期化）
    peak_pnl_pct: float | None = None

    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class PortfolioSnapshot(SQLModel, table=True):
    """日次の資産集計（米国市場引け後）。"""

    __tablename__ = "portfolio_snapshots"

    date: dt.date = Field(primary_key=True)

    total_assets_jpy: float
    cash_jpy: float
    us_stocks_value_jpy: float
    jp_stocks_value_jpy: float
    satellite_value_jpy: float
    core_value_jpy: float

    usd_jpy_rate: float
    holding_count: int
    daily_pnl_jpy: float

    created_at: dt.datetime = Field(default_factory=utcnow)
