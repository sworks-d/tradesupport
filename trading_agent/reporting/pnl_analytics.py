"""損益分布 + 銘柄別 / 戦略別 P/L 集計（v2.10 P5/P7）。

Portfolio テーブルから closed/active を集計し、運用判断の根拠を提供。

集計軸:
  - broker_mode 別 (paper / live)
  - personality 別 (REI/ASUKA/KAWORU/SHINJI/None)
  - strategy_category 別 (短期/中期/長期)
  - sector 別 (Universe.sector 経由)

メトリクス:
  - 累積 P/L (realized / unrealized)
  - 勝率
  - 平均リターン / 中央値 / 標準偏差
  - 最大ドローダウン (closed のみ)
  - 損益分布ヒストグラム
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("reporting.pnl_analytics")


@dataclass
class PnLSummary:
    """1 グループ (broker_mode/personality/etc) の P/L 集計。"""

    label: str
    closed_count: int = 0
    active_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    realized_pnl_jpy: float = 0.0
    returns: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        decided = self.win_count + self.loss_count
        return self.win_count / decided if decided > 0 else 0.0

    @property
    def avg_return_pct(self) -> float:
        return statistics.mean(self.returns) if self.returns else 0.0

    @property
    def median_return_pct(self) -> float:
        return statistics.median(self.returns) if self.returns else 0.0

    @property
    def stdev_return_pct(self) -> float:
        return statistics.stdev(self.returns) if len(self.returns) >= 2 else 0.0


def aggregate_pnl(
    engine: Engine,
    *,
    broker_mode: str | None = None,
    group_by: str = "personality",
) -> dict[str, PnLSummary]:
    """Portfolio を group_by の軸で集計。

    Args:
      group_by: "personality" / "strategy_category" / "sector" / "broker_mode"
    """
    summaries: dict[str, PnLSummary] = {}
    with Session(engine) as s:
        stmt = select(Portfolio)
        if broker_mode is not None:
            stmt = stmt.where(col(Portfolio.broker_mode) == broker_mode)
        ports = list(s.exec(stmt).all())

        # group_by="sector" の時は Universe を join
        ticker_to_sector: dict[str, str] = {}
        if group_by == "sector":
            for u in s.exec(select(Universe)).all():
                ticker_to_sector[u.ticker] = u.sector or "—"

        for p in ports:
            # キー決定
            if group_by == "personality":
                key = p.personality or "—"
            elif group_by == "strategy_category":
                key = p.strategy_category or "—"
            elif group_by == "sector":
                key = ticker_to_sector.get(p.ticker, "—")
            elif group_by == "broker_mode":
                key = p.broker_mode or "—"
            else:
                key = "—"
            if key not in summaries:
                summaries[key] = PnLSummary(label=key)
            sm = summaries[key]
            if p.status == "closed":
                sm.closed_count += 1
                if p.closed_price and p.buy_price and p.qty:
                    pnl = (p.closed_price - p.buy_price) * p.qty
                    sm.realized_pnl_jpy += pnl
                    if p.buy_price > 0:
                        sm.returns.append(
                            (p.closed_price - p.buy_price) / p.buy_price
                        )
                    if pnl > 0:
                        sm.win_count += 1
                    elif pnl < 0:
                        sm.loss_count += 1
            elif p.status == "active":
                sm.active_count += 1
    return summaries


def distribution_buckets(
    returns: list[float], *, n_buckets: int = 10
) -> list[tuple[float, float, int]]:
    """returns（小数比）を n バケットに区切ってヒストグラムを作る。

    Returns:
      list of (bucket_min, bucket_max, count)
    """
    if not returns:
        return []
    lo = min(returns)
    hi = max(returns)
    if lo == hi:
        return [(lo, hi, len(returns))]
    width = (hi - lo) / n_buckets
    buckets = [0] * n_buckets
    for r in returns:
        idx = min(n_buckets - 1, int((r - lo) / width))
        buckets[idx] += 1
    return [(lo + i * width, lo + (i + 1) * width, buckets[i]) for i in range(n_buckets)]


def render_daily_report_extension(engine: Engine) -> str:
    """日次レポートに追加する集計セクション HTML（P5）。"""
    sections: list[str] = []

    for broker_mode in ("paper", "live"):
        for group_by, title in [
            ("personality", "DS 機別"),
            ("strategy_category", "戦略カテゴリ別"),
            ("sector", "セクター別"),
        ]:
            summaries = aggregate_pnl(engine, broker_mode=broker_mode, group_by=group_by)
            if not summaries:
                continue
            rows = []
            for label, sm in sorted(
                summaries.items(), key=lambda x: -x[1].realized_pnl_jpy
            ):
                pnl_color = (
                    "#4ade80"
                    if sm.realized_pnl_jpy > 0
                    else "#f87171"
                    if sm.realized_pnl_jpy < 0
                    else "#888"
                )
                rows.append(
                    f"<tr><td>{label}</td>"
                    f"<td>{sm.active_count}</td>"
                    f"<td>{sm.closed_count}</td>"
                    f"<td>{sm.win_count}勝/{sm.loss_count}敗</td>"
                    f"<td>{sm.win_rate*100:.0f}%</td>"
                    f'<td style="color:{pnl_color}">¥{int(sm.realized_pnl_jpy):,}</td>'
                    f"<td>{sm.avg_return_pct*100:.2f}%</td>"
                    f"</tr>"
                )
            sections.append(
                f'<div class="card"><h2>📊 {broker_mode} {title} P/L</h2>'
                f"<table>"
                f"<tr><th>{group_by}</th><th>active</th><th>closed</th><th>勝敗</th>"
                f"<th>勝率</th><th>累積 P/L</th><th>平均%</th></tr>"
                f"{''.join(rows)}</table></div>"
            )

    return "\n".join(sections) if sections else ""
