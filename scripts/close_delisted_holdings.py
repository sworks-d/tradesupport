"""上場廃止銘柄の active 保有を一括 close する CLI（v2.10 致命候補 2 関連）。

使い方:
  .venv/bin/python scripts/close_delisted_holdings.py           # dry-run（表示のみ）
  .venv/bin/python scripts/close_delisted_holdings.py --apply   # 実行（確認プロンプト付き）
  .venv/bin/python scripts/close_delisted_holdings.py --apply --force  # 確認なし

設計意図:
  - 朝バッチに組み込まない（誤発動・自動化リスク回避）
  - 人間が実行を承認する明示的 CLI
  - Universe.is_active=False かつ Portfolio.status="active" な保有を対象
  - closed_reason="delisted" で記録、closed_price は暫定 None
    （正確な P/L は人間が後日 closed_price を入れる）

対象判定:
  - Universe.is_active = False
  - Portfolio.status = "active"
  - broker_mode 別の集計（paper / live 両方を表示）
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import get_engine
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.utils.time_utils import utcnow


def find_delisted_holdings(engine) -> list[tuple[Portfolio, Universe | None]]:
    """Universe inactive な active 保有を返す。"""
    targets: list[tuple[Portfolio, Universe | None]] = []
    with Session(engine) as s:
        active_ports = list(
            s.exec(
                select(Portfolio).where(col(Portfolio.status) == "active")
            ).all()
        )
        for p in active_ports:
            uni = s.get(Universe, p.ticker)
            if uni is None or not uni.is_active:
                targets.append((p, uni))
    return targets


def main() -> int:
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    engine = get_engine(Path("data/trading.sqlite"))
    targets = find_delisted_holdings(engine)

    if not targets:
        print("対象なし（Universe inactive な active 保有はゼロ）。")
        return 0

    print(f"=== 上場廃止扱いの active 保有 {len(targets)} 件 ===")
    for p, uni in targets:
        uni_name = uni.name if uni else "(Universe 不在)"
        uni_state = "inactive" if uni else "missing"
        print(
            f"  ticker={p.ticker} ({uni_name})  "
            f"personality={p.personality}  broker={p.broker_mode}  "
            f"buy_price={p.buy_price}  qty={p.qty}  universe={uni_state}"
        )

    if not apply:
        print()
        print("=== DRY-RUN モード ===")
        print("実行するには --apply を付けてください。")
        return 0

    if not force:
        print()
        ans = input(f"{len(targets)} 件を close しますか？ [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("キャンセルしました。")
            return 1

    # 実行
    closed_at = utcnow()
    with Session(engine) as s:
        for p, _ in targets:
            # 再取得（session が違うので）
            port = s.get(Portfolio, p.id)
            if port is None:
                continue
            port.status = "closed"
            port.closed_at = closed_at
            port.closed_reason = "delisted"
            # closed_price は暫定 None（正確な P/L は人間が後日入力）
            s.add(port)
        s.commit()
    print(f"✓ {len(targets)} 件を closed_reason='delisted' で close しました。")
    print("  注: closed_price は None のまま（実際の取引価格 or 株式交換比率は手動入力）。")

    # 監査ログとして Discord 通知
    try:
        from trading_agent.utils.discord_notifier import notify_discord

        ticker_list = ", ".join(p.ticker for p, _ in targets)
        notify_discord(
            f"📦 **上場廃止保有 close**\n対象 {len(targets)} 件: {ticker_list}",
            level="info",
            title="WILLE Delisted Holdings Closed",
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
