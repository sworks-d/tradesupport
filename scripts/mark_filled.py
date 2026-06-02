"""発注完了マーク CLI（v2.10 楽天かぶミニ運用向け・連携漏れ防止）。

ユーザーが楽天証券で発注した後、この CLI で Decision を "filled" に更新し
Portfolio エントリを作成する。

使い方:
  # 推奨株数 / 寄付想定価格で fill（簡易）
  .venv/bin/python scripts/mark_filled.py --decision-id 87

  # 実約定価格と株数を指定（推奨）
  .venv/bin/python scripts/mark_filled.py --decision-id 87 --price 702 --shares 14

  # ticker から最新 awaiting を fill
  .venv/bin/python scripts/mark_filled.py --ticker 3697 --price 702 --shares 14

設計意図:
  - DB が単一の真実源（localStorage 等は使わない）
  - 発注完了 = Decision.status="filled" + Portfolio エントリ作成
  - 翌朝バッチ繰越時に「完了済」が反映される
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
from trading_agent.utils.time_utils import today_jst, utcnow


def main() -> int:
    p = argparse.ArgumentParser(description="発注完了マーク CLI（試験運用 / 楽天本番 切替対応）")
    p.add_argument("--decision-id", type=int, help="Decision ID")
    p.add_argument("--ticker", type=str, help="ticker（最新 awaiting を fill）")
    p.add_argument("--price", type=float, help="実約定価格（省略時は寄付想定値）")
    p.add_argument("--shares", type=int, help="実約定株数（省略時は推奨株数）")
    p.add_argument("--personality", type=str, default=None, help="DS 機（REI/ASUKA/...）省略可")
    p.add_argument(
        "--broker-mode",
        type=str,
        default="live",
        choices=["paper", "live"],
        help='記録先 broker_mode（"live"=楽天本番、"paper"=試験運用）。デフォルト live',
    )
    args = p.parse_args()

    if not args.decision_id and not args.ticker:
        print("--decision-id か --ticker のいずれかが必須")
        return 1

    engine = get_engine(Path("data") / "trading.sqlite")
    with Session(engine, expire_on_commit=False) as s:
        # Decision 特定
        if args.decision_id:
            d = s.get(Decision, args.decision_id)
            if d is None:
                print(f"Decision id={args.decision_id} 見つからず")
                return 1
        else:
            today = today_jst()
            d = s.exec(
                select(Decision)
                .where(col(Decision.ticker) == args.ticker)
                .where(col(Decision.status) == "awaiting")
                .where(col(Decision.date) == today)
                .order_by(col(Decision.id).desc())
            ).first()
            if d is None:
                print(f"ticker={args.ticker} の awaiting Decision なし")
                return 1

        if d.status != "awaiting":
            print(f"Decision id={d.id} の status={d.status} (awaiting でない、処理しません)")
            return 1

        # 価格・株数
        price = args.price if args.price is not None else (d.entry_price or 0.0)
        shares = args.shares if args.shares is not None else int(d.shares_filled or 0)
        if price <= 0 or shares <= 0:
            print(f"price={price} shares={shares} が不正。--price --shares で指定してください")
            return 1

        # v2.10 P10: volume 妥当性チェック（過大発注ミス防止・警告のみ）
        from trading_agent.reporting.order_list import build_order_items
        from trading_agent.portfolio.misato import treasury_view as _tv

        try:
            items = build_order_items(engine, available_jpy=None)
            recommended = next((it for it in items if it.decision_id == d.id), None)
            if recommended and recommended.recommended_shares > 0:
                ratio = shares / recommended.recommended_shares
                if ratio > 2.0:
                    print(
                        f"⚠️ 警告: 報告株数 {shares} が推奨 {recommended.recommended_shares} の {ratio:.1f} 倍。"
                        " 入力ミス（桁違い）の可能性があります。"
                    )
                elif ratio > 1.5:
                    print(
                        f"⚠️ 注意: 報告株数 {shares} が推奨 {recommended.recommended_shares} の {ratio:.1f} 倍。"
                    )
            # 予算チェック
            tv_check = _tv(engine, args.broker_mode)
            available = float(tv_check.get("available_jpy") or 0)
            cost_check = price * shares
            if cost_check > available + 100:  # ¥100 の誤差許容
                print(
                    f"⚠️ 警告: 約定額 ¥{cost_check:,.0f} が {args.broker_mode} Treasury 残 ¥{available:,.0f} を超過。"
                    " 入金漏れ or 信用取引等の特殊ケース? 確認してください。"
                )
        except Exception:
            pass  # 警告のみ。fill 処理は止めない

        # Decision を filled に
        d.status = "filled"
        d.entry_price = price
        d.shares_filled = float(shares)
        # P0: 評価前提フィールドを刻む（実弾報告の実取引を増額ゲート⑥の実績に乗せる）。
        # A3/A8: エントリ時点の trailing 相場局面を固定保存（両局面判定）。失敗時は unknown。
        try:
            from trading_agent.wille.ritsuko import detect_market_cycle

            _regime = str(detect_market_cycle().get("cycle") or "unknown")
        except Exception:
            _regime = "unknown"
        stamp_evaluation_fields(
            d,
            target_period_days=int(d.target_period_days or 90),
            on_date=today_jst(),
            market_regime=_regime,
            filled_via="manual",  # 実弾代行（楽天）報告
        )
        # personalities_filled は将来 DS 機運用向け
        if args.personality:
            existing = list(d.personalities_filled or [])
            if args.personality not in existing:
                existing.append(args.personality)
                d.personalities_filled = existing
        s.add(d)

        # Portfolio エントリ作成（broker_mode 別に記録）
        portfolio = Portfolio(
            ticker=d.ticker,
            buy_date=today_jst(),
            buy_price=price,
            qty=shares,
            currency="JPY",
            strategy_category=d.strategy_category or "中期",
            target_period_days=int(d.target_period_days or 90),
            target_pct=float(getattr(d, "target_pct", None) or 0.20),
            stop_loss_pct=float(d.stop_pct or 0.10),
            target_date=today_jst() + dt.timedelta(days=int(d.target_period_days or 90)),
            thesis=d.thesis_at_decision or "",
            status="active",
            personality=args.personality,
            broker_mode=args.broker_mode,  # "live"=楽天本番、"paper"=試験運用
            planned_total_qty=shares,
            decision_id=d.id,
        )
        s.add(portfolio)
        s.commit()
        s.refresh(d)
        s.refresh(portfolio)

    # v2.10 P13: Treasury から fill コストを減算（既存バグ修正）
    cost = price * shares
    try:
        from trading_agent.portfolio.misato import deposit as _deposit

        _deposit(engine, -cost, broker_mode=args.broker_mode)
    except Exception as exc:
        print(f"  ⚠️ Treasury 減算失敗: {type(exc).__name__}: {exc}")

    mode_label = "🟢 楽天本番" if args.broker_mode == "live" else "🟡 試験運用"
    print(f"✓ Decision id={d.id} ticker={d.ticker} を filled に更新 ({mode_label})")
    print(f"  実約定: ¥{price:,.0f} × {shares} 株 = ¥{price * shares:,.0f}")
    print(f"  Portfolio id={portfolio.id} 作成 (broker_mode={args.broker_mode}, status=active)")
    print(f"  stop_loss: ¥{price * (1 - abs(portfolio.stop_loss_pct)):,.0f} (-{abs(portfolio.stop_loss_pct)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
