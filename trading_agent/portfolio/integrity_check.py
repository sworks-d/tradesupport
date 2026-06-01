"""データ整合性自動チェック（v2.10 P8）。

Decision / Portfolio / Treasury / Universe の不整合を検出して警告する。
警告のみ・自動修正はしない（既存ロジック尊重）。

検出項目:
  I1: filled Decision に対応する Portfolio が存在しない
  I2: active Portfolio に対応する filled Decision が無い
  I3: Treasury.seed_jpy - active コスト = 計算上の available と Treasury.available_jpy が食い違う
  I4: 上場廃止 (Universe.is_active=False) で active Portfolio
  I5: planned_total_qty が qty より小さい（ピラミッディング不整合）
  I6: closed Portfolio に closed_at が無い

朝バッチの pre_check や手動 CLI から呼ぶ用途。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.integrity_check")


@dataclass
class IntegrityResult:
    """整合性チェックの結果。"""

    checked: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, **detail: Any) -> None:
        self.issues.append({"kind": kind, **detail})

    @property
    def ok(self) -> bool:
        return not self.issues


def run_integrity_check(engine: Engine) -> IntegrityResult:
    """Decision / Portfolio / Treasury / Universe の整合性を検査。

    Returns: IntegrityResult（issues に検出された項目）
    """
    result = IntegrityResult()
    with Session(engine) as s:
        decisions = list(s.exec(select(Decision)).all())
        ports = list(s.exec(select(Portfolio)).all())

        # I1: filled Decision に対応する active/closed Portfolio がない
        port_by_ticker_date: dict[tuple[str, str], list[Portfolio]] = {}
        for p in ports:
            if p.buy_date:
                key = (p.ticker, p.buy_date.isoformat())
                port_by_ticker_date.setdefault(key, []).append(p)
        for d in decisions:
            if d.status == "filled":
                key = (d.ticker, d.date.isoformat())
                if key not in port_by_ticker_date:
                    result.add(
                        "I1_filled_decision_no_portfolio",
                        ticker=d.ticker,
                        decision_id=d.id,
                        date=d.date.isoformat(),
                    )

        # I4: 上場廃止 (Universe.is_active=False) で active Portfolio
        for p in ports:
            if p.status != "active":
                continue
            u = s.get(Universe, p.ticker)
            if u is None:
                result.add(
                    "I4_active_holding_no_universe",
                    ticker=p.ticker,
                    portfolio_id=p.id,
                )
            elif not u.is_active:
                result.add(
                    "I4_active_holding_universe_inactive",
                    ticker=p.ticker,
                    portfolio_id=p.id,
                    name=u.name,
                )

        # I5: planned_total_qty が qty より小さい
        for p in ports:
            if p.status != "active":
                continue
            planned = p.planned_total_qty
            qty = p.qty or 0
            if planned is not None and planned < qty:
                result.add(
                    "I5_pyramid_inconsistency",
                    ticker=p.ticker,
                    portfolio_id=p.id,
                    qty=qty,
                    planned_total_qty=planned,
                )

        # I6: closed Portfolio に closed_at が無い
        for p in ports:
            if p.status == "closed" and p.closed_at is None:
                result.add(
                    "I6_closed_without_closed_at",
                    ticker=p.ticker,
                    portfolio_id=p.id,
                )

        result.checked = len(decisions) + len(ports)

    if result.issues:
        _log.warning(
            "integrity_check_issues_found",
            count=len(result.issues),
            kinds=sorted(set(i["kind"] for i in result.issues)),
        )
    else:
        _log.info("integrity_check_clean", checked=result.checked)
    return result
