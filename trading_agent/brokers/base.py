"""ブローカー連携の抽象（口座・保有・資金）。moomoo OpenAPI 接続の土台（Phase 1.2）。

口座未開設 / OpenD未起動 / SDK未導入でも全体が動くよう、実ブローカー(MoomooBroker)が
使えない時は ``BrokerUnavailable`` を送出し、呼び出し側がスタンドインへフォールバックする。
Position は moomoo の position フィールドに対応させ、口座連携後はそのまま差し替えられる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BrokerUnavailable(RuntimeError):
    """ブローカー（SDK/OpenD/口座）に接続できない。スタンドインへ切替える合図。"""


@dataclass
class Position:
    """保有ポジション（moomoo position に対応）。

    取得単価(cost_price)は必須。現在値・含み損益は moomoo が返す時のみ埋まり、
    無い時（スタンドイン）は呼び出し側が market_data の実価格から計算する。
    """

    code: str  # ティッカー（市場接頭辞 "US."/"JP." は除去済み）
    qty: float
    cost_price: float  # 取得単価
    currency: str = ""
    nominal_price: float | None = None  # 現在値（moomoo提供時）
    market_val: float | None = None  # 評価額（moomoo提供時）
    pl_ratio: float | None = None  # 含み損益率(%)（moomoo提供時）
    pl_val: float | None = None  # 含み損益額（moomoo提供時）


@dataclass
class Account:
    """口座資金（moomoo accinfo に対応）。"""

    cash: float
    total_assets: float
    currency: str = ""


@runtime_checkable
class BrokerClient(Protocol):
    """ブローカーの最小インターフェース。"""

    def get_positions(self) -> list[Position]: ...

    def get_account(self) -> Account | None: ...
