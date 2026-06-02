"""S4a：2期分の財務（餌）。Beneish M-Score / Piotroski F-Score / Altman Z-Score・
MELCHIOR コード反証の物理的前提（前期比が要る）。

yfinance の財務諸表（income_stmt / balance_sheet / cashflow）から**直近2期**の主要ラインを取得・
正規化する。**APIキー不要**（EDINET/EDGAR の一次情報・監査意見/GC注記は S4b で別途）。
数値はすべてコード取得値（R1）。欠損は None（R4：埋めない）。fetcher 注入でテスト可能。
"""

from __future__ import annotations

import datetime as dt
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
    """直近2期の財務を取得・正規化。データが無ければ None。

    v2.10: **JP 株は J-Quants を優先**し、取れない場合は yfinance に fallback する。
    `fetcher` が明示的に注入された場合（テスト用途）は J-Quants をスキップして
    そのまま fetcher を使う（既存テスト互換）。

    v2.4 TASK-Z6: yfinance ラベル取得失敗の検出を強化。
    """
    from trading_agent.utils.logger import get_logger
    from trading_agent.utils.ticker_normalize import is_jp_ticker

    log = get_logger("screening.financials")

    # v2.10: JP 株かつ fetcher 注入なし → J-Quants を試す
    if fetcher is None and is_jp_ticker(ticker):
        jq_result = _fetch_jquants_financials(ticker, market_cap=market_cap)
        if jq_result is not None and jq_result.current.revenue is not None:
            return jq_result
        log.info("financials_jquants_unavailable_fallback_yfinance", ticker=ticker)

    fetch = fetcher or _fetch_yfinance_statements
    raw = fetch(ticker)
    periods = list(raw.get("periods", []) or [])
    rows = dict(raw.get("rows", {}) or {})  # type: ignore[arg-type]
    if not periods or not rows:
        log.warning("financials_unavailable", ticker=ticker, source="yfinance")
        # 将来: ここで EDINET fallback を試す
        # if _has_edinet_credentials():
        #     return _fetch_edinet_financials(ticker, market_cap)
        return None

    # ラベル取得健全性のチェック（v2.4 TASK-Z6）
    found_labels = sum(1 for labels in _LABELS.values() if any(label in rows for label in labels))
    if found_labels < len(_LABELS) * 0.5:  # 半分未満ならラベル変更の疑い
        log.warning(
            "financials_labels_partial",
            ticker=ticker,
            found=found_labels,
            total=len(_LABELS),
            sample_rows=list(rows.keys())[:5],
        )

    current = _period(rows, periods, 0)
    prior = _period(rows, periods, 1) if len(periods) >= 2 else None
    prior2 = _period(rows, periods, 2) if len(periods) >= 3 else None
    return Financials(
        ticker=ticker, current=current, prior=prior, prior2=prior2,
        market_cap=market_cap, source="yfinance",
    )


def _fetch_jquants_financials(
    ticker: str, *, market_cap: float | None = None
) -> Financials | None:
    """J-Quants の statements API から Financials を構築（JP 株専用・v2.10）。

    取れないフィールド（cogs/gross_profit/sga/depreciation/inventory 等）は **None のまま**。
    後段（M-Score 等）はこれを "warn" / "na" 判定するので、推測で埋めることはしない。

    Returns:
        Financials（current のみで OK、prior が無ければ None）。
        J-Quants 接続不可・データ空・例外 → 全て None を返す（ハルシネーション防止）。
    """
    try:
        from trading_agent.mcp_tools.jquants import get_default_client
        from trading_agent.utils.ticker_normalize import universe_to_jquants
    except Exception:
        return None

    client = get_default_client()
    if client is None:
        return None

    jq_code = universe_to_jquants(ticker)
    statements = client.statements(ticker=jq_code)
    if not statements:
        return None

    # 直近 3 期（新しい順）
    periods: list[PeriodFinancials] = []
    for stmt in statements[:3]:
        pf = _jquants_stmt_to_period(stmt)
        if pf is not None:
            periods.append(pf)
    if not periods:
        return None

    return Financials(
        ticker=ticker,
        current=periods[0],
        prior=periods[1] if len(periods) >= 2 else None,
        prior2=periods[2] if len(periods) >= 3 else None,
        market_cap=market_cap,
        source="jquants",
    )


# BT-0: J-Quants statements の開示日フィールド候補。
# 実 API dry-read（2026-06-02）で実名 = "DiscDate"（Timestamp 型）と確認。先頭に置く。
# 財務値の実列名も確認済: Sales/OP/NP/TA/CFO/EPS（_jquants_stmt_to_period と一致）。
_DISCLOSED_DATE_KEYS = ("DiscDate", "DisclosedDate", "DisclosureDate")


