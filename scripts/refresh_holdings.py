"""約定後の「保有・損益・決裁待ち」反映・リーン版。

build_snapshot.py --light より速い：候補(LLM)・ZEELE・topics・dummy_system・MISATO 等を
作り直さず、**口座 / 保有 / 決裁待ち**のみを DB + yfinance で再計算して snapshot に patch する。
LLM コスト 0。楽天には触れない（保有は Portfolio = mark_filled 経由の記録）。

UI から /api/refresh-holdings 経由で「✓約定」直後に呼ばれる想定。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, col, select

sys.path.insert(0, "scripts")

import build_snapshot as bs  # noqa: E402
from refresh_prices import _fetch_prices_bulk  # noqa: E402

from trading_agent.brokers import load_account, load_positions  # noqa: E402
from trading_agent.config import load_settings  # noqa: E402
from trading_agent.db import get_engine  # noqa: E402
from trading_agent.models.portfolio import Portfolio  # noqa: E402
from trading_agent.models.universe import Universe  # noqa: E402

SNAP = Path("ui/public/data/snapshot.json")


def main() -> int:
    if not SNAP.exists():
        print(json.dumps({"ok": False, "error": "snapshot.json not found"}))
        return 1

    eng = get_engine(Path("data") / "trading.sqlite")
    st = load_settings()

    positions, src = load_positions(prefer_moomoo=False, settings=st, engine=eng)
    account, _asrc = load_account(eng, st)

    codes = [p.code for p in positions]
    prices = _fetch_prices_bulk(codes)
    hist = bs._fetch_price_histories(codes, days=30)

    uni: dict[str, dict[str, str]] = {}
    pf: dict[str, dict[str, object]] = {}
    with Session(eng) as s:
        for u in s.exec(select(Universe).where(col(Universe.ticker).in_(codes))).all():
            uni[u.ticker] = {
                "name": u.name or "",
                "sector": u.sector or "",
                "market": u.market or "",
            }
        for r in s.exec(
            select(Portfolio)
            .where(col(Portfolio.status) == "active")
            .where(col(Portfolio.ticker).in_(codes))
        ).all():
            pf[r.ticker] = {
                "target_pct": r.target_pct,
                "stop_loss_pct": r.stop_loss_pct,
                "buy_date": str(r.buy_date) if r.buy_date else None,
                "strategy": r.strategy_category or "",
                "target_period_days": r.target_period_days,
            }

    holdings: dict[str, dict[str, object]] = {}
    for p in positions:
        price = prices.get(p.code)
        cost = float(p.cost_price or 0.0)
        qty = float(p.qty or 0)
        pnl = None
        if price is not None and cost:
            r = (price - cost) / cost * 100.0
            pnl = {"ratio_display": f"{r:+.1f}%", "direction": "up" if r >= 0 else "down"}
        um = uni.get(p.code, {})
        pm = pf.get(p.code, {})
        holdings[p.code] = {
            "price_display": bs._fmt_price(p.code, price) if price is not None else None,
            "current_price": price,
            "reconciliation": "single",
            "as_of": None,
            "source": src,
            "pnl": pnl,
            "qty": qty,
            "cost_price": cost,
            "cost_jpy": cost * qty,
            "unrealized_jpy": ((price - cost) * qty) if (price is not None and cost) else None,
            "name": um.get("name") or p.code,
            "sector": um.get("sector") or "",
            "market": um.get("market") or "",
            "target_pct": pm.get("target_pct"),
            "stop_pct": pm.get("stop_loss_pct"),
            "buy_date": pm.get("buy_date"),
            "strategy": pm.get("strategy"),
            "target_period_days": pm.get("target_period_days"),
            "history_30d": hist.get(p.code, []),
        }

    pending = bs._build_pending_decisions(eng)

    data = json.loads(SNAP.read_text(encoding="utf-8"))
    data["holdings"] = holdings
    data["pending_decisions"] = pending
    acct = data.get("account") or {}
    acct["cash"] = account.cash
    acct["total_assets"] = account.total_assets
    acct["currency"] = account.currency
    data["account"] = acct
    # codex #2: 約定/保有変化後は Phase C レポート(open_positions/deployable/gate)と scaling も
    # 更新する（価格 tick でなく position/event 変化なので stale NG）。cost0 DB only・失敗しても継続。
    try:
        data["scaling"] = bs._build_scaling_section(eng)
        data["gates"] = bs._build_gates_section(eng)
        import phase_c_status as _pcs  # noqa: E402

        with _pcs._suppressed():
            data["phase_c"] = _pcs._json_safe(_pcs.build_phase_c_status(eng))
    except Exception:
        pass
    data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    SNAP.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {"ok": True, "holdings": len(holdings), "pending": len(pending), "source": src}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
