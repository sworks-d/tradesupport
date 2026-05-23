"""S4a：2期分の財務（餌）。Beneish M-Score / Piotroski F-Score / Altman Z-Score・
MELCHIOR コード反証の物理的前提（前期比が要る）。

yfinance の財務諸表（income_stmt / balance_sheet / cashflow）から**直近2期**の主要ラインを取得・
正規化する。**APIキー不要**（EDINET/EDGAR の一次情報・監査意見/GC注記は S4b で別途）。
数値はすべてコード取得値（R1）。欠損は None（R4：埋めない）。fetcher 注入でテスト可能。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# 生の財務諸表：{"periods": [ISO日付, ...降順], "rows": {ラベル: [値, ...periods整列]}}
RawStatements = dict[str, object]
StatementFetcher = Callable[[str], RawStatements]

# 共通フィールド → yfinance ラベル候補（先頭優先・フォールバック付き）
_LABELS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "cogs": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
    "gross_profit": ("Gross Profit",),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "sga": ("Selling General And Administration",),
    "depreciation": ("Reconciled Depreciation", "Depreciation And Amortization"),
    "ebit": ("EBIT", "Operating Income"),
    "total_assets": ("Total Assets",),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
    "ppe": ("Net PPE", "Net Property Plant And Equipment"),
    "receivables": ("Accounts Receivable", "Receivables"),
    "inventory": ("Inventory",),
    "total_liabilities": ("Total Liabilities Net Minority Interest", "Total Liabilities"),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "retained_earnings": ("Retained Earnings",),
    "shares": ("Ordinary Shares Number", "Share Issued"),
    "working_capital": ("Working Capital",),
    "operating_cashflow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
}


@dataclass
class PeriodFinancials:
    period: str  # 報告期（ISO日付）
    revenue: float | None = None
    cogs: float | None = None
    gross_profit: float | None = None
    net_income: float | None = None
    sga: float | None = None
    depreciation: float | None = None
    ebit: float | None = None
    total_assets: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    ppe: float | None = None
    receivables: float | None = None
    inventory: float | None = None
    total_liabilities: float | None = None
    long_term_debt: float | None = None
    retained_earnings: float | None = None
    shares: float | None = None
    working_capital: float | None = None
    operating_cashflow: float | None = None


@dataclass
class Financials:
    ticker: str
    current: PeriodFinancials
    prior: PeriodFinancials | None
    prior2: PeriodFinancials | None = None  # 3期目（earnings acceleration＝成長率の加速に必要）
    market_cap: float | None = None
    source: str = "yfinance"

    def has_two_periods(self) -> bool:
        return self.prior is not None

    def has_three_periods(self) -> bool:
        return self.prior is not None and self.prior2 is not None


def _num(rows: dict[str, list], labels: tuple[str, ...], idx: int) -> float | None:
    """ラベル候補を順に試し、period idx の数値を返す（無/NaN は None）。"""
    for label in labels:
        series = rows.get(label)
        if series is None or idx >= len(series):
            continue
        val = series[idx]
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        return f
    return None


def _period(rows: dict[str, list], periods: list[str], idx: int) -> PeriodFinancials:
    pf = PeriodFinancials(period=periods[idx] if idx < len(periods) else "")
    for field, labels in _LABELS.items():
        setattr(pf, field, _num(rows, labels, idx))
    # working_capital 欠損なら 流動資産−流動負債 で補完（コード計算）
    if pf.working_capital is None and pf.current_assets is not None:
        if pf.current_liabilities is not None:
            pf.working_capital = pf.current_assets - pf.current_liabilities
    return pf


def fetch_financials(
    ticker: str,
    *,
    fetcher: StatementFetcher | None = None,
    market_cap: float | None = None,
) -> Financials | None:
    """直近2期の財務を取得・正規化。データが無ければ None。"""
    fetch = fetcher or _fetch_yfinance_statements
    raw = fetch(ticker)
    periods = list(raw.get("periods", []) or [])
    rows = dict(raw.get("rows", {}) or {})  # type: ignore[arg-type]
    if not periods or not rows:
        return None
    current = _period(rows, periods, 0)
    prior = _period(rows, periods, 1) if len(periods) >= 2 else None
    prior2 = _period(rows, periods, 2) if len(periods) >= 3 else None
    return Financials(
        ticker=ticker, current=current, prior=prior, prior2=prior2,
        market_cap=market_cap, source="yfinance",
    )


def _fetch_yfinance_statements(ticker: str) -> RawStatements:
    """yfinance の3表を {periods, rows} にマージ（直近3期）。"""
    import yfinance as yf

    sym = f"{ticker}.T" if ticker.split(".")[0].isdigit() else ticker
    t = yf.Ticker(sym)

    frames = []
    for attr in ("income_stmt", "balance_sheet", "cashflow"):
        try:
            df = getattr(t, attr)
        except Exception:
            df = None
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return {"periods": [], "rows": {}}

    # 期（列）：最初のフレームの列を基準に直近3期
    cols = list(frames[0].columns)[:3]
    periods = [_period_str(c) for c in cols]
    rows: dict[str, list] = {}
    for df in frames:
        for label in df.index:
            raw = [df.at[label, c] if c in df.columns else None for c in cols]
            rows[str(label)] = [_to_float(v) for v in raw]
    return {"periods": periods, "rows": rows}


def _period_str(col: object) -> str:
    date_fn = getattr(col, "date", None)
    return str(date_fn()) if callable(date_fn) else str(col)


def _to_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None
