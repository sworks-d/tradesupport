"""S7：V字（ターンアラウンド）質判定の単体テスト。"""

from __future__ import annotations

from trading_agent.screening.credibility import CredibilityResult, ScoreResult
from trading_agent.screening.financials import Financials, PeriodFinancials
from trading_agent.screening.turnaround import (
    assess_turnaround,
    derive_earnings_signal_tags,
)


def _pf(period: str, *, ebit=None, revenue=None, net_income=None) -> PeriodFinancials:
    return PeriodFinancials(period=period, ebit=ebit, revenue=revenue, net_income=net_income)


def _fin(cur, prior=None, prior2=None) -> Financials:
    return Financials(ticker="X", current=cur, prior=prior, prior2=prior2)


def _cred(z="safe", f="safe") -> CredibilityResult:
    sr = lambda name, zone: ScoreResult(name, 0.0, zone, "")  # noqa: E731
    return CredibilityResult(
        m_score=sr("Beneish M", "safe"), f_score=sr("Piotroski F", f),
        z_score=sr("Altman Z", z), credibility_flag="ok",
    )


def test_not_a_bottom_is_not_applicable() -> None:
    # マージン健全（30%）→ ターンアラウンド対象外
    fin = _fin(_pf("2026", ebit=300.0, revenue=1000.0))
    assert assess_turnaround(fin).zone == "not_applicable"


def test_bottom_without_ignition_is_value_trap() -> None:
    # 底（マージン2%）だが反転の点火なし（マージン悪化）→ value trap
    cur = _pf("2026", ebit=20.0, revenue=1000.0)  # 2%
    prior = _pf("2025", ebit=50.0, revenue=1000.0)  # 5% → 悪化
    assert assess_turnaround(_fin(cur, prior)).zone == "value_trap"


def test_v_candidate_when_all_axes_align() -> None:
    # 底（2%）＋点火（マージンYoY改善 1%→2%）＋株価転換（GC）＋生存性OK → V字候補
    cur = _pf("2026", ebit=20.0, revenue=1000.0)  # 2%
    prior = _pf("2025", ebit=10.0, revenue=1000.0)  # 1% → 改善
    res = assess_turnaround(
        _fin(cur, prior), signals=["golden_cross"], credibility=_cred()
    )
    assert res.zone == "v_candidate"
    assert res.axes["bottom"] and res.axes["ignition"] and res.axes["price_turn"]


def test_earnings_acceleration_with_three_periods() -> None:
    # 3期：純益成長率が加速（-50%→0%→+100%）→ ignition True
    cur = _pf("2026", ebit=20.0, revenue=1000.0, net_income=200.0)
    prior = _pf("2025", ebit=15.0, revenue=1000.0, net_income=100.0)  # g=+100%
    prior2 = _pf("2024", ebit=10.0, revenue=1000.0, net_income=100.0)  # g(prior)=0%
    res = assess_turnaround(
        _fin(cur, prior, prior2), signals=["golden_cross"], credibility=_cred()
    )
    assert res.axes["ignition"] is True
    assert res.zone == "v_candidate"


# === A prime: derive_earnings_signal_tags（J-Quants 由来 earnings_accel・record-only）===


def test_derive_earnings_signal_tags_fires_on_ignition() -> None:
    """純益成長が加速（ignition True）→ earnings_accel タグ + 証拠メタを返す。"""
    cur = _pf("2026", ebit=20.0, revenue=1000.0, net_income=240.0)   # g=+100% vs 120
    prior = _pf("2025", ebit=15.0, revenue=1000.0, net_income=120.0)  # g=+20% vs 100
    prior2 = _pf("2024", ebit=10.0, revenue=1000.0, net_income=100.0)
    tags, evidence = derive_earnings_signal_tags(_fin(cur, prior, prior2))
    assert tags == ["earnings_accel"]
    assert evidence["earnings_accel"]["net_income"] == 240.0
    assert evidence["earnings_accel"]["asof"] == "2026"  # 期末で開示 recency 近似


def test_derive_earnings_signal_tags_empty_on_deceleration() -> None:
    """成長が減速（ignition False）→ タグなし（推測しない）。"""
    cur = _pf("2026", ebit=20.0, revenue=1000.0, net_income=110.0)   # g=+10%
    prior = _pf("2025", ebit=15.0, revenue=1000.0, net_income=100.0)  # g=+100%
    prior2 = _pf("2024", ebit=10.0, revenue=1000.0, net_income=50.0)
    tags, evidence = derive_earnings_signal_tags(_fin(cur, prior, prior2))
    assert tags == [] and evidence == {}


def test_derive_earnings_signal_tags_none_fin() -> None:
    """fin 不在 → 空（H10 推測しない）。"""
    assert derive_earnings_signal_tags(None) == ([], {})


def test_bottom_ignition_but_no_price_turn_is_value_trap() -> None:
    cur = _pf("2026", ebit=20.0, revenue=1000.0)
    prior = _pf("2025", ebit=10.0, revenue=1000.0)
    res = assess_turnaround(_fin(cur, prior), signals=["death_cross"], credibility=_cred())
    assert res.zone == "value_trap"
    assert "株価未転換" in res.note


def test_survival_risk_blocks_v_candidate() -> None:
    # 底＋点火＋株価転換でも、倒産リスク（Z risk）なら V字候補にしない
    cur = _pf("2026", ebit=20.0, revenue=1000.0)
    prior = _pf("2025", ebit=10.0, revenue=1000.0)
    res = assess_turnaround(
        _fin(cur, prior), signals=["golden_cross"], credibility=_cred(z="risk")
    )
    assert res.zone == "value_trap"


def test_missing_margin_is_na() -> None:
    fin = _fin(_pf("2026", revenue=1000.0))  # ebit 欠損
    assert assess_turnaround(fin).zone == "na"
