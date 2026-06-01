"""auカブコム証券 broker（v2.10・Phase 2 移行用 placeholder）。

将来の **完全自動売買** 経路。kabu STATION API（公式 REST/WebSocket）で接続。

現状: **未実装** stub。broker_provider="kabucom" を選択した時点でこのクラスを実装する。

将来実装すべきインターフェース:
  - get_positions()  → kabu STATION /position から保有を取得
  - get_account()    → kabu STATION /wallet/cash から残高を取得
  - place_order()    → kabu STATION /sendorder で発注（プチ株 / 単元株）
  - cancel_order()   → 注文取消
  - get_realtime_quote() → リアルタイム配信（WebSocket）

kabu STATION の仕様:
  - 公式 REST + WebSocket（PUSH 配信）
  - 月額無料（信用取引 or 月10万円約定の条件達成時）
  - プチ株 4,500+ 銘柄、単元株は通常取引手数料
  - REST 認証は事前トークン取得 → API key ヘッダ

Phase 2 移行のタイミング:
  - 楽天試験運用で +¥100 万到達後
  - ユーザー判断で「完全自動化に進む」と決めた時
  - auカブコム証券口座開設 + kabu STATION 有効化
"""

from __future__ import annotations

from typing import Any

from trading_agent.brokers.base import Account, BrokerUnavailable, Position
from trading_agent.utils.logger import get_logger

_log = get_logger("brokers.kabucom")


class KabuComBroker:
    """auカブコム証券 broker（kabu STATION API 経由）。

    Phase 2 移行時に実装。現状は呼ばれない（broker_provider="kabucom" 設定時のみ）。
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 18080,
        api_password: str | None = None,
        engine: Any = None,
        broker_mode: str = "live",
    ) -> None:
        self.host = host
        self.port = port
        self.api_password = api_password
        self.engine = engine
        self.broker_mode = broker_mode

    @classmethod
    def from_settings(cls, settings: Any) -> "KabuComBroker":
        return cls(
            host=getattr(settings, "kabucom_api_host", "localhost"),
            port=int(getattr(settings, "kabucom_api_port", 18080)),
            api_password=getattr(settings, "kabucom_api_password", None),
        )

    def get_positions(self) -> list[Position]:
        """未実装。Phase 2 で kabu STATION /position から取得。"""
        raise BrokerUnavailable(
            "KabuComBroker.get_positions: Phase 2 で実装予定。"
            "現状は broker_provider='rakuten' での手動運用を継続してください。"
        )

    def get_account(self) -> Account | None:
        """未実装。Phase 2 で kabu STATION /wallet/cash から取得。"""
        raise BrokerUnavailable(
            "KabuComBroker.get_account: Phase 2 で実装予定。"
        )

    def place_order(self, *args: Any, **kwargs: Any) -> dict:
        """未実装。Phase 2 で kabu STATION /sendorder で実装。"""
        return {
            "ok": False,
            "error": "kabucom_not_implemented: Phase 2 で実装予定。",
        }
