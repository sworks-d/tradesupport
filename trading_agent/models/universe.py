"""universe テーブル（銘柄母集団）。SYSTEM_DESIGN.md §2.3。"""

import datetime as dt

from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class Universe(SQLModel, table=True):
    """スクリーニング対象の銘柄母集団（US + JP）。"""

    __tablename__ = "universe"

    ticker: str = Field(primary_key=True)
    name: str
    name_en: str | None = None
    name_ja: str | None = None  # JP 銘柄の日本語名（JPX 公式 Excel 由来）
    market: str  # "US" / "JP"
    sector: str
    industry: str | None = None
    market_cap: float  # 元通貨
    market_cap_jpy: float  # JPY 換算
    avg_volume_30d: float
    is_active: bool = True
    listed_date: dt.date | None = None
    delisted_date: dt.date | None = None
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)
