"""fundamentals MCP ツール（SYSTEM_DESIGN.md §3.2）。

財務サマリ指標（PER/PBR/EPS/増収率/営業利益率 等）を共通フォーマットで返し、
一次情報（EDGAR/EDINET）への ``source_url`` を付与する（透明性）。

実装方針（Phase 1.1.3）:
- 数値は **yfinance** を主ソースとする（US は素のティッカー、JP は ``.T`` サフィックス）。
  設計書は EDGAR/EDINET を主としているが、それらの XBRL 深掘り解析は本来 market-analyst
  の深掘り（F6 / Phase 1.4）の責務であり、軽量な fundamentals ツールでは yfinance の
  サマリ指標で十分かつ検証容易。EDGAR/EDINET は ``source_url`` として透明性に用いる。
- ``fetcher`` を注入可能にし、将来 EDGAR/EDINET ベースの provider へツールを壊さず差し替え可能。
- 財務は変化が遅いため、メモリキャッシュ（既定 6h TTL）で API 呼び出しを節約する。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import Field

from trading_agent.mcp_tools.base import (
    DataNotFoundError,
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
    SourceRef,
)
from trading_agent.utils.time_utils import utcnow

# yfinance の info キー → 共通フィールド名
# MELCHIOR（業績審判）が成長・収益性・健全性・キャッシュフローを多面評価できるよう拡張（P1-5）。
_YF_FIELD_MAP: dict[str, str] = {
    # バリュエーション
    "eps": "trailingEps",
    "per": "trailingPE",
    "forward_per": "forwardPE",
    "pbr": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months",
    "peg": "trailingPegRatio",
    # 成長
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    # 収益性（マージン・資本効率）
    "operating_margin": "operatingMargins",
    "profit_margin": "profitMargins",
    "gross_margin": "grossMargins",
    "roe": "returnOnEquity",
    "roa": "returnOnAssets",
    # 規模・配当
    "revenue": "totalRevenue",
    "net_income": "netIncomeToCommon",
    "dividend_yield": "dividendYield",
    # 財務健全性（レバレッジ・流動性・CF）
    "debt_to_equity": "debtToEquity",
    "current_ratio": "currentRatio",
    "quick_ratio": "quickRatio",
    "free_cashflow": "freeCashflow",
    "total_debt": "totalDebt",
    "total_cash": "totalCash",
    "beta": "beta",
}

# 取得関数の型：ticker → (正規化済み数値, fiscal_period)
Fetcher = Callable[[str], tuple[dict[str, float], str]]

# screening の S7 V字判定用：四半期 EPS 派生フィールド（fundamentals に同居させる）
_QUARTERLY_EPS_FIELDS = (
    "eps_latest_q",
    "eps_prev_prev_q",
    "eps_growth_latest_q",
    "eps_growth_prev_prev_q",
)


def _default_fields() -> list[str]:
    """MELCHIOR が見る既定指標。成長・収益性・健全性・CF を網羅（取得不能な項目は欠損＝na）。"""
    return [
        "eps",
        "per",
        "pbr",
        "revenue_growth",
        "earnings_growth",
        "operating_margin",
        "profit_margin",
        "gross_margin",
        "roe",
        "roa",
        "debt_to_equity",
        "current_ratio",
        "free_cashflow",
        "dividend_yield",
        # screening S7（V字）用：四半期 EPS。欠損可（取得不能なら採点側でスキップ）
        *_QUARTERLY_EPS_FIELDS,
    ]


def eps_metrics_from_series(series: list[float]) -> dict[str, float]:
    """新しい順の EPS 列から screening 派生4指標を計算する。

    - `eps_latest_q`     = 直近の EPS（赤字→黒字 判定用）
    - `eps_prev_prev_q`  = 2四半期前の EPS（同上）
    - `eps_growth_latest_q`    = (直近 - 4q前) / |4q前|（YoY 成長率）
    - `eps_growth_prev_prev_q` = (2q前 - 6q前) / |6q前|（YoY 成長率）

    取得できない指標は dict から除外する（欠損として扱う）。
    """
    out: dict[str, float] = {}
    if len(series) >= 1:
        out["eps_latest_q"] = series[0]
    if len(series) >= 3:
        out["eps_prev_prev_q"] = series[2]
    if len(series) >= 5 and series[4] != 0:
        out["eps_growth_latest_q"] = (series[0] - series[4]) / abs(series[4])
    if len(series) >= 7 and series[6] != 0:
        out["eps_growth_prev_prev_q"] = (series[2] - series[6]) / abs(series[6])
    return out


class FundamentalsInput(MCPToolInput):
    ticker: str
    fields: list[str] = Field(default_factory=_default_fields)
    period: str = "latest"  # "latest" / "ttm" / "annual"


class FundamentalsOutput(MCPToolOutput):
    data: dict[str, float] = Field(default_factory=dict)
    fiscal_period: str = "latest"
    source_url: str | None = None  # 一次情報（EDGAR / EDINET）へのリンク


@dataclass
class _MemEntry:
    values: dict[str, float]
    fiscal_period: str
    as_of: datetime = field(default_factory=utcnow)


def is_jp_ticker(ticker: str) -> bool:
    """日本株ティッカーか判定する（``7203`` / ``7203.T`` のような数字コード）。"""
    code = ticker.split(".")[0]
    return ticker.upper().endswith(".T") or code.isdigit()


def to_yfinance_symbol(ticker: str) -> str:
    """yfinance に渡せる形式に正規化する（JP は ``.T`` を付与、US はそのまま）。

    - ``"7203"``    → ``"7203.T"``
    - ``"7203.T"``  → ``"7203.T"``（冪等）
    - ``"NVDA"``    → ``"NVDA"``
    """
    if ticker.upper().endswith(".T"):
        return ticker
    if ticker.split(".")[0].isdigit():
        return f"{ticker}.T"
    return ticker


def primary_source_url(ticker: str) -> str:
    """一次情報（EDGAR / EDINET）への参照 URL を返す（透明性）。"""
    if is_jp_ticker(ticker):
        # EDINET の書類検索ポータル（証券コードは metadata 側で補足）
        return "https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx"
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&ticker={ticker}&type=10-K&dateb=&owner=include&count=40"
    )


def _fetch_quarterly_eps_series(symbol: str) -> list[float]:
    """yfinance.quarterly_income_stmt から EPS 系列を新しい順で取り出す。

    取得できなければ空リスト。NaN は除外する。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(symbol).quarterly_income_stmt
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    # 行ラベルは "Diluted EPS" / "Basic EPS" のいずれか
    eps_row = None
    for key in ("Diluted EPS", "Basic EPS"):
        if key in getattr(df, "index", []):
            eps_row = df.loc[key]
            break
    if eps_row is None:
        return []
    series: list[float] = []
    for v in eps_row.values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN チェック
            continue
        series.append(f)
    return series


