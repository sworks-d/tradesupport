"""楽天証券 broker（v2.10）。

現状: 楽天証券は公式 REST API を提供していないため、
**自動執行は不可、手動発注 + mark_filled CLI による Portfolio 記録**。

将来: 楽天が API を提供 or MarketSpeed II RSS 経由の自動化を実装する場合、
このモジュールの実装を埋めれば broker_provider="rakuten" の経路で接続される。

インターフェース:
  - get_positions() → Portfolio テーブルから broker_mode="live" の active を返す（mark_filled 経由で記録済）
  - get_account()    → MisatoTreasury (broker_mode="live") から残高計算

これにより、将来 API 接続が可能になった時点で実発注経路を追加する余地を残しておく。
"""

from __future__ import annotations

from typing import Any

from trading_agent.brokers.base import Account, Position
from trading_agent.utils.logger import get_logger

_log = get_logger("brokers.rakuten")


class RakutenBroker:
    """楽天証券 broker（手動発注 + DB 記録ベース）。

    将来 API 接続実装時はこのクラスを拡張。
    """

    def __init__(self, *, engine: Any = None, broker_mode: str = "live") -> None:
        self.engine = engine
        self.broker_mode = broker_mode

    @classmethod
    def from_settings(cls, settings: Any) -> "RakutenBroker":
        # 将来: settings から楽天 API key 等を読む
        # 現状: 設定不要（手動発注 + DB ベース）
        return cls()

    def get_positions(self) -> list[Position]:
        """DB の Portfolio から live モードの active 保有を返す（mark_filled 経由で記録）。"""
        if self.engine is None:
            return []
        from sqlmodel import Session, col, select

        from trading_agent.models.portfolio import Portfolio

        with Session(self.engine) as s:
            rows = list(
                s.exec(
                    select(Portfolio)
                    .where(col(Portfolio.status) == "active")
                    .where(col(Portfolio.broker_mode) == self.broker_mode)
                ).all()
            )
        positions: list[Position] = []
        for p in rows:
            positions.append(
                Position(
                    code=p.ticker,
                    qty=int(p.qty or 0),
                    cost_price=float(p.buy_price or 0.0),
                    currency=p.currency or "JPY",
                    nominal_price=None,
                )
            )
        return positions

    def get_account(self) -> Account | None:
        """MisatoTreasury (broker_mode="live") から残高を計算。

        実装フロー:
          cash = Treasury.seed_jpy - sum(active Portfolio.buy_price × qty)
        """
        if self.engine is None:
            return None
        from trading_agent.brokers import _active_portfolio_cost_jpy
        from trading_agent.portfolio.misato import treasury_view

        tv = treasury_view(self.engine, self.broker_mode)
        seed = float(tv.get("seed_jpy") or 0)
        cost = _active_portfolio_cost_jpy(
            self.engine, broker_mode=self.broker_mode
        )
        cash = seed - cost
        total = seed  # cash + 含み（current_price ベースは別途）
        return Account(cash=cash, total_assets=total, currency="JPY")

    def place_order(self, *args: Any, **kwargs: Any) -> dict:
        """楽天証券は公式 API なし → 自動発注不可。

        将来 MarketSpeed II RSS 経由 or kabu.com 移行で実装。
        """
        return {
            "ok": False,
            "error": "rakuten_no_api: 楽天証券は公式 API なし。手動発注 + mark_filled で記録してください。",
        }
