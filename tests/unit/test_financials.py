"""S4a：2期分財務（screening/financials）の単体テスト。fetcher注入でネット非依存。"""

from __future__ import annotations

from trading_agent.screening.financials import fetch_financials


def _raw() -> dict:
    return {
        "periods": ["2026-01-31", "2025-01-31"],
        "rows": {
            "Total Revenue": [1000.0, 800.0],
            "Cost Of Revenue": [400.0, 360.0],
            "Gross Profit": [600.0, 440.0],
            "Net Income": [200.0, 120.0],
            "Selling General And Administration": [100.0, 90.0],
            "Reconciled Depreciation": [50.0, 45.0],
            "EBIT": [250.0, 150.0],
            "Total Assets": [2000.0, 1800.0],
            "Current Assets": [900.0, 800.0],
            "Current Liabilities": [400.0, 420.0],
            "Net PPE": [700.0, 650.0],
            "Accounts Receivable": [150.0, 100.0],
            "Total Liabilities Net Minority Interest": [800.0, 820.0],
            "Long Term Debt": [300.0, 350.0],
            "Retained Earnings": [1000.0, 820.0],
            "Ordinary Shares Number": [10.0, 10.0],
            "Operating Cash Flow": [220.0, 130.0],
        },
    }


def test_maps_current_and_prior() -> None:
    fin = fetch_financials("NVDA", fetcher=lambda _t: _raw(), market_cap=5000.0)
    assert fin is not None and fin.has_two_periods()
    assert fin.current.revenue == 1000.0
    assert fin.prior.revenue == 800.0
    assert fin.current.receivables == 150.0
    assert fin.current.operating_cashflow == 220.0
    assert fin.market_cap == 5000.0


def test_working_capital_computed_when_missing() -> None:
    # Working Capital 行が無くても 流動資産−流動負債 で補完
    fin = fetch_financials("NVDA", fetcher=lambda _t: _raw())
    assert fin.current.working_capital == 900.0 - 400.0


def test_label_fallback() -> None:
    raw = {"periods": ["2026"], "rows": {"Operating Revenue": [500.0]}}  # Total Revenue 無し
    fin = fetch_financials("X", fetcher=lambda _t: raw)
    assert fin.current.revenue == 500.0  # フォールバック採用


def test_missing_is_none_not_fabricated() -> None:
    raw = {"periods": ["2026"], "rows": {"Total Revenue": [100.0]}}
    fin = fetch_financials("X", fetcher=lambda _t: raw)
    assert fin.current.revenue == 100.0
    assert fin.current.net_income is None  # 欠損は None（捏造しない）
    assert fin.prior is None  # 1期のみ


def test_nan_treated_as_missing() -> None:
    raw = {"periods": ["2026", "2025"], "rows": {"Total Revenue": [float("nan"), 800.0]}}
    fin = fetch_financials("X", fetcher=lambda _t: raw)
    assert fin.current.revenue is None
    assert fin.prior.revenue == 800.0


def test_empty_returns_none() -> None:
    assert fetch_financials("X", fetcher=lambda _t: {"periods": [], "rows": {}}) is None
