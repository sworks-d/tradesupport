"""market_data MCP ツール（SYSTEM_DESIGN.md §3.2）。

株価・出来高を取得する。フォールバック順序（SYSTEM_DESIGN §3.2）：

    moomoo（Phase 1.2 で接続） → メモリキャッシュ(5分TTL) → 取得（live） → DB キャッシュ

Phase 1.1 では moomoo 未接続のため **yfinance を live の主ソース**とする
（Task 1.1.2）。Task 1.2.4 で moomoo を live の第一優先に差し替える。

- メモリキャッシュ：ticker ごとに 5分 TTL。`use_cache=False` で無視。
- live 取得失敗（NetworkError）：tenacity でリトライ後、`fallback` が DB キャッシュ
  （`market_data_cache`）から stale 値を返す（§3.4「全失敗 → cache の最新 + warning」）。
- DB にも無い場合：失敗出力（success=False）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session

from trading_agent.mcp_tools.base import (
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
    SourceRef,
)
from trading_agent.models.market_data import MarketDataCache
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

# live ソースが返す quote の正規キー（MarketDataCache の数値列に対応）
QUOTE_FIELDS = (
    "current_price",
    "open_price",
    "high_today",
    "low_today",
    "prev_close",
    "volume_today",
    "price_change_today",
    "price_change_pct_today",
)

# 取得関数の型：ticker のリスト → {ticker: {field: value}}
Fetcher = Callable[[list[str]], dict[str, dict[str, float]]]


def _default_fields() -> list[str]:
    return ["current_price", "volume_today", "prev_close"]


class MarketDataInput(MCPToolInput):
    tickers: list[str]
    fields: list[str] = Field(default_factory=_default_fields)
    use_cache: bool = True  # 5分以内のメモリキャッシュを使うか


class MarketDataOutput(MCPToolOutput):
    data: dict[str, dict[str, float]] = Field(default_factory=dict)  # {ticker: {field: value}}
    as_of: datetime | None = None
    sources: dict[str, str] = Field(default_factory=dict)  # {ticker: source}
    # 2ソース照合の結果（D-12）。ticker -> ok/mismatch/single/cached
    reconciliation: dict[str, str] = Field(default_factory=dict)


@dataclass
class _MemEntry:
    quote: dict[str, float]
    as_of: datetime


def _fetch_from_yfinance(tickers: list[str]) -> dict[str, dict[str, float]]:
    """yfinance から quote を取得する。失敗は NetworkError に正規化する。"""
    import yfinance as yf  # 重い import は遅延

    result: dict[str, dict[str, float]] = {}
    try:
        for ticker in tickers:
            info = yf.Ticker(ticker).fast_info
            current = float(info.last_price)
            prev = float(info.previous_close)
            result[ticker] = {
                "current_price": current,
                "open_price": float(info.open),
                "high_today": float(info.day_high),
                "low_today": float(info.day_low),
                "prev_close": prev,
                "volume_today": float(info.last_volume),
                "price_change_today": current - prev,
                "price_change_pct_today": (current - prev) / prev if prev else 0.0,
            }
    except Exception as exc:  # ネットワーク/データ欠落をまとめて一時障害扱い
        raise NetworkError(f"yfinance fetch failed: {exc}") from exc
    return result


class MarketDataTool(MCPTool[MarketDataInput]):
    """株価・出来高の取得ツール。"""

    name = "market_data"
    description = "ティッカーの現在価格・出来高を取得（5分キャッシュ + DBフォールバック）。"
    input_schema = MarketDataInput
    output_schema = MarketDataOutput

    def __init__(
        self,
        engine: Engine,
        *,
        cache_ttl_seconds: int = 300,
        fetcher: Fetcher | None = None,
        reconcile_fetcher: Fetcher | None = None,
        reconcile_tolerance: float = 0.005,
    ) -> None:
        self._engine = engine
        self._ttl = timedelta(seconds=cache_ttl_seconds)
        self._memory: dict[str, _MemEntry] = {}
        self._fetcher: Fetcher = fetcher or _fetch_from_yfinance
        self._reconcile: Fetcher | None = reconcile_fetcher  # 2ソース目（任意・未設定なら無効）
        self._tol = reconcile_tolerance  # 許容誤差（D-12: ±0.5%）

    async def _execute(self, tool_input: MarketDataInput) -> MCPToolOutput:
        now = utcnow()
        data: dict[str, dict[str, float]] = {}
        sources: dict[str, str] = {}
        ref_asof: dict[str, datetime] = {}  # ticker ごとの実際の時点（防御層用）
        recon: dict[str, str] = {}  # ticker -> ok/mismatch/single/cached（2ソース照合）
        to_fetch: list[str] = []

        for ticker in tool_input.tickers:
            entry = self._memory.get(ticker)
            if tool_input.use_cache and entry is not None and (now - entry.as_of) < self._ttl:
                data[ticker] = _project(entry.quote, tool_input.fields)
                sources[ticker] = "memory"
                ref_asof[ticker] = entry.as_of  # キャッシュ元の取得時点を保持
                recon[ticker] = "cached"
            else:
                to_fetch.append(ticker)

        if to_fetch:
            fetched = self._fetcher(to_fetch)  # NetworkError は base がリトライ/フォールバック
            for ticker, quote in fetched.items():
                self._memory[ticker] = _MemEntry(quote=quote, as_of=now)
                self._write_db_cache(ticker, quote, now)
                data[ticker] = _project(quote, tool_input.fields)
                sources[ticker] = "yfinance"
                ref_asof[ticker] = now
                recon[ticker] = "single"
            self._reconcile_prices(fetched, recon)

        refs = [
            SourceRef(source=sources[t], ref=t, as_of=ref_asof.get(t)) for t in sources
        ]
        mismatches = [t for t, s in recon.items() if s == "mismatch"]
        metadata = {"price_mismatch": mismatches} if mismatches else {}
        return MarketDataOutput(
            success=True,
            data=data,
            as_of=now,
            sources=sources,
            data_asof=now,
            source_refs=refs,
            reconciliation=recon,
            metadata=metadata,
        )

    def _reconcile_prices(
        self, fetched: dict[str, dict[str, float]], recon: dict[str, str]
    ) -> None:
        """2ソース目で current_price を再取得し ±tol 内かを照合する（D-12）。

        不一致は recon[ticker]="mismatch"。2ソース目の失敗は主データを壊さず "single" のまま。
        """
        if self._reconcile is None or not fetched:
            return
        try:
            secondary = self._reconcile(list(fetched))
        except Exception as exc:  # 2ソース目の失敗は主データを壊さない
            get_logger("mcp_tool").bind(tool=self.name).warning(
                "reconcile_failed", reason=str(exc)
            )
            return
        for ticker, quote in fetched.items():
            sq = secondary.get(ticker)
            p1 = quote.get("current_price")
            p2 = sq.get("current_price") if sq else None
            if not p1 or not p2:
                continue
            recon[ticker] = "ok" if abs(p1 - p2) / p1 <= self._tol else "mismatch"

    async def fallback(self, tool_input: MarketDataInput, error: Exception) -> MCPToolOutput | None:
        """live 取得が全滅した時、DB キャッシュ（stale）から返す。"""
        data: dict[str, dict[str, float]] = {}
        sources: dict[str, str] = {}
        refs: list[SourceRef] = []
        oldest: datetime | None = None  # 最も古い時点をデータ代表時点に（staleの明示）
        with Session(self._engine) as session:
            for ticker in tool_input.tickers:
                row = session.get(MarketDataCache, ticker)
                if row is not None:
                    data[ticker] = _project(_row_to_quote(row), tool_input.fields)
                    sources[ticker] = "db_cache"
                    refs.append(SourceRef(source="db_cache", ref=ticker, as_of=row.as_of))
                    if oldest is None or (row.as_of is not None and row.as_of < oldest):
                        oldest = row.as_of
        if not data:
            return None  # キャッシュも無い → base が失敗出力にする
        get_logger("mcp_tool").bind(tool=self.name).warning(
            "market_data_degraded", reason=str(error), served=list(data)
        )
        return MarketDataOutput(
            success=True,
            data=data,
            as_of=utcnow(),
            sources=sources,
            data_asof=oldest,
            source_refs=refs,
            metadata={"degraded": True, "reason": str(error)},
        )

    def _write_db_cache(self, ticker: str, quote: dict[str, float], now: datetime) -> None:
        """market_data_cache を upsert する。"""
        with Session(self._engine) as session:
            row = session.get(MarketDataCache, ticker)
            if row is None:
                row = MarketDataCache(ticker=ticker, market_status="unknown", **quote)
            else:
                for field, value in quote.items():
                    setattr(row, field, value)
            row.as_of = now
            row.source = "yfinance"
            session.add(row)
            session.commit()

    async def health_check(self) -> bool:
        # DB に接続できれば最低限 OK（live ソースの死活は別途 health_checks ジョブで）
        try:
            with Session(self._engine) as session:
                session.get(MarketDataCache, "__healthcheck__")
            return True
        except Exception:
            return False


def _project(quote: dict[str, float], fields: list[str]) -> dict[str, float]:
    """quote から要求された field だけ抜き出す（無い field は省略）。"""
    return {field: quote[field] for field in fields if field in quote}


def _row_to_quote(row: MarketDataCache) -> dict[str, float]:
    return {field: float(getattr(row, field)) for field in QUOTE_FIELDS}
