"""S5：信用性フィルタ（Beneish M / Piotroski F / Altman Z）の単体テスト。

ゾーン（safe/grey/risk/na）と集約 credibility_flag を、作り込んだ財務で検証する。
"""

from __future__ import annotations

from trading_agent.screening.credibility import (
    altman_z_score,
    assess_credibility,
    beneish_m_score,
    melchior_credibility_counter,
    piotroski_f_score,
    scan_disclosure_red_flags,
)
from trading_agent.screening.financials import Financials, PeriodFinancials


def _pf(period: str, **kw) -> PeriodFinancials:
    return PeriodFinancials(period=period, **kw)


def _fin(cur: PeriodFinancials, prior: PeriodFinancials | None, market_cap=None) -> Financials:
    return Financials(ticker="X", current=cur, prior=prior, market_cap=market_cap)


# 安定・健全（NI≈OCF・比率横ばい）→ M は safe
_CLEAN_T = dict(
    revenue=1000.0, cogs=400.0, gross_profit=600.0, net_income=200.0, sga=100.0,
    depreciation=50.0, ebit=250.0, total_assets=1000.0, current_assets=500.0,
    current_liabilities=300.0, ppe=300.0, receivables=120.0, total_liabilities=400.0,
    long_term_debt=100.0, retained_earnings=600.0, shares=10.0, working_capital=200.0,
    operating_cashflow=205.0,
)
_CLEAN_P = dict(_CLEAN_T)
_CLEAN_P.update(revenue=950.0, net_income=180.0, receivables=115.0, operating_cashflow=185.0)


class TestBeneishM:
    def test_clean_is_safe(self) -> None:
        fin = _fin(_pf("2026", **_CLEAN_T), _pf("2025", **_CLEAN_P))
        assert beneish_m_score(fin).zone in ("safe", "grey")

    def test_manipulator_is_risk(self) -> None:
        # 売掛金が売上より速く伸び(DSRI高)＋利益が現金で裏付かない(TATA高)
        t = _pf("2026", revenue=1000.0, cogs=400.0, gross_profit=600.0, net_income=300.0,
                sga=100.0, depreciation=50.0, ebit=300.0, total_assets=1000.0,
                current_assets=500.0, current_liabilities=300.0, ppe=300.0, receivables=300.0,
                long_term_debt=100.0, operating_cashflow=-50.0)
        p = _pf("2025", revenue=800.0, cogs=320.0, gross_profit=480.0, net_income=150.0,
                sga=90.0, depreciation=45.0, ebit=150.0, total_assets=900.0,
                current_assets=450.0, current_liabilities=300.0, ppe=280.0, receivables=100.0,
                long_term_debt=100.0, operating_cashflow=140.0)
        assert beneish_m_score(_fin(t, p)).zone == "risk"

    def test_single_period_is_na(self) -> None:
        assert beneish_m_score(_fin(_pf("2026", **_CLEAN_T), None)).zone == "na"


class TestPiotroskiF:
    def test_strong_is_safe(self) -> None:
        t = _pf("2026", net_income=200.0, total_assets=1000.0, operating_cashflow=250.0,
                current_assets=600.0, current_liabilities=200.0, long_term_debt=80.0,
                gross_profit=600.0, revenue=1000.0, shares=10.0)
        p = _pf("2025", net_income=120.0, total_assets=1000.0, operating_cashflow=130.0,
                current_assets=500.0, current_liabilities=300.0, long_term_debt=120.0,
                gross_profit=480.0, revenue=950.0, shares=10.0)
        assert piotroski_f_score(_fin(t, p)).zone == "safe"

    def test_weak_is_risk(self) -> None:
        t = _pf("2026", net_income=-50.0, total_assets=1000.0, operating_cashflow=-60.0,
                current_assets=300.0, current_liabilities=400.0, long_term_debt=300.0,
                gross_profit=200.0, revenue=700.0, shares=12.0)
        p = _pf("2025", net_income=50.0, total_assets=1000.0, operating_cashflow=80.0,
                current_assets=500.0, current_liabilities=300.0, long_term_debt=200.0,
                gross_profit=400.0, revenue=900.0, shares=10.0)
        assert piotroski_f_score(_fin(t, p)).zone == "risk"

    def test_insufficient_data_na(self) -> None:
        t = _pf("2026", net_income=10.0)  # ほぼ欠損
        p = _pf("2025", net_income=5.0)
        assert piotroski_f_score(_fin(t, p)).zone == "na"


