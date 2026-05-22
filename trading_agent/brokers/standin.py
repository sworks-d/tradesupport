"""口座未接続時のスタンドイン・ブローカー（実稼働の初期状態）。

実稼働の前提＝「**運用元本 ¥100,000 の現金を持ち、保有ポジションは0**」（MASTER §1.3）。
口座連携後は MoomooBroker が実際の現金・保有を返す（同じ形）。
"""

from __future__ import annotations

from trading_agent.brokers.base import Account, Position

# 運用元本（仮：100万円。docのMASTER §1.3は10万円だが、実稼働想定の検証用に1,000,000）。
# 口座連携後は accinfo_query が実際の現金を返す。
STARTING_CASH_JPY = 1_000_000.0


class StandInBroker:
    """実稼働初期状態（現金 ¥100,000・保有0）を返すフォールバック・ブローカー。"""

    def get_positions(self) -> list[Position]:
        return []  # まだ何も買っていない（現金100%）

    def get_account(self) -> Account | None:
        return Account(
            cash=STARTING_CASH_JPY, total_assets=STARTING_CASH_JPY, currency="JPY"
        )
