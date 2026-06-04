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
    # Phase C 段階大規模化（unlock 方式・codex 推奨）。
    # paper は口座総額 seed=¥100万 を固定で置き、実際に使える deploy 上限だけを段階解放する。
    # unlocked_budget_jpy: 現在解放済みの運用上限（¥10万 start → gate⑥通過で ¥100万 へ）。
    # target_ceiling_jpy : 解放上限（paper=¥100万）。0.0=未設定。
    # last_unlock_eval_n : 直近 unlock 時点の公式評価件数（同一 gate snapshot での多重 unlock 防止）。
    # 資本注入(deposit)で seed を増やさない → equity 曲線が注入で歪まず純粋に edge を測れる。
    unlocked_budget_jpy: float = 0.0
    target_ceiling_jpy: float = 0.0
    last_unlock_eval_n: int = 0
    updated_at: dt.datetime = Field(default_factory=utcnow)


class TreasuryInjection(SQLModel, table=True):
    """paper budget unlock の台帳（codex 推奨の unlock 方式）。

    口座総額 seed は固定（¥100万）なので「資本注入」ではなく「deploy 上限の段階解放」を記録する。
    amount_jpy=今回の解放差分 / tier_after_jpy=解放後の運用上限 / reason=解放理由。
    現運用規模 current_risk_budget = MisatoTreasury.unlocked_budget_jpy（解放済み上限）で読む
    （台帳は監査履歴）。equity 曲線は口座総額固定なので注入で歪まない。
    """

    __tablename__ = "treasury_injection"

    id: int | None = Field(default=None, primary_key=True)
    broker_mode: str = Field(default="paper", index=True)
    amount_jpy: float = 0.0  # 今回の解放差分（unlock delta）
    reason: str = ""  # "phase_c_start_unlock" / "gate_pass_unlock" / "manual" 等
    tier_after_jpy: float = 0.0  # 解放後の運用上限（unlocked cap after）
    created_at: dt.datetime = Field(default_factory=utcnow)


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
