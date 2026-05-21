"""market_data_cache / earnings_calendar テーブル。SYSTEM_DESIGN.md §2.3。"""

import datetime as dt

from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class MarketDataCache(SQLModel, table=True):
    """価格キャッシュ（メモリキャッシュの永続化バックアップ）。"""

    __tablename__ = "market_data_cache"

    ticker: str = Field(primary_key=True)

    current_price: float
    open_price: float
    high_today: float
    low_today: float
    prev_close: float
    volume_today: float
    price_change_today: float
    price_change_pct_today: float

    bid: float | None = None
    ask: float | None = None

    market_status: str  # "open" / "closed" / "pre" / "post"

    as_of: dt.datetime = Field(default_factory=utcnow)
    source: str = "moomoo"


class EarningsCalendar(SQLModel, table=True):
    """決算予定。"""

    __tablename__ = "earnings_calendar"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    earnings_date: dt.date = Field(index=True)
    earnings_time: str  # "before_open" / "after_close" / "during" / "unknown"
    fiscal_quarter: str | None = None
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    eps_actual: float | None = None
    revenue_actual: float | None = None
    source: str
    created_at: dt.datetime = Field(default_factory=utcnow)
