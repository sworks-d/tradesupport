"""試験運用（paper）モードで今日の awaiting Decision を自動 fill するスクリプト。

ユーザーが楽天で「朝バッチの推奨通りに発注した想定」を実現するため、
朝バッチで生成された当日の awaiting buy Decision を全部 paper モードで Portfolio エントリ化する。

朝バッチの notify ノード直後に毎日自動実行される設計。
trailing_check / close_due は両モードで動くため、これだけで完全自動シミュレーション運用が成立する。

使い方:
  .venv/bin/python scripts/auto_fill_paper.py
  .venv/bin/python scripts/auto_fill_paper.py --available-jpy 100000  # 予算指定
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import get_engine
from trading_agent.evaluation.job import stamp_evaluation_fields
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.misato import treasury_view
from trading_agent.reporting.order_list import build_order_items
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import today_jst

_log = get_logger("scripts.auto_fill_paper")


def main() -> int:
    p = argparse.ArgumentParser(description="paper モード自動 fill（試験運用継続用）")
    p.add_argument("--available-jpy", type=float, default=None, help="予算（None なら paper Treasury から）")
    args = p.parse_args()

    engine = get_engine(Path("data") / "trading.sqlite")
    today = today_jst()

    available = args.available_jpy
    if available is None:
        tv = treasury_view(engine, "paper")
        available = float(tv.get("available_jpy") or 0)
    # codex C/#3: 直 fill 経路なので、Phase C unlock 有効時は available(¥100万側) を
    # deployable（解放上限 − active exposure）で cap する（解放枠 ¥10万 を無視させない）。
    from trading_agent.portfolio.misato import deployable_budget_jpy

    _dep = deployable_budget_jpy(engine, "paper")
    if _dep is not None and available > _dep:
        print(f"⚠ Phase C unlock 有効: available ¥{available:,.0f} → deployable ¥{_dep:,.0f} に cap")
        available = _dep
    if available <= 0:
        print("paper deploy 可能額ゼロ（解放枠使い切り or 未解放 or Treasury 残ゼロ）。fill しません")
        return 0

    items = build_order_items(engine, available_jpy=available)
    items_by_decision = {it.decision_id: it for it in items}

    # A3/A8: エントリ時点の trailing 相場局面を 1 回取得（ゲート⑥両局面判定）。失敗時 unknown。
    try:
        from trading_agent.wille.ritsuko import detect_market_cycle

        entry_regime = str(detect_market_cycle().get("cycle") or "unknown")
    except Exception:
        entry_regime = "unknown"

    filled = skipped = 0
    total_cost = 0.0
    with Session(engine, expire_on_commit=False) as s:
        decs = list(
            s.exec(
                select(Decision)
                .where(col(Decision.date) == today)
                .where(col(Decision.status) == "awaiting")
                .where(col(Decision.action) == "buy")
            ).all()
        )
        for d in decs:
            it = items_by_decision.get(d.id)
            if it is None or it.recommended_shares == 0 or it.current_price is None:
                skipped += 1
                continue
            price = it.current_price
            shares = it.recommended_shares
            d.status = "filled"
            d.entry_price = price
            d.shares_filled = float(shares)
            period = int(getattr(d, "target_period_days", None) or 90)
            # P0: 評価前提フィールド（stop/target/評価期日）を刻む。
            # これが無いと evaluate_due_decisions が filled を採点できず実績が貯まらない。
            stamp_evaluation_fields(
                d, target_period_days=period, on_date=today,
                market_regime=entry_regime, filled_via="paper_auto",
            )
            s.add(d)
            s.add(
                Portfolio(
                    ticker=d.ticker,
                    buy_date=today,
                    buy_price=price,
                    qty=shares,
                    currency="JPY",
                    strategy_category=getattr(d, "strategy_category", None) or "中期",
                    target_period_days=period,
                    target_pct=0.20,
                    stop_loss_pct=float(d.stop_pct or 0.10),
                    target_date=today + dt.timedelta(days=period),
                    thesis=d.thesis_at_decision or "",
                    status="active",
                    broker_mode="paper",
                    planned_total_qty=shares,
                    decision_id=d.id,
                )
            )
            filled += 1
            total_cost += price * shares
        s.commit()

    # v2.10 P13: Treasury から累計 fill コストを減算
    if total_cost > 0:
        try:
            from trading_agent.portfolio.misato import deposit as _deposit

            _deposit(engine, -total_cost, broker_mode="paper")
        except Exception as exc:
            print(f"  ⚠️ Treasury 減算失敗: {type(exc).__name__}: {exc}")

    print(f"✓ paper 自動 fill: filled {filled} 件 / skipped {skipped} 件 / 合計 ¥{int(total_cost):,}")
    _log.info("paper_auto_fill_done", filled=filled, skipped=skipped, total_cost=total_cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
