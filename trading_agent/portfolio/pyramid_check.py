"""ピラミッディング追加買付ジョブ（v2.10 Phase 1A-Step2）。

保有銘柄の含み益が機別トリガーを超えたら、planned_total_qty まで追加買付。
朝バッチで毎日実行され、Decision(action="buy", status="approved", thesis=pyramid)
を登録する → paper_exec の既存 buy フローで実行。

設計:
  - 対象: Portfolio.status="active" かつ planned_total_qty > qty
  - 現価取得: yfinance（J-Quants Free は遅延あり）
  - 含み益 → get_target_alloc(機別) で目標累積比率
  - 目標 > 現在比率 + マージンなら追加買付トリガー

ハルシネーション対策:
  - 現価取れない銘柄は判定しない（推測しない）
  - planned_total_qty が None / qty 以下なら対象外（旧 portfolio 互換）
  - 含み益 None で should_pyramid_up は False を返す（既存ロジック）
  - 同銘柄の重複 buy Decision を防止
  - Universe 不在は skip
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.pyramiding import get_target_alloc, should_pyramid_up
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.pyramid_check")


def _fetch_current_price(ticker: str) -> float | None:
    """yfinance で現価取得（推測しない）。"""
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        info = yf.Ticker(symbol).fast_info
        price = float(info.last_price or 0)
        return price if price > 0 else None
    except Exception as exc:
        _log.warning(
            "pyramid_check_price_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def run_pyramid_check(
    engine: Engine,
    *,
    broker_mode: str = "paper",
    today: dt.date | None = None,
    price_lookup: dict[str, float] | None = None,
) -> dict[str, Any]:
    """全保有銘柄に対してピラミッディング判定 → 追加買付 Decision 登録。

    Args:
        engine: DB エンジン
        broker_mode: "paper" or "live"
        today: 今日（None なら utcnow().date()）
        price_lookup: テスト用の価格辞書（None なら yfinance）

    Returns:
        実行サマリ dict。
    """
    if today is None:
        today = dt.date.today()

    with Session(engine) as s:
        active = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )

    if not active:
        return {
            "status": "no_holdings",
            "checked": 0,
            "added_decisions": [],
            "skipped_no_price": [],
            "skipped_no_planned": [],
            "skipped_already_full": [],
            "skipped_not_in_universe": [],
        }

    added: list[dict[str, Any]] = []
    skipped_no_price: list[str] = []
    skipped_no_planned: list[str] = []
    skipped_already_full: list[str] = []
    skipped_not_in_universe: list[str] = []

    with Session(engine) as s:
        for p in active:
            # planned_total_qty が無い / 既に満玉 → 対象外（推測しない）
            planned = p.planned_total_qty
            if planned is None or planned <= 0:
                skipped_no_planned.append(p.ticker)
                continue
            current_qty = int(p.qty or 0)
            if current_qty >= planned:
                skipped_already_full.append(p.ticker)
                continue
            current_alloc = current_qty / planned

            # Universe 照合（ハルシネーション防壁）
            uni = s.get(Universe, p.ticker)
            if uni is None or not uni.is_active:
                skipped_not_in_universe.append(p.ticker)
                continue

            # 現価取得
            if price_lookup is not None:
                current_price = price_lookup.get(p.ticker)
            else:
                current_price = _fetch_current_price(p.ticker)
            if current_price is None or current_price <= 0:
                skipped_no_price.append(p.ticker)
                continue

            # 含み益率
            entry_price = float(p.buy_price or 0)
            if entry_price <= 0:
                skipped_no_price.append(p.ticker)
                continue
            pnl_pct = (current_price - entry_price) / entry_price

            # 機別の目標累積比率
            pilot = p.personality or "KAWORU"  # 不明な機は KAWORU 扱い（最も保守的）
            should_up, target_alloc = should_pyramid_up(
                pilot=pilot,
                current_alloc=current_alloc,
                current_pnl_pct=pnl_pct,
            )
            if not should_up:
                continue

            # 重複 Decision 防止: 同 ticker の未処理 buy Decision がないか
            existing = s.exec(
                select(Decision)
                .where(col(Decision.ticker) == p.ticker)
                .where(col(Decision.action) == "buy")
                .where(col(Decision.status).in_(("verifying", "awaiting", "approved")))
            ).first()
            if existing is not None:
                continue

            # 追加 buy Decision を登録（浮動小数点誤差回避のため round）
            add_qty = int(round((target_alloc - current_alloc) * planned))
            if add_qty <= 0:
                continue
            reason = (
                f"ピラミッディング {pilot}: 含み益 +{pnl_pct*100:.1f}% で "
                f"{current_alloc*100:.0f}% → {target_alloc*100:.0f}% に追加 (+{add_qty} 株)"
            )
            new_dec = Decision(
                date=today,
                ticker=p.ticker,
                action="buy",
                status="approved",  # 規律執行・自動承認
                gendo_stance="推し",
                thesis_at_decision=reason,
                entry_price=entry_price,
                stop_pct=abs(float(p.stop_loss_pct or 0)),
            )
            s.add(new_dec)
            added.append(
                {
                    "ticker": p.ticker,
                    "pilot": pilot,
                    "current_alloc": round(current_alloc, 3),
                    "target_alloc": round(target_alloc, 3),
                    "add_qty": add_qty,
                    "pnl_pct": round(pnl_pct * 100, 2),
                }
            )
        s.commit()

    _log.info(
        "pyramid_check_done",
        checked=len(active),
        added=len(added),
        skipped_no_price=len(skipped_no_price),
        skipped_no_planned=len(skipped_no_planned),
        skipped_already_full=len(skipped_already_full),
    )
    return {
        "status": "active",
        "checked": len(active),
        "added_decisions": added,
        "skipped_no_price": skipped_no_price,
        "skipped_no_planned": skipped_no_planned,
        "skipped_already_full": skipped_already_full,
        "skipped_not_in_universe": skipped_not_in_universe,
    }
