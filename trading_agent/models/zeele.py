"""zeele_state テーブル：ZEELE 車線の銘柄滞留状態。

ZEELE は「数週〜月単位で熟成中の攻め候補プール」。1日のスクリーニング bump で
出入りせず、複数週の継続性が確認できたものだけが entry し、weeks をカウントする。

1行 = 1ティッカー。screening_results を週次で集計し、`zeele_curator` agent が upsert する。
"""

from __future__ import annotations

import datetime as dt

from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class ZeeleState(SQLModel, table=True):
    """ZEELE 滞留中の1銘柄の状態。

    - `entered_at`: 初めて ZEELE 入りした日（3週連続入賞を確認した最初の朝）
    - `weeks_in_zeele`: 滞留週数（カウントは週次で進む。日次ではない）
    - `last_screened_at`: 直近で screening_results に登場した日時
    - `consecutive_weeks`: 直近の連続入賞週数（途切れたら 0 に戻す）
    - `preset`: 最後に上位入賞した preset（value / growth / momentum / contrarian / alpha /
       pullback / growth-value）
    - `structural_thesis`: ZEELE 入り時に確定した構造的根拠（narrative）
    - `is_active`: ZEELE プールから出たら false（4週連続で screening に入賞しなかった等）
    """

    __tablename__ = "zeele_state"

    ticker: str = Field(primary_key=True, foreign_key="universe.ticker")
    entered_at: dt.date
    weeks_in_zeele: int = 0
    consecutive_weeks: int = 0  # 直近の連続入賞カウント（3で entry 確定、0 でリセット）
    last_screened_at: dt.datetime
    preset: str = ""  # 最後の入賞 preset
    structural_thesis: str = ""  # 入賞理由（narrative）
    reference_score: float = 0.0  # 最後の composite_score
    is_active: bool = Field(default=True, index=True)
    exited_at: dt.date | None = None
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)
