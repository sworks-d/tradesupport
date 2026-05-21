"""disclosure MCP ツール（SYSTEM_DESIGN.md §3.2）。

適時開示・IR を取得する。当面 JP（TDnet RSS + EDINET API）が対象。米国は将来 SEC 8-K
を追加（Phase 1 は fundamentals と併用で間に合う、と設計書）。

- fetcher 注入可能。既定は TDnet RSS（feedparser）+ EDINET API（httpx, キーがある時のみ）。
- 期間/銘柄フィルタ・URL デデュープ・Graceful Degradation（news ツールと同方針）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field

from trading_agent.mcp_tools.base import (
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
)
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

Disclosure = dict[str, Any]
Fetcher = Callable[["DisclosureInput"], list[Disclosure]]

# TDnet の公開 RSS ミラー（yanoshin）。実運用で要確認・差替可。
DEFAULT_TDNET_RSS = "https://webapi.yanoshin.jp/webapi/tdnet/list/today.rss"


class DisclosureInput(MCPToolInput):
    tickers: list[str] | None = None
    since: datetime | None = None  # 既定は 48時間前
    market: str = "JP"


class DisclosureOutput(MCPToolOutput):
    disclosures: list[Disclosure] = Field(default_factory=list)


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            return None
    return None


def _matches_tickers(item: Disclosure, tickers: list[str] | None) -> bool:
    if not tickers:
        return True
    ticker_field = str(item.get("ticker", ""))
    title = str(item.get("title", ""))
    return any(t == ticker_field or t in title for t in tickers)


class DisclosureTool(MCPTool[DisclosureInput]):
    """適時開示・IR の取得ツール（JP）。"""

    name = "disclosure"
    description = "TDnet / EDINET から適時開示を取得し、期間/銘柄でフィルタする。"
    input_schema = DisclosureInput
    output_schema = DisclosureOutput

    def __init__(self, *, fetchers: list[Fetcher] | None = None) -> None:
        self._fetchers: list[Fetcher] = fetchers if fetchers is not None else _default_fetchers()

    async def _execute(self, tool_input: DisclosureInput) -> MCPToolOutput:
        log = get_logger("mcp_tool").bind(tool=self.name)
        since = tool_input.since or (utcnow() - timedelta(hours=48))

        collected: list[Disclosure] = []
        failures = 0
        for fetcher in self._fetchers:
            try:
                collected.extend(fetcher(tool_input))
            except Exception as exc:
                failures += 1
                log.warning("disclosure_source_failed", error=str(exc))

        if collected == [] and failures > 0:
            raise NetworkError("all disclosure sources failed")

        seen_urls: set[str] = set()
        result: list[Disclosure] = []
        for item in collected:
            if not _matches_tickers(item, tool_input.tickers):
                continue
            published = _parse_dt(item.get("published_at"))
            if published is not None and published < since:
                continue
            url = str(item.get("url", ""))
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            result.append(item)

        result.sort(key=lambda d: d.get("published_at", ""), reverse=True)
        return DisclosureOutput(
            success=True, disclosures=result, metadata={"source_failures": failures}
        )


def _default_fetchers() -> list[Fetcher]:
    return [_fetch_tdnet, _fetch_edinet]


def _fetch_tdnet(tool_input: DisclosureInput) -> list[Disclosure]:
    import feedparser

    parsed = feedparser.parse(DEFAULT_TDNET_RSS)
    disclosures: list[Disclosure] = []
    for entry in parsed.entries:
        disclosures.append(
            {
                "ticker": entry.get("tdnet_companycode", "") or "",
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published_at": entry.get("published", ""),
                "source": "TDnet",
            }
        )
    return disclosures


def _fetch_edinet(tool_input: DisclosureInput) -> list[Disclosure]:
    from trading_agent.config import get_settings

    settings = get_settings()
    if not settings.edinet_api_key:
        return []  # キー未設定なら静かにスキップ

    import httpx

    date = (tool_input.since or utcnow()).date().isoformat()
    headers = {"Ocp-Apim-Subscription-Key": settings.edinet_api_key}
    params = {"date": date, "type": "2"}
    try:
        resp = httpx.get(
            "https://api.edinet-fsa.go.jp/api/v2/documents.json",
            params=params,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise NetworkError(f"EDINET failed: {exc}") from exc

    payload = resp.json()
    disclosures: list[Disclosure] = []
    base = "https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID="
    for doc in payload.get("results", []):
        sec_code = doc.get("secCode") or ""
        disclosures.append(
            {
                "ticker": sec_code[:4] if sec_code else "",
                "title": doc.get("docDescription", ""),
                "url": base + str(doc.get("docID", "")),
                "published_at": doc.get("submitDateTime", ""),
                "source": "EDINET",
            }
        )
    return disclosures