def _parse_disclosed_date(stmt: dict) -> dt.date | None:
    """statements の 1 期から開示日を取り出す。取れなければ None（推測しない）。"""
    for key in _DISCLOSED_DATE_KEYS:
        raw = stmt.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, dt.datetime):
            return raw.date()
        if isinstance(raw, dt.date):
            return raw
        s = str(raw).strip()
        try:
            return dt.date.fromisoformat(s.replace("/", "-")[:10])
        except ValueError:
            pass
        digits = "".join(ch for ch in s if ch.isdigit())[:8]
        if len(digits) == 8:
            try:
                return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
            except ValueError:
                pass
    return None


def fetch_financials_asof(
    ticker: str,
    statements: list[dict],
    as_of: dt.date,
    *,
    market_cap: float | None = None,
) -> Financials | None:
    """BT-0: as_of 時点で公表済みの財務だけで Financials を構成（PIT・look-ahead 回避・純粋関数）。

    DisclosedDate ≤ as_of の開示だけを使い、開示日降順で current/prior/prior2 を作る。
    **開示日が取れない statement は除外**（保守的＝未来財務の混入を防ぐ。silent look-ahead を出さない）。
    API 非依存（statements を注入）なので fixtures でテスト可能。

    ⚠ J-Quants SDK の開示日フィールド実名は実 API で要確認（_DISCLOSED_DATE_KEYS の候補で対応中）。
       実名が候補外だと全 statement が除外され None を返す（＝backtest が空＝バグに気づける／嘘は出さない）。
    """
    eligible: list[tuple[dt.date, dict]] = []
    for stmt in statements:
        d = _parse_disclosed_date(stmt)
        if d is None or d > as_of:
            continue
        eligible.append((d, stmt))
    if not eligible:
        return None
    eligible.sort(key=lambda x: x[0], reverse=True)
    periods: list[PeriodFinancials] = []
    for _, stmt in eligible[:3]:
        pf = _jquants_stmt_to_period(stmt)
        if pf is not None:
            periods.append(pf)
    if not periods:
        return None
    return Financials(
        ticker=ticker,
        current=periods[0],
        prior=periods[1] if len(periods) >= 2 else None,
        prior2=periods[2] if len(periods) >= 3 else None,
        market_cap=market_cap,
        source="jquants_asof",
    )


def _jquants_stmt_to_period(stmt: dict) -> PeriodFinancials | None:
    """J-Quants statements の 1 期分 → PeriodFinancials。

    マッピング（J-Quants → PeriodFinancials）:
      Sales → revenue        / OP → ebit        / NP → net_income
      TA → total_assets      / CFO → operating_cashflow
      EPS と NP から shares を逆算（両方ある時のみ・推測しない）

    取れない/空のフィールドは None のまま（ハルシネーション禁止）。
    """
    def _safe_float(key: str) -> float | None:
        v = stmt.get(key)
        if v is None or v == "":
            return None
        try:
            f = float(v)
            if f != f:  # NaN
                return None
            return f
        except (TypeError, ValueError):
            return None

    # period 文字列: CurPerEn（当期末） を優先、無ければ DiscDate
    period_raw = stmt.get("CurPerEn") or stmt.get("DiscDate") or ""
    if hasattr(period_raw, "isoformat"):
        period_str = period_raw.isoformat()[:10]
    else:
        period_str = str(period_raw)[:10]

    net_income = _safe_float("NP")
    eps = _safe_float("EPS")
    # shares: NP / EPS で逆算（両方あって EPS ≠ 0 の時のみ・推測しない）
    shares: float | None = None
    if net_income is not None and eps is not None and eps != 0:
        shares = net_income / eps

    return PeriodFinancials(
        period=period_str,
        revenue=_safe_float("Sales"),
        net_income=net_income,
        ebit=_safe_float("OP"),
        total_assets=_safe_float("TA"),
        operating_cashflow=_safe_float("CFO"),
        shares=shares,
        # cogs / gross_profit / sga / depreciation / current_assets / current_liabilities /
        # ppe / receivables / inventory / total_liabilities / long_term_debt /
        # retained_earnings / working_capital は J-Quants から取れない → None のまま
    )


def _fetch_yfinance_statements(ticker: str) -> RawStatements:
    """yfinance の3表を {periods, rows} にマージ（直近3期）。

    v2.5 TASK-Z12: 3 期固定を環境変数で上書き可能に（年次→四半期切替の柔軟性）。
    """
    import os
    max_periods = int(os.environ.get("FINANCIALS_MAX_PERIODS", "3"))

    import yfinance as yf

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

    # v2.10: 新型 ticker (141A 等) も .T 付与する to_yfinance_symbol 経由
    sym = to_yfinance_symbol(ticker)
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

    # 期（列）：最初のフレームの列を基準に直近 max_periods 期
    cols = list(frames[0].columns)[:max_periods]
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
