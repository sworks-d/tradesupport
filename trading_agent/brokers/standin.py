"""口座未接続時のスタンドイン・ブローカー（実稼働の初期状態）。

実稼働の前提＝「**運用元本 ¥100,000 の現金を持ち、保有ポジションは0**」（MASTER §1.3）。
口座連携後は MoomooBroker が実際の現金・保有を返す（同じ形）。
"""

from __future__ import annotations

from trading_agent.brokers.base import Account, Position

# 運用元本：¥100,000（RISK_EXOSKELETON 2026-05-23 確定。¥1Mから変更）。
# ¥100kは「①実弾検証 ②規律の訓練」が主目的。口座連携後は accinfo_query が実値を返す。
STARTING_CASH_JPY = 100_000.0


class StandInBroker:
    """実稼働初期状態（現金 ¥100,000・保有0）を返すフォールバック・ブローカー。"""

    def get_positions(self) -> list[Position]:
        return []  # まだ何も買っていない（現金100%）

    def get_account(self) -> Account | None:
        return Account(
            cash=STARTING_CASH_JPY, total_assets=STARTING_CASH_JPY, currency="JPY"
        )
