"""損出し loss harvesting（v2.10 Phase 6-1）。

含み損が一定額を超えた銘柄をリストアップし、年内の含み益と相殺して
**税引後リターンを最適化**する候補を提案する。

JP 株の税制（2026 年現在）:
  - 特定口座 (源泉あり): 譲渡益課税 20.315%
  - 年内損益通算: 同年内の譲渡益と譲渡損を通算可能
  - 翌年への繰越: 確定申告で 3 年繰越可能

ハルシネーション対策:
  - 現在価格が取れない銘柄は除外（推測しない）
  - 「税効果」は計算式に基づくのみ・LLM 推論なし
  - 含み益・含み損は実取得コストベース（推測しない）
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.loss_harvesting")

# 譲渡益課税率（特定口座源泉あり前提）
_TAX_RATE = 0.20315

# 「損出し対象」になる最低含み損 %（これ未満は無視）
_MIN_LOSS_PCT_FOR_HARVEST = 0.05


def find_harvest_candidates(
    engine: Engine,
    *,
    current_price_lookup: dict[str, float],
    broker_mode: str = "paper",
    realized_gain_jpy: float = 0.0,
) -> dict[str, Any]:
    """損出し候補を抽出する。

    Args:
        engine: DB エンジン
        current_price_lookup: {ticker: current_price} の辞書（None は除外）
        broker_mode: "paper" or "live"
        realized_gain_jpy: 年初来の実現益（これと相殺できる範囲で売却推奨）

    Returns:
        {
            "status": "active" | "insufficient_data",
            "candidates": [
                {ticker, qty, cost_basis_jpy, current_value_jpy,
                 unrealized_loss_jpy, loss_pct, tax_saving_jpy, ...},
                ...
            ],
            "total_loss_jpy": float,
            "harvestable_loss_jpy": float (実現益と相殺できる範囲),
            "estimated_tax_saving_jpy": float,
        }
    """
    with Session(engine) as s:
        ports = list(
            s.exec(
                select(Portfolio)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
        )

    if not ports:
        return _empty_result(reason="no_holdings")

    candidates: list[dict[str, Any]] = []
    total_loss = 0.0

    for p in ports:
        current_price = current_price_lookup.get(p.ticker)
        if current_price is None or current_price <= 0:
            continue
        cost_basis = float(p.buy_price or 0) * float(p.qty or 0)
        current_value = current_price * float(p.qty or 0)
        unrealized_pnl = current_value - cost_basis
        if cost_basis <= 0:
            continue
        loss_pct = unrealized_pnl / cost_basis
        # 含み損が一定以上の銘柄のみ対象（推測しない）
        if loss_pct >= -_MIN_LOSS_PCT_FOR_HARVEST:
            continue
        loss_jpy = abs(unrealized_pnl)
        # 個別の節税推定（loss × 税率）
        tax_saving = loss_jpy * _TAX_RATE
        total_loss += loss_jpy
        candidates.append(
            {
                "ticker": p.ticker,
                "personality": p.personality,
                "qty": int(p.qty or 0),
                "buy_price": float(p.buy_price or 0),
                "current_price": current_price,
                "cost_basis_jpy": round(cost_basis, 0),
                "current_value_jpy": round(current_value, 0),
                "unrealized_loss_jpy": round(unrealized_pnl, 0),
                "loss_pct": round(loss_pct * 100, 2),
                "individual_tax_saving_jpy": round(tax_saving, 0),
            }
        )

    if not candidates:
        return _empty_result(
            reason="no_loss_candidates",
            total_loss_jpy=0.0,
            realized_gain_jpy=realized_gain_jpy,
        )

    # 含み損の大きい順に
    candidates.sort(key=lambda x: x["unrealized_loss_jpy"])

    # 実現益と相殺できる範囲
    harvestable = min(total_loss, max(realized_gain_jpy, 0.0))
    estimated_saving = harvestable * _TAX_RATE

    return {
        "status": "active",
        "candidates": candidates,
        "total_loss_jpy": round(total_loss, 0),
        "realized_gain_jpy": round(realized_gain_jpy, 0),
        "harvestable_loss_jpy": round(harvestable, 0),
        "estimated_tax_saving_jpy": round(estimated_saving, 0),
        "min_loss_threshold_pct": _MIN_LOSS_PCT_FOR_HARVEST * 100,
    }


def _empty_result(
    *,
    reason: str,
    total_loss_jpy: float = 0.0,
    realized_gain_jpy: float = 0.0,
) -> dict[str, Any]:
    return {
        "status": "insufficient_data" if reason == "no_holdings" else "no_candidates",
        "candidates": [],
        "total_loss_jpy": total_loss_jpy,
        "realized_gain_jpy": realized_gain_jpy,
        "harvestable_loss_jpy": 0.0,
        "estimated_tax_saving_jpy": 0.0,
        "min_loss_threshold_pct": _MIN_LOSS_PCT_FOR_HARVEST * 100,
        "reason": reason,
    }
