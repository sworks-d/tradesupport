"""moomoo 実弾運用相当の手数料・スリッページ・為替スプレッドを再現する。

ペーパー紙約定の各 buy/sell に対して、実弾運用と同じコストを差し引くことで、
DS 4 機の検証結果が「実弾化したらどうなるか」を正しく反映する。

参考:
- moomoo JP 単元未満（1 株単位）: 手数料 0%（D-25 で採用理由として明記）
- moomoo JP 単元（100 株）: 0.088% (税込) / 上限 ¥1,070
- moomoo US ETF: 0.495% (税込) / 上限 ¥22 USD 相当
- 為替 USD/JPY: 25 銭/$ のスプレッド
- 寄付スリッページ: 流動性により ±0.1-0.5%、中央値 ±0.2% を採用

設計：純粋関数。引数で全部の制約を受け取り、`FillResult` で結果を返す。
テストしやすい。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoomooFeeSchedule:
    """moomoo 実費の手数料スケジュール（2026-05 時点）。"""

    jp_single_unit_pct: float = 0.0  # 単元未満: 0%（D-25 採用理由）
    jp_full_unit_pct: float = 0.00088  # 単元 (100 株): 0.088% 税込
    jp_fee_cap_jpy: float = 1_070.0  # JP 上限
    us_etf_pct: float = 0.00495  # US ETF: 0.495% 税込
    us_fee_cap_usd: float = 22.0  # US 上限
    fx_spread_jpy_per_usd: float = 0.25  # USD/JPY スプレッド 25 銭/$
    slippage_pct: float = 0.002  # 寄付スリッページ 0.2%


DEFAULT_FEES = MoomooFeeSchedule()


@dataclass(frozen=True)
class FillCost:
    """1 件の約定にかかる実費の内訳。"""

    market_price: float  # 市場価格（slippage 適用前）
    fill_price: float  # 実約定価格（slippage 適用後）
    qty: int
    notional_jpy: float  # fill_price × qty × fx
    fee_jpy: float  # 手数料
    fx_cost_jpy: float  # 為替スプレッド分（JP は 0）
    slippage_cost_jpy: float  # スリッページ分（参考）
    total_cost_jpy: float  # 現金から引かれる合計

    @property
    def all_in_pct(self) -> float:
        """市場価格に対する all-in コスト率（参考表示用）。"""
        if self.market_price == 0:
            return 0.0
        base = self.market_price * self.qty
        return (self.total_cost_jpy - base) / base if base else 0.0


def _liquidity_slippage(volume_30d_avg: float | None, base: float) -> float:
    """v2.4 TASK-F1: 流動性に応じて slippage を変動させる。

    出来高が薄い銘柄ほど slippage が大きい（実弾相当）。
    - volume_30d_avg ≥ 1M shares/day → base そのまま
    - volume_30d_avg <  1M           → 1.5x
    - volume_30d_avg <  100k         → 2.5x
    - 取得不能 (None)                → base × 1.5（保守側）
    """
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
    side: str = "buy",  # "buy" / "sell"
    fees: MoomooFeeSchedule = DEFAULT_FEES,
    volume_30d_avg: float | None = None,
) -> FillCost:
    """1 件の紙約定に moomoo 相当の手数料・スリッページ・為替を適用する。

    - `market_price`: 翌寄り価格（is_jp なら JPY、US なら USD のいずれか前提）
    - `qty`: 約定株数（v1 整数株）
    - `is_jp`: True なら単元未満手数料 0、US ETF は 0.495%
    - `usdjpy`: 為替レート（US 銘柄を JPY 換算）。v2.4 TASK-F2: US 銘柄では必須
    - `side`: buy だと cash 引き、sell だと cash 増（fee は両方発生）

    Returns: FillCost

    v2.4 TASK-F2: usdjpy 既定値 159.0 を撤去。US 銘柄で usdjpy=None なら ValueError。
    （米国為替を引数で受け取る or 取得失敗を明示的に検出するため）
    """
    if not is_jp and usdjpy is None:
        raise ValueError(
            "simulate_fill: US 銘柄では usdjpy 引数が必須（実レートを渡してください）"
        )
    # 1. スリッページ（買い: 高く約定 / 売り: 安く約定）。v2.4 TASK-F1: 流動性連動
    effective_slip = _liquidity_slippage(volume_30d_avg, fees.slippage_pct)
    direction = 1 if side == "buy" else -1
    fill_price = market_price * (1.0 + direction * effective_slip)

    # 2. 名目金額（JPY 建て）
    if is_jp:
        notional_jpy = fill_price * qty
        fx_cost = 0.0
    else:
        # US: USD → JPY 換算 + スプレッド
        notional_usd = fill_price * qty
        notional_jpy = notional_usd * usdjpy
        fx_cost = abs(qty) * fees.fx_spread_jpy_per_usd

    # 3. 手数料
    if is_jp:
        # 単元未満（< 100 株）なら 0%、それ以外は 0.088% with cap
        if qty < 100:
            fee_jpy = 0.0
        else:
            fee_jpy = min(notional_jpy * fees.jp_full_unit_pct, fees.jp_fee_cap_jpy)
    else:
        # US ETF: 0.495% with cap
        fee_pct = notional_jpy * fees.us_etf_pct
        cap_jpy = fees.us_fee_cap_usd * usdjpy
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
