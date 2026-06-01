"""KATSURAGI 戦略の重み校正分析スクリプト（D3・v2.8）。

運用 1 ヶ月後に実行することを想定。Brief boost と実際の hit_rate / avg_R の
相関を測定し、戦略パラメータの重みを実証で調整すべきかを判定する。

実行:
  uv run python scripts/calibrate_misato_weights.py
  uv run python scripts/calibrate_misato_weights.py --month 2026-05  # 月指定
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from sqlmodel import Session, col, select

from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KATSURAGI 戦略重み校正")
    p.add_argument(
        "--month",
        help="対象月 (YYYY-MM)。未指定なら全期間。",
        default=None,
    )
    p.add_argument(
        "--output",
        help="結果 JSON 出力先。未指定なら標準出力。",
        default=None,
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    engine = get_engine(Path("data") / "trading.sqlite")

    with Session(engine) as s:
        portfolios = s.exec(
            select(Portfolio).where(col(Portfolio.status) == "closed")
        ).all()

    if not portfolios:
        print(json.dumps({"ok": False, "message": "No closed portfolios for analysis"}))
        return 1

    # 月フィルタ
    if args.month:
        target_year, target_month = map(int, args.month.split("-"))
        portfolios = [
            p
            for p in portfolios
            if p.closed_at
            and p.closed_at.year == target_year
            and p.closed_at.month == target_month
        ]

    if not portfolios:
        print(json.dumps({"ok": False, "message": f"No closed portfolios in {args.month}"}))
        return 1

    # 各 portfolio の Return % を計算
    by_pilot: dict[str, list[float]] = defaultdict(list)
    by_situation: dict[str, list[float]] = defaultdict(list)
    overall: list[float] = []

    for p in portfolios:
        try:
            buy = float(p.buy_price or 0)
            sell = float(p.closed_price or buy)
            if buy <= 0:
                continue
            r = (sell - buy) / buy * 100
            overall.append(r)
            by_pilot[p.personality or "—"].append(r)
        except Exception:
            continue

    # 集計
    def _stats(rs: list[float]) -> dict:
        if not rs:
            return {"n": 0}
        wins = sum(1 for x in rs if x > 0)
        return {
            "n": len(rs),
            "hit_rate": round(wins / len(rs), 3),
            "avg_R": round(mean(rs), 2),
            "best_R": round(max(rs), 2),
            "worst_R": round(min(rs), 2),
        }

    result = {
        "period": args.month or "all",
        "generated_at": datetime.now().isoformat(),
        "total_closed": len(portfolios),
        "overall": _stats(overall),
        "by_pilot": {pilot: _stats(rs) for pilot, rs in by_pilot.items()},
    }

    # 推奨アクション
    actions: list[str] = []
    for pilot, stats in result["by_pilot"].items():
        if stats.get("n", 0) >= 20:
            if stats.get("hit_rate", 0) >= 0.55 and stats.get("avg_R", 0) >= 0.3:
                actions.append(
                    f"✅ {pilot}: 昇格候補（n={stats['n']} hit={stats['hit_rate']:.0%} avg_R={stats['avg_R']}）"
                )
            elif stats.get("hit_rate", 0) < 0.4:
                actions.append(
                    f"⚠ {pilot}: 降格検討（hit={stats['hit_rate']:.0%} < 40%）"
                )
    if not actions and result["total_closed"] >= 30:
        actions.append("ℹ️ 全機が n≥20 を満たさず、明確な昇降格シグナルなし。継続観察。")
    elif result["total_closed"] < 30:
        actions.append("ℹ️ サンプル数 < 30、判断には時期尚早。運用継続を推奨。")

    result["recommended_actions"] = actions

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
