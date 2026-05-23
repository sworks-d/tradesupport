"""news MCP ツール（SYSTEM_DESIGN.md §3.2）。

NewsAPI と RSS フィード（日経・Bloomberg・Reuters・TechCrunch・9to5Mac・政府公式 等）から
ニュースを収集し、重複除去・言語判別・期間/銘柄/トピックフィルタを行う。

- 重複除去：URL 完全一致 + 見出しハッシュ + 類似度（difflib）
- 言語判別：日本語文字の有無で ``ja`` / ``en``
- Graceful Degradation：一部のソースが落ちても取得できたものを返す。全滅時のみ
  NetworkError（base がリトライ）。

fetcher（ソース）は注入可能。既定は RSS（feedparser）+ NewsAPI（httpx, キーがある時のみ）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from pydantic import Field

from trading_agent.mcp_tools.base import (
    MCPTool,
    MCPToolInput,
    MCPToolOutput,
    NetworkError,
    SourceRef,
)
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

# 1記事を表す正規化済み dict
Article = dict[str, Any]

# 既定 RSS フィード（SYSTEM_DESIGN §3.2 / STEP_A データソース）
DEFAULT_RSS_FEEDS: tuple[str, ...] = (
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://techcrunch.com/feed/",
    "https://9to5mac.com/feed/",
    "https://www.nikkei.com/rss/",
)

_FUZZY_DUP_THRESHOLD = 0.90

# 取得関数の型：NewsInput → 正規化済み記事のリスト
Fetcher = Callable[["NewsInput"], list[Article]]


class NewsInput(MCPToolInput):
    tickers: list[str] | None = None
    topics: list[str] | None = None
    since: datetime | None = None  # 既定は 24時間前
    sources: list[str] | None = None  # 特定ソースに絞る
    companies: dict[str, str] | None = None  # ticker→社名（GoogleNews検索語の補完用・任意）


class NewsOutput(MCPToolOutput):
    articles: list[Article] = Field(default_factory=list)
    total_before_dedupe: int = 0


def detect_language(text: str) -> str:
    """日本語文字（ひらがな/カタカナ/漢字）を含めば ja、なければ en。"""
    for ch in text:
        if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿":
            return "ja"
    return "en"


def _normalize_title(title: str) -> str:
    return "".join(title.lower().split())


def dedupe_articles(articles: Iterable[Article]) -> list[Article]:
    """URL 完全一致・見出しハッシュ・類似度で重複を除去する。"""
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    kept: list[Article] = []
    kept_titles: list[str] = []
    for article in articles:
        url = article.get("url", "")
        norm = _normalize_title(article.get("title", ""))
        digest = hashlib.md5(norm.encode("utf-8"), usedforsecurity=False).hexdigest()
        if url and url in seen_urls:
            continue
        if digest in seen_hashes:
            continue
        if any(
            SequenceMatcher(None, norm, kt).ratio() > _FUZZY_DUP_THRESHOLD for kt in kept_titles
        ):
            continue
        kept.append(article)
        kept_titles.append(norm)
        seen_hashes.add(digest)
        if url:
            seen_urls.add(url)
    return kept


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


def _matches_filters(article: Article, tickers: list[str] | None, topics: list[str] | None) -> bool:
    if not tickers and not topics:
        return True
    haystack = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    needles = [n.lower() for n in (*(tickers or []), *(topics or []))]
    return any(n in haystack for n in needles)


def _within_since(article: Article, since: datetime) -> bool:
    published = _parse_dt(article.get("published_at"))
    return published is None or published >= since


class NewsTool(MCPTool[NewsInput]):
    """ニュース収集ツール。"""

    name = "news"
    description = "NewsAPI / RSS からニュースを収集し、重複除去・期間/銘柄フィルタして返す。"
    input_schema = NewsInput
    output_schema = NewsOutput

    def __init__(self, *, fetchers: list[Fetcher] | None = None) -> None:
        self._fetchers: list[Fetcher] = fetchers if fetchers is not None else _default_fetchers()

    async def _execute(self, tool_input: NewsInput) -> MCPToolOutput:
        log = get_logger("mcp_tool").bind(tool=self.name)
        since = tool_input.since or (utcnow() - timedelta(hours=24))

        collected: list[Article] = []
        failures = 0
        for fetcher in self._fetchers:
            try:
                collected.extend(fetcher(tool_input))
            except Exception as exc:  # 1ソースの失敗で全体を止めない
                failures += 1
                log.warning("news_source_failed", error=str(exc))

        if collected == [] and failures > 0:
            raise NetworkError("all news sources failed")  # 全滅 → base がリトライ

        filtered = [
            a
            for a in collected
            if _matches_filters(a, tool_input.tickers, tool_input.topics)
            and _within_since(a, since)
        ]
        total_before = len(filtered)
        deduped = dedupe_articles(filtered)
        for article in deduped:
            article.setdefault("language", detect_language(article.get("title", "")))
        deduped.sort(key=lambda a: a.get("published_at", ""), reverse=True)

        # 記事ごとに出典（URL・公開時点）を保持＝CASPER の主張の出典実在照合の土台
        refs = [
            SourceRef(
                source=str(a.get("source") or "news"),
                ref=a.get("url") or None,
                as_of=_parse_dt(a.get("published_at")),
            )
            for a in deduped
            if a.get("url")
        ]
        asof = [r.as_of for r in refs if r.as_of is not None]
        return NewsOutput(
            success=True,
            articles=deduped,
            total_before_dedupe=total_before,
            data_asof=max(asof) if asof else None,
            source_refs=refs,
            metadata={"source_failures": failures},
        )


def _default_fetchers() -> list[Fetcher]:
    """既定の取得ソース。無料・キー不要を先頭に置く（P1-6）。

    yfinance（銘柄別）→ Google News RSS（銘柄別・日本語可）→ 汎用RSS → NewsAPI（キー時のみ）。
    """
    return [_fetch_yf_news, _fetch_gnews_rss, _fetch_rss, _fetch_newsapi]


# --- yfinance（銘柄別・無料） ----------------------------------------------


def _yf_symbol(ticker: str) -> str:
    """yfinance 用シンボル。日本株（数字のみの証券コード）は ``.T`` を付す。"""
    t = ticker.strip()
    return f"{t}.T" if t.isdigit() else t


def _epoch_to_iso(epoch: object) -> str:
    """epoch 秒 → naive ISO 文字列。失敗時は空文字。"""
    try:
        dt = datetime.fromtimestamp(float(epoch), tz=UTC)
    except (TypeError, ValueError, OSError):
        return ""
    return dt.replace(tzinfo=None).isoformat()


def _normalize_yf_item(item: dict[str, Any]) -> Article | None:
    """yfinance の1記事を Article に正規化。旧形（フラット）/新形（content入れ子）両対応。

    旧：``{title, publisher, link, providerPublishTime(epoch), ...}``
    新：``{content: {title, summary, pubDate(ISO), provider.displayName, canonicalUrl.url}}``
    数値・本文は生成せずソース提供値のまま（R5）。タイトルもURLも無ければ None。
    """
    content = item.get("content")
    if isinstance(content, dict):  # 新形
        title = content.get("title", "") or ""
        summary = content.get("summary", "") or content.get("description", "") or ""
        provider = content.get("provider") or {}
        source = (provider.get("displayName") if isinstance(provider, dict) else None) or "yfinance"
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            cand = content.get(key)
            if isinstance(cand, dict) and cand.get("url"):
                url = cand["url"]
                break
        published = content.get("pubDate") or content.get("displayTime") or ""
    else:  # 旧形
        title = item.get("title", "") or ""
        summary = item.get("summary", "") or ""
        source = item.get("publisher") or "yfinance"
        url = item.get("link", "") or ""
        published = _epoch_to_iso(item.get("providerPublishTime"))

    if not title and not url:
        return None
    return {
        "title": title,
        "summary": summary,
        "source": source,
        "url": url,
        "published_at": published,
    }


def _fetch_yf_news(tool_input: NewsInput) -> list[Article]:
    if not tool_input.tickers:
        return []

    import yfinance as yf

    articles: list[Article] = []
    for ticker in tool_input.tickers:
        raw = yf.Ticker(_yf_symbol(ticker)).news or []
        for item in raw:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_yf_item(item)
            if normalized is not None:
                articles.append(normalized)
    return articles


# --- Google News RSS（銘柄別・無料・日本語可） --------------------------------

_GNEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


def _gnews_query(ticker: str, company: str | None) -> str:
    """検索語。社名が分かれば併記して関連度を上げる（無くても ticker で動く）。"""
    head = f"{ticker} {company}".strip() if company else ticker
    return f"{head} 株 OR stock"


def _normalize_gnews_entry(entry: Any) -> Article:
    """feedparser の1エントリを Article に正規化。"""
    source_d = entry.get("source") if hasattr(entry, "get") else None
    source = ""
    if hasattr(source_d, "get"):
        source = source_d.get("title", "") or ""
    return {
        "title": entry.get("title", "") or "",
        "summary": entry.get("summary", "") or "",
        "source": source or "GoogleNews",
        "url": entry.get("link", "") or "",
        "published_at": entry.get("published", "") or "",
    }


def _fetch_gnews_rss(tool_input: NewsInput) -> list[Article]:
    if not tool_input.tickers:
        return []

    import urllib.parse

    import feedparser

    companies = tool_input.companies or {}
    articles: list[Article] = []
    for ticker in tool_input.tickers:
        q = urllib.parse.quote(_gnews_query(ticker, companies.get(ticker)))
        parsed = feedparser.parse(_GNEWS_RSS.format(q=q))
        for entry in parsed.entries:
            articles.append(_normalize_gnews_entry(entry))
    return articles


def _fetch_rss(tool_input: NewsInput) -> list[Article]:
    import feedparser

    feeds = tool_input.sources or list(DEFAULT_RSS_FEEDS)
    articles: list[Article] = []
    for feed_url in feeds:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries:
            articles.append(
                {
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "source": parsed.feed.get("title", feed_url),
                    "url": entry.get("link", ""),
                    "published_at": entry.get("published", ""),
                }
            )
    return articles


def _fetch_newsapi(tool_input: NewsInput) -> list[Article]:
    from trading_agent.config import get_settings

    settings = get_settings()
    if not settings.newsapi_key:
        return []  # キー未設定なら静かにスキップ

    import httpx

    query = " OR ".join([*(tool_input.tickers or []), *(tool_input.topics or [])]) or "stocks"
    params: dict[str, str] = {
        "apiKey": settings.newsapi_key,
        "language": "en",
        "pageSize": "50",
        "q": query,
    }
    try:
        resp = httpx.get("https://newsapi.org/v2/everything", params=params, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise NetworkError(f"NewsAPI failed: {exc}") from exc

    payload = resp.json()
    return [
        {
            "title": a.get("title", ""),
            "summary": a.get("description", ""),
            "source": (a.get("source") or {}).get("name", "NewsAPI"),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
        }
        for a in payload.get("articles", [])
    ]
