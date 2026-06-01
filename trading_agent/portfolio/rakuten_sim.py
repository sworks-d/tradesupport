"""楽天証券「かぶミニ」相当の手数料・スプレッド・スリッページを再現する。

ペーパー紙約定の各 buy/sell に対して、楽天実弾運用と同じコストを差し引くことで、
DS 4 機の検証結果が「楽天で運用したら」を正しく反映する。

参考（楽天証券かぶミニ・2024-2025 時点の一般情報）:
- 売買手数料: 完全無料（買付・売却とも）
- 寄付取引: スプレッドなし、約定価格は当日始値
- リアルタイム取引: スプレッド 0.22%（買付時は売値+0.22%、売却時は買値-0.22%）
- 取引単位: 1 株〜（単元未満株対応）
- 対象銘柄: プライム・スタンダード・グロース 約 1,750 銘柄
- 米国株: 楽天証券通常口座（円貨決済 / 外貨決済、為替手数料 25 銭/$ 程度）

朝バッチは「寄付発注」前提なので **デフォルトは寄付取引モード（スプレッド 0%）**。
場中 trailing 売却（v2.10 Phase I-13 想定）はリアルタイム取引モード（0.22%）を使う。

設計：moomoo_sim と同じインターフェース・同じ FillCost を返す（dispatcher 経由で差し替え可能）。
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.portfolio.moomoo_sim import FillCost


@dataclass(frozen=True)
class RakutenFeeSchedule:
    """楽天かぶミニの手数料スケジュール（2024-2025 時点）。"""

    # 寄付取引（朝バッチ標準）
    spread_pct_kifutsuke: float = 0.0  # 寄付はスプレッドなし
    # リアルタイム取引（場中執行用、Phase I-13 で使用）
    spread_pct_realtime: float = 0.0022  # 0.22%
    # 売買手数料（買付・売却とも完全無料）
    fee_pct: float = 0.0
    fee_cap_jpy: float = 0.0
    # 米国株（楽天通常口座、為替）
    us_fx_spread_jpy_per_usd: float = 0.25  # 為替手数料 25 銭/$
    # スリッページ（市場流動性由来、寄付の自然スリッページ）
    slippage_pct: float = 0.002  # 0.2%（moomoo と同等の市場依存値）


DEFAULT_FEES = RakutenFeeSchedule()


def _liquidity_slippage(volume_30d_avg: float | None, base: float) -> float:
    """流動性連動スリッページ（moomoo_sim と同じロジック）。"""
    if volume_30d_avg is None:
        return base * 1.5
    if volume_30d_avg < 100_000:
        return base * 2.5
    if volume_30d_avg < 1_000_000:
        return base * 1.5
    return base


def simulate_fill(
    *,
    market_price: float,
    qty: int,
    is_jp: bool,
    usdjpy: float | None = None,
    side: str = "buy",
    fees: RakutenFeeSchedule = DEFAULT_FEES,
    volume_30d_avg: float | None = None,
    realtime_trading: bool = False,
) -> FillCost:
    """1 件の紙約定に楽天かぶミニ相当の手数料・スプレッド・為替を適用する。

    Args:
        market_price: 翌寄り価格（is_jp なら JPY、US なら USD）
        qty: 約定株数（v1 整数株、楽天かぶミニは 1 株単位）
        is_jp: True なら楽天かぶミニ、False なら楽天通常口座（米国株）
        usdjpy: 為替レート（US 銘柄を JPY 換算）。US 銘柄では必須
        side: "buy" / "sell"
        fees: 楽天手数料スケジュール
        volume_30d_avg: 30 日平均出来高（流動性連動スリッページ用）
        realtime_trading: True ならリアルタイム取引（0.22% スプレッド）、
                          False なら寄付取引（スプレッドなし・朝バッチ標準）

    Returns: FillCost（moomoo_sim と同じ型）

    楽天実コスト（寄付取引）:
      - 売買手数料 0
      - スプレッド 0
      - 流動性スリッページのみ（市場由来）
      → 朝バッチ寄付発注の総コストは「市場スリッページのみ」
    """
    if not is_jp and usdjpy is None:
        raise ValueError(
            "simulate_fill (rakuten): US 銘柄では usdjpy 引数が必須"
        )

    # 1. スリッページ（流動性連動）+ スプレッド（リアルタイム取引のみ）
    effective_slip = _liquidity_slippage(volume_30d_avg, fees.slippage_pct)
    spread = fees.spread_pct_realtime if realtime_trading else fees.spread_pct_kifutsuke
    # buy: 高く約定、sell: 安く約定
    direction = 1 if side == "buy" else -1
    fill_price = market_price * (1.0 + direction * (effective_slip + spread))

    # 2. 名目金額（JPY 建て）
    if is_jp:
        notional_jpy = fill_price * qty
        fx_cost = 0.0
    else:
        # 米国株: USD → JPY 換算 + 為替手数料
        notional_usd = fill_price * qty
        notional_jpy = notional_usd * usdjpy
        fx_cost = abs(qty) * fees.us_fx_spread_jpy_per_usd

    # 3. 手数料（楽天かぶミニは完全無料、米国株も超割コースで小額無料）
    if is_jp:
        fee_jpy = 0.0  # 楽天かぶミニ完全無料
    else:
        # 米国株は楽天証券通常口座（超割の標準手数料相当）
        # 簡略化: 0.495% with cap（moomoo と同等）
        fee_pct = notional_jpy * 0.00495
        cap_jpy = 22.0 * usdjpy  # $22 cap
        fee_jpy = min(fee_pct, cap_jpy)

    # 4. スリッページコスト（参考表示）
    slippage_cost = abs(market_price - fill_price) * qty * (usdjpy if not is_jp else 1.0)

    # 5. 合計（buy なら cash 引き、sell なら cash 増・fee は減算）
    if side == "buy":
        total_cost = notional_jpy + fee_jpy + fx_cost
    else:
        total_cost = notional_jpy - fee_jpy - fx_cost

    return FillCost(
        market_price=market_price,
        fill_price=fill_price,
        qty=qty,
        notional_jpy=notional_jpy,
        fee_jpy=fee_jpy,
        fx_cost_jpy=fx_cost,
        slippage_cost_jpy=slippage_cost,
        total_cost_jpy=total_cost,
    )
