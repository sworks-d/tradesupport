"""D：screening 候補エンリッチ（信用性/V字/相対力）の単体テスト。フェッチャ注入。"""

from __future__ import annotations

from trading_agent.agents.screening_agent import enrich_candidates
from trading_agent.screening.financials import Financials, PeriodFinancials


def _result(ticker: str, sector: str, composite: float, market: str = "US") -> dict:
    return {
        "ticker": ticker, "name": ticker, "market": market, "sector": sector,
        "composite_score": composite, "market_cap": 1.0e9,
    }


def _healthy_fin(ticker: str) -> Financials:
    cur = PeriodFinancials(
        period="2026", working_capital=500.0, total_assets=1000.0, retained_earnings=600.0,
        ebit=300.0, total_liabilities=400.0, revenue=1000.0, net_income=200.0,
        operating_cashflow=205.0, current_assets=500.0, current_liabilities=300.0,
        receivables=120.0, ppe=300.0, gross_profit=600.0, shares=10.0, long_term_debt=100.0,
    )
    return Financials(ticker=ticker, current=cur, prior=None, market_cap=3000.0)


def _distressed_fin(ticker: str) -> Financials:
    cur = PeriodFinancials(
        period="2026", working_capital=-100.0, total_assets=1000.0, retained_earnings=50.0,
        ebit=10.0, total_liabilities=900.0, revenue=300.0,
    )
    return Financials(ticker=ticker, current=cur, prior=None, market_cap=100.0)


def _flat_prices(_sym: str) -> list[float]:
    return [100.0 + 0.5 * i for i in range(80)]


def test_attaches_quality_signals() -> None:
    results = [_result("AAA", "Technology", 70.0)]
    out = enrich_candidates(
        results, financials_fetcher=_healthy_fin, price_history=_flat_prices
    )
    r = out[0]
    assert r["credibility_flag"] == "ok"
    assert "turnaround_zone" in r
    assert "rs_quadrant" in r


def test_credibility_warn_demotes_and_reranks() -> None:
    # 高スコアだが倒産リスク（Z risk）→ 減点され、健全な低スコアより下がる
    results = [_result("RISKY", "Technology", 80.0), _result("SAFE", "Industrials", 70.0)]

    def fin_fetcher(ticker: str) -> Financials:
        return _distressed_fin(ticker) if ticker == "RISKY" else _healthy_fin(ticker)

    out = enrich_candidates(results, financials_fetcher=fin_fetcher, price_history=_flat_prices)
    risky = next(r for r in out if r["ticker"] == "RISKY")
    assert risky["credibility_flag"] == "warn"
    assert risky["composite_score"] == 80.0 * 0.7  # 減点
    assert risky.get("quality_penalty")
    assert out[0]["ticker"] == "SAFE"  # 再ランクで健全が上に


def test_graceful_when_no_financials() -> None:
    results = [_result("X", "Technology", 60.0)]
    out = enrich_candidates(
        results, financials_fetcher=lambda _t: None, price_history=_flat_prices
    )
    assert out[0]["composite_score"] == 60.0  # 減点なし
    assert out[0]["rs_quadrant"] in ("leading", "weakening", "lagging", "improving", "na")


def test_fetch_failure_is_isolated() -> None:
    results = [_result("X", "Technology", 60.0)]

    def boom(_t: str) -> Financials:
        raise RuntimeError("net down")

    out = enrich_candidates(results, financials_fetcher=boom, price_history=_flat_prices)
    assert out[0]["composite_score"] == 60.0  # 失敗は無視（減点なし）
