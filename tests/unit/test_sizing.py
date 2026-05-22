"""ポジションサイジングの単体テスト（実稼働：予算内で何株/いくら）。"""

from __future__ import annotations

from trading_agent.portfolio import recommend_position

TOTAL = 1_000_000.0  # 仮の総資産


def test_us_fractional_within_20pct() -> None:
    # NVDA ¥45,750/株（$305×150）。上限20%＝¥200,000 → 端株で約4.37株
    rec = recommend_position(
        price_jpy=45_750.0, total_assets_jpy=TOTAL, cash_jpy=TOTAL, is_jp=False
    )
    assert rec.amount_jpy <= TOTAL * 0.20 + 1
    assert 0 < rec.weight <= 0.20 + 1e-6
    assert rec.shares > 4  # 端株可
    assert "端株" in rec.note


def test_jp_lot_unit() -> None:
    # 安い日本株 ¥1,500/株。上限¥200,000 → 単元100株なら ¥150,000=1単元
    rec = recommend_position(
        price_jpy=1_500.0, total_assets_jpy=TOTAL, cash_jpy=TOTAL, is_jp=True
    )
    assert rec.shares % 100 == 0  # 単元の倍数
    assert rec.shares == 100
    assert rec.amount_jpy == 150_000


def test_jp_unaffordable_lot_returns_zero() -> None:
    # トヨタ ¥2,987×100=¥298,700/単元 は 20%(¥200,000)超 → 購入不可
    rec = recommend_position(
        price_jpy=2_987.0, total_assets_jpy=TOTAL, cash_jpy=TOTAL, is_jp=True
    )
    assert rec.shares == 0
    assert "購入不可" in rec.note


def test_cash_constrains_budget() -> None:
    # 現金が少なければ 20%上限ではなく現金が制約
    rec = recommend_position(
        price_jpy=10_000.0, total_assets_jpy=TOTAL, cash_jpy=50_000.0, is_jp=False
    )
    assert rec.amount_jpy <= 50_000