def _fetch_from_yfinance(ticker: str) -> tuple[dict[str, float], str]:
    """yfinance から財務サマリ指標を取得し、共通フォーマットに正規化する。"""
    import yfinance as yf

    symbol = to_yfinance_symbol(ticker)
    try:
        info = yf.Ticker(symbol).info
    except Exception as exc:
        raise NetworkError(f"yfinance fundamentals fetch failed: {exc}") from exc

    if not info:
        raise DataNotFoundError(f"no fundamentals for {ticker}")

    values: dict[str, float] = {}
    for common_field, yf_key in _YF_FIELD_MAP.items():
        raw = info.get(yf_key)
        if raw is not None:
            try:
                values[common_field] = float(raw)
            except (TypeError, ValueError):
                continue

    # 四半期 EPS の派生指標を追加（S7 V字 採点用）。取れない銘柄は静かに欠損で進む。
    eps_series = _fetch_quarterly_eps_series(symbol)
    values.update(eps_metrics_from_series(eps_series))

    fiscal_period = "latest"
    epoch = info.get("lastFiscalYearEnd")
    if isinstance(epoch, int | float):
        fiscal_period = datetime.fromtimestamp(epoch, tz=None).date().isoformat()

    return values, fiscal_period


class FundamentalsTool(MCPTool[FundamentalsInput]):
    """財務サマリ指標の取得ツール。"""

    name = "fundamentals"
    description = "PER/PBR/EPS 等の財務指標を取得し、一次情報 URL を付与する。"
    input_schema = FundamentalsInput
    output_schema = FundamentalsOutput

    def __init__(
        self,
        *,
        cache_ttl_seconds: int = 6 * 60 * 60,
        fetcher: Fetcher | None = None,
    ) -> None:
        self._ttl = timedelta(seconds=cache_ttl_seconds)
        self._memory: dict[str, _MemEntry] = {}
        self._fetcher: Fetcher = fetcher or _fetch_from_yfinance

    async def _execute(self, tool_input: FundamentalsInput) -> MCPToolOutput:
        ticker = tool_input.ticker
        now = utcnow()
        entry = self._memory.get(ticker)
        if entry is not None and (now - entry.as_of) < self._ttl:
            values, fiscal_period = entry.values, entry.fiscal_period
            source = "memory"
            data_asof = entry.as_of
        else:
            values, fiscal_period = self._fetcher(ticker)  # NetworkError は base が処理
            self._memory[ticker] = _MemEntry(values=values, fiscal_period=fiscal_period, as_of=now)
            source = "yfinance"
            data_asof = now

        projected = {f: values[f] for f in tool_input.fields if f in values}
        # 報告期(fiscal_period)が ISO 日付なら時点として保持（temporal hallucination 対策）
        try:
            fiscal_asof: datetime | None = datetime.fromisoformat(fiscal_period)
        except ValueError:
            fiscal_asof = None
        ref = SourceRef(
            source=source,
            ref=primary_source_url(ticker),
            as_of=fiscal_asof,
            note=f"fiscal_period={fiscal_period}",
        )
        return FundamentalsOutput(
            success=True,
            data=projected,
            fiscal_period=fiscal_period,
            source_url=primary_source_url(ticker),
            data_asof=data_asof,
            source_refs=[ref],
            metadata={"source": source, "market": "JP" if is_jp_ticker(ticker) else "US"},
        )
