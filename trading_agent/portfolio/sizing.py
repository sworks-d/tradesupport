"""ポジションサイジング（実稼働：予算内で何株／いくら買うか）。

原則（MASTER §1.4 / STEP_C：分散）：1銘柄は総資産の ``max_weight``（既定20%）まで、
かつ現金を超えない。米株は端株（小数株）可、日本株は単元（100株）単位。
数値はコードが計算する（LLMに計算させない）。
"""

from __future__ import annotations

from dataclasses import dataclass

_JP_LOT = 100  # 日本株の単元


@dataclass
class SizeRec:
    amount_jpy: float  # 推奨投下額(¥)
    shares: float  # 推奨株数（米株は小数可、日本株は単元の倍数）
    weight: float  # 総資産に対する比率（0-1）
    note: str  # 補足（端株/単元/予算制約）


def recommend_position(
    *,
    price_jpy: float,
    total_assets_jpy: float,
    cash_jpy: float,
    is_jp: bool,
    max_weight: float = 0.20,
) -> SizeRec:
    """1銘柄の推奨ポジションを返す。上限＝min(総資産×max_weight, 現金)。"""
    budget = min(total_assets_jpy * max_weight, cash_jpy)
    if price_jpy <= 0 or budget <= 0:
        return SizeRec(0.0, 0.0, 0.0, "価格・予算が無効で算定不可")

    if is_jp:
        lots = int(budget // (price_jpy * _JP_LOT))
        shares = float(lots * _JP_LOT)
        amount = shares * price_jpy
        note = (
            f"日本株：単元{_JP_LOT}株×{lots}（端株不可）"
            if shares > 0
            else f"1単元(¥{price_jpy * _JP_LOT:,.0f})が予算({max_weight:.0%})超で購入不可"
        )
    else:
        shares = round(budget / price_jpy, 4)  # 米株は端株可
        amount = shares * price_jpy
        note = "米株：端株可"

    weight = (amount / total_assets_jpy) if total_assets_jpy else 0.0
    return SizeRec(round(amount), shares, round(weight, 4), note)
