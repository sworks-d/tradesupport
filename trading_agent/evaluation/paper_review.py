"""P4-4 ペーパー評価の向け直し：守りはリターンで測らない。

守り（自爆回避）の価値は「稀な大惨事の回避＝保険」で、数ヶ月では見えない（見えなくて正常）。
よって測るのは：
  ① プロセス遵守（規律を守れたか）＝利確で刻まない(B')・現金下限・1銘柄上限・保有数上限。
  ② （履歴が貯まれば）コア vs パッシブのリスク調整（Sharpe/最大DD）。
命中率/平均R は副次（払戻比の産物になり得る＝過信しない）。全てコード計算（LLM非関与）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import SellSignal
from trading_agent.risk.params import DEFAULT_RISK, RiskParams


@dataclass
class ProcessFinding:
    rule: str
    ok: bool
    detail: str


def check_process_adherence(
    engine: Engine, *, cash_jpy: float, params: RiskParams = DEFAULT_RISK
) -> list[ProcessFinding]:
    """現在の紙ポートフォリオが規律を守れているかを点検（守りの本体＝プロセス遵守）。

    ウェイトは取得原価(buy_price×qty・JPY)を用いた近似。利確トリムの痕跡は active な
    profit_taking シグナルの有無で見る（B' 後はゼロのはず）。
    """
    with Session(engine) as s:
        positions = list(s.exec(select(Portfolio).where(col(Portfolio.status) == "active")))
        profit_caps = list(
            s.exec(
                select(SellSignal)
                .where(col(SellSignal.is_active))
                .where(col(SellSignal.signal_type) == "profit_taking")
            )
        )

    invested = sum(p.buy_price * p.qty for p in positions)
    total = cash_jpy + invested
    cash_ratio = (cash_jpy / total) if total > 0 else 1.0

    findings: list[ProcessFinding] = []

    # 1) 保有数 ≤ 上限
    n = len(positions)
    findings.append(
        ProcessFinding("保有数≤上限", n <= params.max_positions, f"{n}/{params.max_positions}")
    )
    # 2) 利確で刻まない（B'）＝profit_taking シグナルが存在しない
    findings.append(
        ProcessFinding(
            "利確で刻まない(B')",
            len(profit_caps) == 0,
            "なし" if not profit_caps else f"利確シグナル {len(profit_caps)} 件（B'違反）",
        )
    )
    # 3) 現金下限 ≥ cash_floor
    findings.append(
        ProcessFinding(
            "現金下限を維持",
            cash_ratio >= params.cash_floor,
            f"現金比 {cash_ratio:.0%}（下限 {params.cash_floor:.0%}）",
        )
    )
    # 4) 1銘柄が上限ウェイト以下
    over = [p.ticker for p in positions if total > 0 and (p.buy_price * p.qty) / total
            > params.max_position_weight]
    findings.append(
        ProcessFinding(
            "1銘柄上限以下",
            not over,
            "OK" if not over else f"超過: {'・'.join(over)}",
        )
    )
    return findings


def sharpe(returns: list[float], *, periods_per_year: int = 252) -> float:
    """リターン列の年率シャープ（リスクフリー0近似）。"""
    a = np.asarray(returns, dtype=float)
    if a.size < 2 or a.std() == 0:
        return 0.0
    return float(a.mean() / a.std() * np.sqrt(periods_per_year))


def max_drawdown(equity: list[float]) -> float:
    """資産曲線の最大ドローダウン（負値）。"""
    a = np.asarray(equity, dtype=float)
    if a.size == 0:
        return 0.0
    peak = np.maximum.accumulate(a)
    return float(((a - peak) / peak).min())


def risk_adjusted_vs_passive(
    equity: list[float], passive_equity: list[float]
) -> dict[str, float]:
    """コアとパッシブを、リスク調整（Sharpe/最大DD）と超過で比較（履歴が貯まってから意味を持つ）。"""

    def rets(e: list[float]) -> list[float]:
        a = np.asarray(e, dtype=float)
        return list(np.diff(a) / a[:-1]) if a.size >= 2 else []

    core_r, pass_r = rets(equity), rets(passive_equity)
    core_total = (equity[-1] / equity[0] - 1.0) if len(equity) >= 2 else 0.0
    pass_total = (passive_equity[-1] / passive_equity[0] - 1.0) if len(passive_equity) >= 2 else 0.0
    return {
        "core_sharpe": sharpe(core_r),
        "passive_sharpe": sharpe(pass_r),
        "core_max_dd": max_drawdown(equity),
        "passive_max_dd": max_drawdown(passive_equity),
        "excess_total": core_total - pass_total,
    }
