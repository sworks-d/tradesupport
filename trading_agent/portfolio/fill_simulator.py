"""broker_provider 別の fill シミュレーション dispatcher（v2.10）。

各 broker のコスト構造が違うため、broker_provider に応じて
適切な simulate_fill を呼び分ける。

設計原則:
  - すべての simulate_fill_* は同じ FillCost を返す（呼び出し側は透過的）
  - 既存 moomoo_sim.simulate_fill は維持（後方互換）
  - 新規 rakuten_sim.simulate_fill は楽天かぶミニ用
  - kabu.com / SBI 等は将来追加（Phase 2 以降）

broker_provider 別の選択:
  - "moomoo"          → moomoo_sim（単元株 0.088% / 単元未満 0%）
  - "rakuten"         → rakuten_sim（寄付 0% / リアルタイム 0.22%）
  - "sbi"             → rakuten_sim 流用（S 株も同等のコスト構造、専用は将来）
  - "monex"           → rakuten_sim 流用
  - "kabucom"         → moomoo_sim 流用（プチ株 52 円/取引、将来は kabucom_sim）
  - "fractional"      → rakuten_sim 流用（仮想 broker、最良ケース）
  - 不明 / 未設定     → moomoo_sim（既存挙動互換）
"""

from __future__ import annotations

from trading_agent.portfolio.moomoo_sim import FillCost


def simulate_fill_for_provider(
    *,
    broker_provider: str,
    market_price: float,
    qty: int,
    is_jp: bool,
    usdjpy: float | None = None,
    side: str = "buy",
    volume_30d_avg: float | None = None,
    realtime_trading: bool = False,
) -> FillCost:
    """broker_provider に応じて適切な simulate_fill を呼び分ける。

    Args:
        broker_provider: "moomoo" / "rakuten" / "sbi" / "monex" / "kabucom" / "fractional"
        realtime_trading: 楽天系のみ意味あり（リアルタイム取引フラグ）
        他: 各 simulate_fill と同じ
    """
    if broker_provider in ("rakuten", "sbi", "monex", "fractional"):
        from trading_agent.portfolio.rakuten_sim import simulate_fill as _rakuten_fill

        return _rakuten_fill(
            market_price=market_price,
            qty=qty,
            is_jp=is_jp,
            usdjpy=usdjpy,
            side=side,
            volume_30d_avg=volume_30d_avg,
            realtime_trading=realtime_trading,
        )

    # デフォルト: moomoo_sim（"moomoo" / "kabucom" / 不明）
    from trading_agent.portfolio.moomoo_sim import simulate_fill as _moomoo_fill

    return _moomoo_fill(
        market_price=market_price,
        qty=qty,
        is_jp=is_jp,
        usdjpy=usdjpy,
        side=side,
        volume_30d_avg=volume_30d_avg,
    )
