"""ポジションサイジング（規律層 G-1/G-2/G-4）。

精鋭トレーダーの「リスクベース・サイジング」を借用（RISK_EXOSKELETON）。
- **R-mult**：1R（許容損失）= 口座×2%。ポジション額 = 1R ÷ 損切り%（損切り広い銘柄は小さく）。
- **anti-martingale**：1Rは口座残高に対する固定%＝残高が減れば自動縮小（連敗ブレーキ）。
- **3つの制約の最小**：min(R-mult額, 1銘柄上限20%, 使える現金=現金−現金下限20%)。
- US＝端株（小数株）可。JP＝moomoo 単元未満（1株単位）。数値はすべてコード（LLM非関与）。
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.risk.params import DEFAULT_RISK, RiskParams


@dataclass
class SizeRec:
    amount_jpy: float  # 推奨投下額(¥)
    shares: float  # 推奨株数（米株は小数可、日本株は1株単位）
    weight: float  # 総資産に対する比率（0-1）
    note: str  # 補足（端株/1株単位/制約）
    stop_pct: float = 0.0  # 採用した損切り幅
    stop_price_jpy: float = 0.0  # 損切りライン（¥）
    risk_jpy: float = 0.0  # 実際の想定損失（amount×stop_pct）≈1R
    binding: str = ""  # サイズを決めた制約："r-mult" / "cap" / "cash" / "none"


def recommend_position(
    *,
    price_jpy: float,
    total_assets_jpy: float,
    cash_jpy: float,
    is_jp: bool,
    stop_pct: float | None = None,
    params: RiskParams = DEFAULT_RISK,
) -> SizeRec:
    """1銘柄の推奨ポジションを R-mult で返す。

    budget = min(1R/stop, 総資産×20%, 現金−総資産×現金下限)。JPは1株単位・USは端株。
    """
    stop = stop_pct if stop_pct is not None else params.default_stop_pct
    one_r = params.one_r_jpy(total_assets_jpy)  # anti-martingale（残高比固定%）
    rmult_amount = one_r / stop if stop > 0 else 0.0
    cap_amount = total_assets_jpy * params.max_position_weight
    spendable_cash = max(0.0, cash_jpy - total_assets_jpy * params.cash_floor)

    budget = min(rmult_amount, cap_amount, spendable_cash)
    binding = _binding(rmult_amount, cap_amount, spendable_cash)

    if price_jpy <= 0 or budget <= 0:
        reason = "現金が下限到達/予算0" if budget <= 0 else "価格が無効"
        return SizeRec(0.0, 0.0, 0.0, f"算定不可（{reason}）", stop_pct=stop, binding=binding)

    if is_jp:
        shares = float(int(budget // price_jpy))  # moomoo 単元未満＝1株単位
        amount = shares * price_jpy
        if shares <= 0:
            note = f"1株(¥{price_jpy:,.0f})が予算(¥{budget:,.0f}/制約{binding})超で購入不可"
        else:
            note = f"日本株：1株単位×{int(shares)}（moomoo単元未満）・制約{binding}"
    else:
        shares = round(budget / price_jpy, 4)  # 米株は端株可
        amount = shares * price_jpy
        note = f"米株：端株可・制約{binding}"

    weight = (amount / total_assets_jpy) if total_assets_jpy else 0.0
    return SizeRec(
        amount_jpy=round(amount),
        shares=shares,
        weight=round(weight, 4),
        note=note,
        stop_pct=stop,
        stop_price_jpy=round(price_jpy * (1.0 - stop)),
        risk_jpy=round(amount * stop),
        binding=binding,
    )


def _binding(rmult: float, cap: float, cash: float) -> str:
    """最小の制約名を返す（透明性：なぜこのサイズか）。"""
    smallest = min(rmult, cap, cash)
    if smallest <= 0:
        return "cash"
    if smallest == rmult:
        return "r-mult"
    if smallest == cap:
        return "cap"
    return "cash"
