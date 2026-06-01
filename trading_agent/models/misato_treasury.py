"""MISATO 財務テーブル：ユーザー預かり金 + 4 機への配分状況。

「ユーザー → MISATO → 4 機」の財務フローを DB に乗せる。

- `MisatoTreasury` (singleton, id=1):
  ユーザーが渡した元本累計（seed_jpy）と直近更新時刻。
  入金で増え、リセットでゼロに戻る。配分で減らさない（配分は別表で管理）。

- `PilotAllocation` (4 機分・pilot_name PK):
  各機への配分済元本（allocated_jpy）。
  MISATO.dispatch(approve=True) で書き換わる。Personality.overlay_cash_jpy は
  「既定均等配分時の参照値」となり、実際の各機の元本はこの DB 値が優先される。
"""

from __future__ import annotations

import datetime as dt

from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class MisatoTreasury(SQLModel, table=True):
    """KATSURAGI 預かり金（v2.8: broker_mode 別に Paper / Live 並行運用）。

    複合 PK: id (1=Paper, 2=Live) で Paper / 本番が独立した残高を持つ。
    """

    __tablename__ = "misato_treasury"

    id: int | None = Field(default=1, primary_key=True)
    # v2.8: broker_mode と id の対応 (1=paper, 2=live)。レガシーデータは "paper" を入れる
    broker_mode: str = Field(default="paper", index=True)
    seed_jpy: float = 0.0
    deposit_count: int = 0
    last_deposit_at: dt.datetime | None = None
    master_auto_trade_until: dt.datetime | None = None
    updated_at: dt.datetime = Field(default_factory=utcnow)


class PilotAllocation(SQLModel, table=True):
    """各機の配分済元本 + 自動売買期限（v2.8: broker_mode 別）。

    複合 PK: pilot_name + broker_mode で Paper / Live を独立管理。
    """

    __tablename__ = "pilot_allocation"

    pilot_name: str = Field(primary_key=True)
    # v2.8: broker_mode を PK の一部にして Paper / Live 独立
    broker_mode: str = Field(primary_key=True, default="paper")
    allocated_jpy: float = 0.0
    auto_trade_until: dt.datetime | None = None
    updated_at: dt.datetime = Field(default_factory=utcnow)
