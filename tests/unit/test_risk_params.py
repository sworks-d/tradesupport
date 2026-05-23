"""規律層パラメータ（外骨格 G-0）の整合性テスト。

数値が「制約系として閉じている」ことをコードで保証する（doc の主張をテストで固定）。
"""

from __future__ import annotations

from trading_agent.risk.params import DEFAULT_RISK, RiskParams


def test_capital_100k_one_r_is_2000() -> None:
    p = RiskParams()
    assert p.one_r_jpy(100_000.0) == 2_000.0  # ① 2% = ¥2,000 = 1R


def test_anti_martingale_one_r_shrinks_with_balance() -> None:
    p = RiskParams()
    assert p.one_r_jpy(80_000.0) < p.one_r_jpy(100_000.0)  # 残高が減れば1Rも縮む


def test_stop10_rmult_equals_cap() -> None:
    # stop10% × 1R(¥2,000) = ¥20,000 = 20%上限ちょうど（噛み合い）
    one_r = DEFAULT_RISK.one_r_jpy(100_000.0)
    rmult_pos = one_r / 0.10
    cap = 100_000.0 * DEFAULT_RISK.max_position_weight
    assert rmult_pos == cap == 20_000.0


def test_position_count_closes_with_cash_floor() -> None:
    # 投資可能 ÷ R-multポジション が max_positions に収まる（②の整合）
    investable = 100_000.0 * DEFAULT_RISK.investable_fraction()  # ¥80,000
    one_r = DEFAULT_RISK.one_r_jpy(100_000.0)
    pos_at_stop10 = one_r / 0.10  # ¥20,000（最大サイズ）
    pos_at_stop15 = one_r / 0.15  # ¥13,333（最小サイズ）
    assert int(investable // pos_at_stop10) == 4  # 全部フルサイズなら4銘柄
    assert int(round(investable / pos_at_stop15)) == 6  # 浅めstopなら最大6銘柄
    assert 4 <= DEFAULT_RISK.max_positions <= 6  # 中央値5に整合


def test_stop_default_within_band() -> None:
    p = RiskParams()
    assert p.stop_pct_min <= p.default_stop_pct <= p.stop_pct_max


def test_invariants_sane() -> None:
    p = RiskParams()
    assert 0 < p.risk_per_trade < 0.1
    assert 0 < p.cash_floor < 1
    assert 0 < p.max_theme_weight <= 1
    assert p.max_per_theme <= p.max_positions
