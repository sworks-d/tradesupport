"""保有銘柄の現在価格と損益のみを更新する軽量スクリプト（v2.8）。

build_snapshot.py との違い:
  - WILLE Brief / MISATO 戦略 / 候補プール再構築なし
  - 保有銘柄の current_price と pnl のみ更新
  - 1-2 秒で完了 / yfinance リクエスト 1 回

UI から /api/refresh-prices 経由で 30/60/300 秒ごとに呼ばれる想定。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.portfolio import Portfolio

SNAPSHOT_PATH = Path("ui/public/data/snapshot.json")
RATE_LIMIT_STATE_PATH = Path("data/refresh_prices_state.json")

# C3: yfinance rate-limit クールダウン設定
RATE_LIMIT_FAIL_THRESHOLD = 3  # 3 連続失敗で
RATE_LIMIT_COOLDOWN_MINUTES = 30  # 30 分クールダウン


def _load_rate_state() -> dict:
    """連続失敗カウントとクールダウン状態を SQLite ファイルから読む。"""
    if not RATE_LIMIT_STATE_PATH.exists():
        return {"consecutive_failures": 0, "cooldown_until": None, "last_check_at": None}
    try:
        return json.loads(RATE_LIMIT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"consecutive_failures": 0, "cooldown_until": None, "last_check_at": None}


def _save_rate_state(state: dict) -> None:
    """状態を保存（refresh_prices_state.json）。"""
    try:
        RATE_LIMIT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _is_in_cooldown(state: dict, now: datetime) -> tuple[bool, str]:
    """クールダウン中かを判定。"""
    cooldown_until = state.get("cooldown_until")
    if not cooldown_until:
        return False, ""
    try:
        until = datetime.fromisoformat(cooldown_until)
    except (ValueError, TypeError):
        return False, ""
    if now < until:
        remaining = int((until - now).total_seconds() / 60)
        return True, f"yfinance クールダウン中（残 {remaining} 分）"
    return False, ""


def _fetch_prices_bulk(tickers: list[str]) -> dict[str, float]:
    """yfinance で複数銘柄の直近終値を一括取得（1 リクエスト）。"""
    if not tickers:
        return {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return {}

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    out: dict[str, float] = {}
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if closes:
                out[ticker] = closes[-1]
        except Exception:
            continue
    return out


def main() -> int:
    if not SNAPSHOT_PATH.exists():
        print(json.dumps({"ok": False, "error": "snapshot.json not found"}))
        return 1

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    # C3: rate-limit クールダウンチェック
    now = datetime.now()
    state = _load_rate_state()
    in_cd, cd_reason = _is_in_cooldown(state, now)
    if in_cd:
        print(json.dumps({"ok": False, "skipped": True, "reason": cd_reason}))
        return 0

    # 1. 現在の active 保有銘柄を集約
    with Session(engine) as s:
        rows = s.exec(select(Portfolio).where(col(Portfolio.status) == "active")).all()
    tickers = sorted({r.ticker for r in rows})

    if not tickers:
        print(json.dumps({"ok": True, "updated": 0, "message": "no active holdings"}))
        return 0

    # 2. yfinance bulk で 1 リクエストで全銘柄分の終値取得
    price_map = _fetch_prices_bulk(tickers)
    if not price_map:
        # C3: 失敗カウント増加 + 閾値超過でクールダウン突入
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        state["last_check_at"] = now.isoformat()
        if state["consecutive_failures"] >= RATE_LIMIT_FAIL_THRESHOLD:
            state["cooldown_until"] = (
                now + timedelta(minutes=RATE_LIMIT_COOLDOWN_MINUTES)
            ).isoformat()
        _save_rate_state(state)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "yfinance fetch failed",
                    "consecutive_failures": state["consecutive_failures"],
                    "tickers": tickers,
                }
            )
        )
        return 1

    # 成功 → 失敗カウントとクールダウンをリセット
    if state.get("consecutive_failures", 0) > 0 or state.get("cooldown_until"):
        state["consecutive_failures"] = 0
        state["cooldown_until"] = None
        state["last_check_at"] = now.isoformat()
        _save_rate_state(state)

    # 3. snapshot.json を読み込み（既存セクションを保持）
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # 4. dummy_system セクションの保有銘柄を更新 + 損益再計算
    updated = 0
    for pilot in data.get("dummy_system", {}).get("personalities", []) or []:
        holdings = pilot.get("holdings", []) or []
        invested = 0.0
        market_value = 0.0
        for h in holdings:
            cur = price_map.get(h["ticker"])
            if cur is not None:
                h["current_price"] = cur
                updated += 1
            cur_p = float(h.get("current_price") or 0)
            qty = float(h.get("qty") or 0)
            buy = float(h.get("buy_price") or 0)
            cost = buy * qty
            mv = cur_p * qty
            h["market_value"] = mv
            h["unrealized"] = mv - cost
            h["unrealized_pct"] = (h["unrealized"] / cost * 100) if cost else 0.0
            # v2.8: 単元株（100 株）相当を再計算
            t = h.get("ticker", "")
            lot_size = 100 if len(t) == 4 and t.isdigit() else 1
            h["lot_size"] = lot_size
            h["lot_equivalent_jpy"] = cur_p * lot_size
            invested += cost
            market_value += mv
        overlay = float(pilot.get("overlay_cash_jpy") or 0)
        cash = overlay - invested
        total = cash + market_value
        pnl = total - overlay
        pilot["invested_jpy"] = invested
        pilot["market_value_jpy"] = market_value
        pilot["cash_jpy"] = cash
        pilot["total_value_jpy"] = total
        pilot["pnl_jpy"] = pnl
        pilot["pnl_pct"] = (pnl / overlay * 100) if overlay else 0.0

    # generated_at を更新（軽量更新であることを示すため別キーも追加）
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "dummy_system" in data:
        data["dummy_system"]["generated_at"] = now_str[:16]  # 秒は表示外
        data["dummy_system"]["last_price_refresh_at"] = now_str

    SNAPSHOT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "updated_holdings": updated,
                "tickers": tickers,
                "refreshed_at": now_str,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
