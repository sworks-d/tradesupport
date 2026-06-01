"""ポジションサイジングの単体テスト（規律層 R-mult・¥100k版）。"""

from __future__ import annotations

from trading_agent.portfolio import recommend_position
from trading_agent.risk.params import RiskParams

TOTAL = 100_000.0  # 運用元本 ¥100,000
CASH = 100_000.0


def test_rmult_position_from_stop() -> None:
    # 1R=¥2,000、stop10% → R-mult額=¥20,000（=20%上限ちょうど）。米株端株。
    rec = recommend_position(
        price_jpy=5_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=False, stop_pct=0.10
    )
    assert rec.amount_jpy == 20_000
    assert rec.shares == 4.0
    assert rec.binding in ("r-mult", "cap")  # 10%では両者一致
    assert rec.risk_jpy == 2_000  # 想定損失=1R
    assert rec.stop_price_jpy == 4_500  # 5000×(1-0.10)


def test_wider_stop_smaller_position() -> None:
    # stopが広い銘柄は小さく：stop20% → ¥2,000/0.20=¥10,000
    rec = recommend_position(
        price_jpy=5_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=False, stop_pct=0.20
    )
    assert rec.amount_jpy == 10_000
    assert rec.binding == "r-mult"
    assert rec.risk_jpy == 2_000  # リスクは全銘柄で揃う（=1R）


def test_cap_binds_when_stop_tight() -> None:
    # stopが浅い(5%)とR-mult額¥40,000 > 20%上限¥20,000 → 上限が効く
    rec = recommend_position(
        price_jpy=5_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=False, stop_pct=0.05
    )
    assert rec.amount_jpy == 20_000  # 上限で抑制（リスクは2%未満に縮む＝保守的）
    assert rec.binding == "cap"


def test_cash_floor_constrains() -> None:
    # 現金が¥25kしか無い → 使える現金=25k−(100k×20%)=¥5,000 が制約
    rec = recommend_position(
        price_jpy=1_000.0, total_assets_jpy=TOTAL, cash_jpy=25_000.0, is_jp=False, stop_pct=0.10
    )
    assert rec.amount_jpy <= 5_000
    assert rec.binding == "cash"


def test_cash_floor_blocks_when_at_floor() -> None:
    # 現金が下限ちょうど → 新規買い不可（現金を割らない）
    rec = recommend_position(
        price_jpy=1_000.0, total_assets_jpy=TOTAL, cash_jpy=20_000.0, is_jp=False, stop_pct=0.10
    )
    assert rec.shares == 0.0
    assert "算定不可" in rec.note


def test_jp_single_share_granularity() -> None:
    # v2.2 TASK-SZ2: 端数 ≥ 0.5 なら切り上げ（予算 1.1 倍以内）
    # ¥3,000株・budget¥16,667(stop12%) → 5.56 株 → 6 株=¥18,000（予算消化 108%）
    rec = recommend_position(
        price_jpy=3_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=True, stop_pct=0.12
    )
    assert rec.shares == 6.0  # 旧 5.0 → 新 6.0（端数 0.56 で切り上げ）
    assert rec.amount_jpy == 18_000
    assert "単元未満" in rec.note


def test_high_priced_jp_rejected_by_risk() -> None:
    # 高単価JP（¥40,000/株）は1株でもR-mult予算超 → 購入不可（規律が効く）
    rec = recommend_position(
        price_jpy=40_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=True, stop_pct=0.12
    )
    assert rec.shares == 0.0
    assert "購入不可" in rec.note


def test_anti_martingale_shrinks_after_drawdown() -> None:
    # 口座が¥100k→¥80kに減ると 1R も縮み、R-mult額も縮む
    big = recommend_position(
        price_jpy=1_000.0, total_assets_jpy=100_000.0, cash_jpy=100_000.0,
        is_jp=False, stop_pct=0.20,
    )
    small = recommend_position(
        price_jpy=1_000.0, total_assets_jpy=80_000.0, cash_jpy=80_000.0, is_jp=False, stop_pct=0.20
    )
    assert small.amount_jpy < big.amount_jpy


def test_default_stop_used_when_omitted() -> None:
    rec = recommend_position(
        price_jpy=1_000.0, total_assets_jpy=TOTAL, cash_jpy=CASH, is_jp=False
    )
    assert rec.stop_pct == RiskParams().default_stop_pct  # 既定12%
