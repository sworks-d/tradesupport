"""トレーリングストップ毎日チェックジョブ（v2.10 Phase 1A）。

保有銘柄の現在価格を取得し、trailing_stop.compute_trailing_stop で
引き上げ後の stop ラインを計算、現価がそれを下回ったら売却推奨を
Decision として登録する。

設計原則:
  - 売却推奨は Decision(action="sell_loss") を新規作成（既存フローと整合）
  - paper_exec の既存 sell ロジックで実際の売却（直接 Portfolio を改変しない）
  - 価格取れない銘柄は判定しない（推測しない）

ハルシネーション対策:
  - yfinance で価格取れない → 売却推奨を出さない（推測しない）
  - 既存 trailing_stop の「current_price None で元 stop」が機能
  - Universe にない銘柄は判定しない（virtually impossible だが念のため）
  - 既に sell 推奨が出てる銘柄は重複登録しない
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.trailing_stop import (
    compute_trailing_stop,
    should_trigger_stop,
)
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.trailing_check")


def _fetch_current_price(ticker: str) -> float | None:
    """yfinance で現在価格取得（None なら判定不能・推測しない）。"""
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        symbol = to_yfinance_symbol(ticker)
        info = yf.Ticker(symbol).fast_info
        price = float(info.last_price or 0)
        return price if price > 0 else None
    except Exception as exc:
        _log.warning(
            "trailing_check_price_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def run_trailing_check(
    engine: Engine,
    *,
    broker_mode: str = "paper",
    today: dt.date | None = None,
    price_lookup: dict[str, float] | None = None,
) -> dict[str, Any]:
    """全保有銘柄に対して trailing stop チェックを実行し、売却推奨を Decision に登録。

    Args:
        engine: DB エンジン
        broker_mode: "paper" or "live"
        today: 今日の日付（None なら utcnow）
        price_lookup: テスト用の価格辞書（None なら yfinance）

    Returns:
        実行サマリ dict。
    """
    if today is None:
        today = dt.date.today()

    # 既存の active 保有 + Universe（最終防壁・既存設計）
    with Session(engine) as s:
        active_ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )

    if not active_ports:
        return {
            "status": "no_holdings",
            "checked": 0,
            "stop_triggered": [],
            "skipped_no_price": [],
            "skipped_not_in_universe": [],
        }

    stop_triggered: list[dict[str, Any]] = []
    skipped_no_price: list[str] = []
    skipped_not_in_universe: list[str] = []

    with Session(engine) as s:
        for p in active_ports:
            # Universe 照合（ハルシネーション防壁）
            uni = s.get(Universe, p.ticker)
            if uni is None or not uni.is_active:
                skipped_not_in_universe.append(p.ticker)
                continue

            # 現在価格取得
            current_price: float | None
            if price_lookup is not None:
                current_price = price_lookup.get(p.ticker)
            else:
                current_price = _fetch_current_price(p.ticker)
            if current_price is None:
                skipped_no_price.append(p.ticker)
                continue

            # v2.10 Phase 1A-Step2: peak_pnl_pct を更新（真の trailing 化）
            entry_price = float(p.buy_price or 0)
            base_stop = float(p.stop_loss_pct or 0)
            current_pnl = (
                (current_price - entry_price) / entry_price
                if entry_price > 0
                else 0.0
            )
            prev_peak = p.peak_pnl_pct
            new_peak = max(prev_peak or current_pnl, current_pnl)
            if prev_peak is None or new_peak > prev_peak:
                p.peak_pnl_pct = new_peak
                s.add(p)

            # trailing stop 計算（真の trailing: peak 基準）
            trail_result = compute_trailing_stop(
                entry_price=entry_price,
                current_price=current_price,
                base_stop_pct=base_stop,
                peak_pnl_pct=p.peak_pnl_pct,
            )

            # v2.10 Phase G-2: earnings_guard との合成
            # 決算 N 日前なら stop_pct をさらに厳格化（trailing と「より厳しい方」を採用）
            try:
                from trading_agent.portfolio.earnings_guard import (
                    compute_effective_stop_with_earnings,
                    fetch_next_earnings_date,
                )

                earnings_date = fetch_next_earnings_date(p.ticker)
                if earnings_date is not None:
                    combined = compute_effective_stop_with_earnings(
                        base_stop_pct=base_stop,
                        trailing_stop_pct=trail_result.effective_stop_pct,
                        earnings_date=earnings_date,
                        today=today,
                    )
                    # 合成結果が trailing より厳しい場合のみ上書き
                    if combined["effective_stop_pct"] > trail_result.effective_stop_pct:
                        # より高い stop_pct（より早く売却）→ trail_price を再計算
                        new_eff = combined["effective_stop_pct"]
                        new_trail = entry_price * (1.0 + new_eff)
                        from trading_agent.portfolio.trailing_stop import (
                            TrailingStopResult,
                        )

                        trail_result = TrailingStopResult(
                            effective_stop_pct=new_eff,
                            trail_price=new_trail,
                            shift_applied=trail_result.shift_applied,
                            is_break_even=new_eff >= 0.0,
                            is_profit_locked=new_eff > 0.0,
                            tier_label=f"{trail_result.tier_label} + {combined['reason']}",
                        )
            except Exception as exc:
                _log.warning(
                    "trailing_earnings_guard_failed",
                    ticker=p.ticker,
                    error_type=type(exc).__name__,
                )
                # earnings 取得失敗時は通常 trailing で続行（推測しない）

            # 売却判定
            triggered = should_trigger_stop(
                current_price=current_price, trail_price=trail_result.trail_price
            )
            if not triggered:
                continue

            # 重複登録防止: 同じ銘柄に未処理の sell Decision がないか確認
            existing = s.exec(
                select(Decision)
                .where(col(Decision.ticker) == p.ticker)
                .where(col(Decision.action).in_(("sell_profit", "sell_loss")))
                .where(col(Decision.status).in_(("verifying", "awaiting", "approved")))
            ).first()
            if existing is not None:
                continue

            # action 判定: 利益確保なら sell_profit、損切りなら sell_loss
            action = "sell_profit" if trail_result.is_profit_locked else "sell_loss"
            reason = (
                f"trailing stop {trail_result.tier_label} → "
                f"trail_price ¥{int(trail_result.trail_price):,} 到達"
            )
            new_dec = Decision(
                date=today,
                ticker=p.ticker,
                action=action,
                status="approved",  # 自動承認（規律による執行）
                gendo_stance="撤退" if action == "sell_loss" else "利確",
                thesis_at_decision=reason,
                entry_price=entry_price,
                stop_pct=abs(trail_result.effective_stop_pct),
            )
            s.add(new_dec)
            stop_triggered.append(
                {
                    "ticker": p.ticker,
                    "personality": p.personality,
                    "current_price": current_price,
                    "trail_price": round(trail_result.trail_price, 2),
                    "effective_stop_pct": trail_result.effective_stop_pct,
                    "action": action,
                    "tier_label": trail_result.tier_label,
                }
            )
        s.commit()

    _log.info(
        "trailing_check_done",
        checked=len(active_ports),
        triggered=len(stop_triggered),
        skipped_no_price=len(skipped_no_price),
        skipped_not_in_universe=len(skipped_not_in_universe),
    )
    return {
        "status": "active",
        "checked": len(active_ports),
        "stop_triggered": stop_triggered,
        "skipped_no_price": skipped_no_price,
        "skipped_not_in_universe": skipped_not_in_universe,
    }
