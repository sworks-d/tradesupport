"""ブローカー層：口座・保有・資金（moomoo OpenAPI）。SYSTEM_DESIGN.md §4 / Phase 1.2。

口座未接続でも全体が動くよう、実ブローカーが使えない時はスタンドインへ自動フォールバックする。
"""

from __future__ import annotations

from typing import Any

from trading_agent.brokers.base import (
    Account,
    BrokerClient,
    BrokerUnavailable,
    Position,
)
from trading_agent.brokers.standin import StandInBroker
from trading_agent.utils.logger import get_logger

__all__ = [
    "Account",
    "BrokerClient",
    "BrokerUnavailable",
    "Position",
    "StandInBroker",
    "load_positions",
]

_log = get_logger("broker")


def load_positions(
    *, prefer_moomoo: bool = False, settings: Any | None = None
) -> tuple[list[Position], str]:
    """保有ポジションを取得する。

    prefer_moomoo=True かつ接続可能なら moomoo の実データ、不可なら StandIn のサンプル。
    口座未開設 / OpenD未起動 / SDK未導入では自動的にスタンドインへ落ちる。

    Returns:
        (positions, source)  source は "moomoo" / "standin"。
    """
    if prefer_moomoo:
        try:
            from trading_agent.brokers.moomoo import MoomooBroker

            broker = (
                MoomooBroker.from_settings(settings)
                if settings is not None
                else MoomooBroker()
            )
            positions = broker.get_positions()
            if positions:
                return positions, "moomoo"
            # 接続OKだが保有0（口座開設中/ペーパー未取引）→ サンプル表示で継続
            _log.info("moomoo_connected_no_positions_using_standin")
        except BrokerUnavailable as exc:
            _log.warning("moomoo_unavailable_fallback_standin", reason=str(exc))
    return StandInBroker().get_positions(), "standin"
