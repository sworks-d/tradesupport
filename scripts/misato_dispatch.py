"""MISATO の人間トリガー CLI。

MISATO（ダミーシステム司令塔）に予算を渡して、4 機への割り当て案を生成・実行する。
**安全装置 4 段**:
  1. HALT ファイル `~/.trading-agent/HALT` が存在する間は何もしない。
  2. dry-run（既定）: 配分案・割り当て案を表示するだけ。発注しない。
  3. 1 命令 ¥500,000 / 1 機 ¥200,000 の上限を misato.py が物理クランプ。
  4. `--approve` を付けた時のみ paper_fill_approved に進む（人間の明示的承認）。

使い方:
  uv run python scripts/misato_dispatch.py --budget 100000              # dry-run（案表示のみ）
  uv run python scripts/misato_dispatch.py --budget 100000 --approve    # 実 fill
  uv run python scripts/misato_dispatch.py --budget 50000 --personality REI --approve
  uv run python scripts/misato_dispatch.py --halt-on                    # HALT ON
  uv run python scripts/misato_dispatch.py --halt-off                   # HALT OFF
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.universe import Universe
from trading_agent.portfolio.misato import (
    AUTO_TRADE_DEFAULT_HOURS,
    DEFAULT_HALT_FILE,
    auto_trade_view,
    check_halt,
    cleanup_for_fresh_run,
    deposit,
    dispatch,
    plan_to_dict,
    reset_treasury,
    set_master_auto_trade,
    set_pilot_auto_trade,
    treasury_view,
)


def _live_lookups(engine: Engine):
    """live yfinance lookups（run_paper._live_lookups の薄い複製・将来共通化）。"""
    import yfinance as yf

    with Session(engine) as s:
        rows = s.exec(select(Universe).where(col(Universe.is_active))).all()
    market = {u.ticker: u.market for u in rows}
    try:
        usdjpy = float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception:
        usdjpy = 150.0

    def is_jp(t: str) -> bool:
        return market.get(t) == "JP"

    def price(t: str) -> float | None:
        m = market.get(t)
        sym = f"{t}.T" if m == "JP" else t
        try:
            p = float(yf.Ticker(sym).fast_info.last_price)
        except Exception:
            return None
        return p if m == "JP" else p * usdjpy

    return price, is_jp


def _format_plan(plan_d: dict) -> str:
    lines: list[str] = []
    if plan_d["halted"]:
        lines.append("⛔ HALT 中：" + plan_d["halt_reason"])
        return "\n".join(lines)

    lines.append(f"=== MISATO Dispatch Plan @ {plan_d['generated_at']} ===")
    lines.append(f"総予算: ¥{plan_d['total_budget_jpy']:,}")
    alloc = plan_d["allocation"]
    lines.append(f"配分方式: {alloc.get('mode', 'fallback-equal')} （{alloc['reason']}）")
    demand = alloc.get("per_pilot_demand", {})
    for pilot, jpy in alloc["per_pilot_jpy"].items():
        d = demand.get(pilot, 0)
        lines.append(f"  {pilot:8s} ¥{jpy:>8,}  候補 {d} 件")

    picked_n = plan_d.get("picked_count", 0)
    short_n = plan_d.get("shortlist_count", 0)
    lines.append(
        f"\n--- 割り当て案 {len(plan_d['assignments'])} 件"
        f"（実 fill {picked_n} 件 / shortlist {short_n} 件）---"
    )
    # picked を先に
    picked_rows = [a for a in plan_d["assignments"] if a.get("picked")]
    short_rows = [a for a in plan_d["assignments"] if not a.get("picked")]
    for a in picked_rows:
        marker = "★ MAGI+ZEELE" if a.get("source") == "both" else (
            "  ZEELE" if a.get("source") == "zeele" else "  MAGI"
        )
        lines.append(
            f"  ✅ {marker:<14} {a['ticker']:<6} stance={a['gendo_stance']:<6}"
            f" → {a['assigned_to']:<7} ¥{a['proposed_budget_jpy']:>6,} score={a.get('score', 0):.2f}"
            f" | {a['reason']}"
        )
    if short_rows:
        lines.append(f"  --- shortlist (上位外・実 fill しない) {len(short_rows)} 件 ---")
        for a in short_rows[:6]:
            lines.append(
                f"  ⏸  {a['ticker']:<6} {a.get('source', 'magi')} score={a.get('score', 0):.2f} → {a['assigned_to']}"
            )
        if len(short_rows) > 6:
            lines.append(f"  ⏸ …他 {len(short_rows) - 6} 件")

    if plan_d["promotions"]:
        lines.append("\n--- 🎖 昇格候補 ---")
        for p in plan_d["promotions"]:
            lines.append(f"  {p['personality']}: {p['note']}")
    else:
        lines.append("\n--- 昇格候補: なし（n<30 or 命中率<50% or R<+0.5）---")

    if plan_d["executed"]:
        lines.append("\n--- ✅ 実行結果 ---")
        for f in plan_d["fills"]:
            if "error" in f:
                lines.append(f"  {f['pilot']}: ERROR {f['error']}")
                continue
            lines.append(
                f"  {f['pilot']:<7} 予算¥{f['budget_jpy']:,} → fill {len(f['fills'])} 件 / "
                f"close {len(f['closes'])} 件 / 残¥{f['cash_after_jpy']:,}"
            )
            for fl in f["fills"]:
                lines.append(
                    f"      buy {fl['ticker']} {fl['shares']}株 @¥{fl['fill_price']:,.0f}"
                    f" = ¥{fl['amount_jpy']:,}"
                )
            for cl in f["closes"]:
                sign = "+" if cl["pnl_jpy"] >= 0 else ""
                lines.append(
                    f"      sell {cl['ticker']} {cl['qty']}株 @¥{cl['sell_price']:,.0f}"
                    f" / {cl['reason']} / PnL {sign}¥{cl['pnl_jpy']:,}"
                )
    else:
        lines.append("\n--- ⚠ Dry-run（実 fill しない）。承認するなら --approve を付けて再実行 ---")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="MISATO Dispatch CLI")
    ap.add_argument("--budget", type=float, default=None, help="今回の総予算（JPY・既定: treasury 未配分残高）")
    ap.add_argument("--approve", action="store_true", help="実 fill を走らせる（人間承認）")
    ap.add_argument("--personality", default=None, help="1 機だけ動かす場合")
    ap.add_argument("--json", action="store_true", help="JSON 出力")
    ap.add_argument("--halt-on", action="store_true", help="HALT ファイル作成")
    ap.add_argument("--halt-off", action="store_true", help="HALT ファイル削除")
    ap.add_argument("--halt-status", action="store_true", help="HALT 状態確認のみ")
    ap.add_argument("--deposit", type=float, default=None, help="MISATO に入金（JPY）")
    ap.add_argument("--reset-treasury", action="store_true", help="預かり金と配分を全リセット")
    ap.add_argument("--balance", action="store_true", help="預かり金・配分状況を表示")
    ap.add_argument(
        "--auto-on",
        default=None,
        help="自動売買 ON: 'master' or 'REI'/'ASUKA'/'SHINJI'/'KAWORU'",
    )
    ap.add_argument(
        "--auto-off",
        default=None,
        help="自動売買 OFF: 'master' or pilot 名",
    )
    ap.add_argument(
        "--auto-hours",
        type=int,
        default=AUTO_TRADE_DEFAULT_HOURS,
        help="auto-on の有効時間（既定 24h）",
    )
    ap.add_argument("--auto-status", action="store_true", help="自動売買状態の確認")
    ap.add_argument(
        "--cleanup-fresh",
        action="store_true",
        help="致命的バグ対策: 全 active portfolio を closed + decision.personalities_filled クリア + treasury reset",
    )
    args = ap.parse_args()

    if args.halt_on:
        DEFAULT_HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_HALT_FILE.write_text(
            f"halted manually @ {__import__('datetime').datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
        print(f"⛔ HALT ON: {DEFAULT_HALT_FILE}")
        return
    if args.halt_off:
        if DEFAULT_HALT_FILE.exists():
            DEFAULT_HALT_FILE.unlink()
            print(f"✅ HALT OFF: {DEFAULT_HALT_FILE} を削除")
        else:
            print(f"既に解除済: {DEFAULT_HALT_FILE} 存在せず")
        return
    if args.halt_status:
        halted, reason = check_halt()
        print(f"HALT={halted} reason={reason or '—'}")
        return

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    if args.balance:
        view = treasury_view(engine)
        if args.json:
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            print(f"=== MISATO Treasury ===")
            print(f"預かり金 (seed):   ¥{view['seed_jpy']:,}")
            print(f"配分済 (allocated): ¥{view['allocated_jpy']:,}")
            print(f"未配分 (available): ¥{view['available_jpy']:,}")
            print(f"入金回数: {view['deposit_count']}")
            print(f"直近入金: {view['last_deposit_at'] or '—'}")
            print(f"--- 各機配分 ---")
            for pilot, jpy in view["allocations"].items():
                print(f"  {pilot:8s}: ¥{jpy:,}")
        return

    if args.deposit is not None:
        if args.deposit == 0:
            print("ERROR: --deposit は 0 以外（負値は払い戻し）", file=sys.stderr)
            sys.exit(2)
        try:
            t = deposit(engine, args.deposit)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        verb = "入金" if args.deposit > 0 else "払い戻し"
        print(f"✅ {verb} ¥{abs(args.deposit):,.0f} → 預かり累計 ¥{t.seed_jpy:,.0f}")
        return

    if args.reset_treasury:
        reset_treasury(engine)
        print("✅ MISATO 預かり金と全配分をリセットしました")
        return

    if args.cleanup_fresh:
        counts = cleanup_for_fresh_run(engine)
        print(
            f"✅ Fresh cleanup: portfolio {counts['portfolios_closed']} 件 closed / "
            f"decision {counts['decisions_reset']} 件 reset / Treasury リセット"
        )
        return

    if args.auto_status:
        v = auto_trade_view(engine)
        if args.json:
            print(json.dumps(v, ensure_ascii=False, indent=2))
        else:
            m = v["master"]
            print(f"=== 自動売買 状態 ({v['checked_at']}) ===")
            print(f"マスター: {'🟢 ON' if m['active'] else '⚫ OFF'} (until {m['until']}・残り {m['remaining_minutes']} 分)")
            for pilot, st in v["per_pilot"].items():
                state = "🟢 ON" if st["active"] else "⚫ OFF"
                print(f"  {pilot:8s} {state}  until {st['until']}・残り {st['remaining_minutes']} 分")
        return

    if args.auto_on or args.auto_off:
        scope = args.auto_on or args.auto_off
        hours = args.auto_hours if args.auto_on else None
        if scope == "master":
            until = set_master_auto_trade(engine, hours=hours)
            print(f"{'🟢 マスター自動売買 ON' if hours else '⚫ マスター自動売買 OFF'} until={until}")
        elif scope in ("REI", "ASUKA", "SHINJI", "KAWORU"):
            until = set_pilot_auto_trade(engine, scope, hours=hours)
            print(f"{'🟢' if hours else '⚫'} {scope} 自動売買 {'ON' if hours else 'OFF'} until={until}")
        else:
            print(f"ERROR: scope は 'master' or 'REI/ASUKA/SHINJI/KAWORU': got {scope}", file=sys.stderr)
            sys.exit(2)
        return

    price, is_jp = _live_lookups(engine) if args.approve else (None, None)

    plan = dispatch(
        engine,
        total_budget_jpy=args.budget,  # None なら treasury の available を使う
        approve=args.approve,
        only_personality=args.personality,
        price_lookup=price,
        is_jp_lookup=is_jp,
    )
    plan_d = plan_to_dict(plan)
    if args.json:
        print(json.dumps(plan_d, ensure_ascii=False, indent=2))
    else:
        print(_format_plan(plan_d))


if __name__ == "__main__":
    main()