class TestAltmanZ:
    def test_healthy_is_safe(self) -> None:
        t = _pf("2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
                ebit=250.0, total_liabilities=500.0, revenue=1000.0)
        assert altman_z_score(_fin(t, None, market_cap=3000.0)).zone == "safe"

    def test_distressed_is_risk(self) -> None:
        t = _pf("2026", working_capital=-100.0, total_assets=1000.0, retained_earnings=50.0,
                ebit=10.0, total_liabilities=900.0, revenue=300.0)
        assert altman_z_score(_fin(t, None, market_cap=100.0)).zone == "risk"

    def test_missing_market_cap_na(self) -> None:
        t = _pf("2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
                ebit=250.0, total_liabilities=500.0, revenue=1000.0)
        assert altman_z_score(_fin(t, None, market_cap=None)).zone == "na"


class TestAssessCredibility:
    def test_clean_flag_ok(self) -> None:
        t = _pf("2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
                ebit=250.0, total_liabilities=500.0, revenue=1000.0, **{
                    k: v for k, v in _CLEAN_T.items()
                    if k not in ("working_capital", "total_assets", "retained_earnings",
                                 "ebit", "total_liabilities", "revenue")
                })
        p = _pf("2025", **_CLEAN_P)
        res = assess_credibility(_fin(t, p, market_cap=3000.0))
        assert res.credibility_flag == "ok"
        assert res.warnings == []

    def test_any_risk_sets_warn(self) -> None:
        # 倒産リスク（Z risk）だけで warn（一致を求めない）
        t = _pf("2026", working_capital=-100.0, total_assets=1000.0, retained_earnings=50.0,
                ebit=10.0, total_liabilities=900.0, revenue=300.0, net_income=-50.0,
                operating_cashflow=-60.0, current_assets=300.0, current_liabilities=400.0,
                long_term_debt=300.0, gross_profit=100.0, shares=10.0, receivables=50.0, ppe=200.0)
        p = _pf("2025", revenue=900.0, total_assets=1000.0, net_income=50.0,
                operating_cashflow=80.0,
                current_assets=500.0, current_liabilities=300.0, long_term_debt=200.0,
                gross_profit=400.0, shares=10.0, receivables=40.0, ppe=210.0)
        res = assess_credibility(_fin(t, p, market_cap=100.0))
        assert res.credibility_flag == "warn"
        assert res.warnings

    def test_financial_sector_excludes_m_and_z(self) -> None:
        fin = _fin(_pf("2026", **_CLEAN_T), _pf("2025", **_CLEAN_P), market_cap=3000.0)
        res = assess_credibility(fin, sector="Financial Services")
        assert res.m_score.zone == "na"
        assert res.z_score.zone == "na"
        assert "業種除外" in res.m_score.note


class TestDisclosureRedFlags:
    def test_detects_going_concern_and_correction(self) -> None:
        disc = [
            {"title": "継続企業の前提に関する重要事象等", "description": ""},
            {"title": "有価証券報告書の訂正報告書の提出", "description": ""},
            {"title": "通期業績予想の修正", "description": ""},  # 通常開示＝フラグでない
        ]
        flags = scan_disclosure_red_flags(disc)
        assert any("継続企業" in f for f in flags)
        assert any("訂正" in f for f in flags)
        assert not any("業績予想" in f for f in flags)

    def test_empty_or_none(self) -> None:
        assert scan_disclosure_red_flags(None) == []
        assert scan_disclosure_red_flags([{"title": "通常のIR"}]) == []

    def test_assess_credibility_warns_on_disclosure(self) -> None:
        # 健全な財務でも、開示にGC注記があれば credibility warn（S4b・D-14）
        t = _pf("2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
                ebit=250.0, total_liabilities=500.0, revenue=1000.0)
        res = assess_credibility(
            _fin(t, None, market_cap=3000.0),
            disclosures=[{"title": "継続企業の前提に関する注記"}],
        )
        assert res.credibility_flag == "warn"
        assert res.disclosure_flags  # GCフラグ
        # MELCHIOR反証にも開示フラグが出る
        counter = melchior_credibility_counter(res)
        assert any("開示レッドフラグ" in c["claim"] for c in counter)


class TestMelchiorCredibilityCounter:
    def test_risk_zones_become_counter_claims(self) -> None:
        # 倒産リスク（Z risk）→ MELCHIOR反証に「倒産リスク域」が出る（S6）
        t = _pf("2026", working_capital=-100.0, total_assets=1000.0, retained_earnings=50.0,
                ebit=10.0, total_liabilities=900.0, revenue=300.0)
        res = assess_credibility(_fin(t, None, market_cap=100.0))
        counter = melchior_credibility_counter(res, source_refs=[{"source": "yfinance"}])
        assert any("倒産リスク" in c["claim"] for c in counter)
        assert all(c["source_refs"] for c in counter)  # 出典付き（摘出＝R5）

    def test_clean_has_no_counter(self) -> None:
        t = _pf("2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
                ebit=250.0, total_liabilities=500.0, revenue=1000.0)
        res = assess_credibility(_fin(t, None, market_cap=3000.0))
        assert melchior_credibility_counter(res) == []
