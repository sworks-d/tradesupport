"""topics-collector エージェント（AGENT_SPECS.md §6）。

ニュース・適時開示を収集し、重複除去・重要度分類・影響先銘柄抽出・影響テキスト生成を行い
topics テーブルに保存する。

LLM（llm_call）は **低重要度記事の重要度補強のみ** に使う（任意）。未登録/失敗時はルール結果を
そのまま採用するため、Anthropic キー無し・Ollama 無しでも動作する（§6.9 Graceful Degradation）。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.agents.serialization import save_topics
from trading_agent.mcp_tools.disclosure import DisclosureInput
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.mcp_tools.news import NewsInput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import BuySignal
from trading_agent.models.topics import Topic
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_HIGH_KEYWORDS = ("FOMC", "利上げ", "利下げ", "rate hike", "rate cut")
_SECTOR_KEYWORDS = ("半導体", "セクター", "業界", "sector", "industry")
_IMPORTANCE = {"high", "medium", "low"}

# 収集した1記事の正規化 dict
Item = dict[str, Any]


class TopicsCollectorInput(AgentInput):
    since_hours: int = 24
    sources: list[str] | None = None


class TopicsCollectorOutput(AgentOutput):
    topics_added: int = 0
    topics_updated: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_importance: dict[str, int] = Field(default_factory=dict)


def extract_affected_tickers(
    text: str, known_tickers: set[str], explicit_ticker: str | None = None
) -> list[str]:
    """テキストから影響先ティッカーを抽出する（明示記法 + universe 既知名）。"""
    found: set[str] = set()
    if explicit_ticker:
        found.add(explicit_ticker)
    found.update(re.findall(r"\$([A-Z]{1,5})", text))  # $NVDA
    found.update(re.findall(r"[（(](\d{4})[)）]", text))  # (7203)
    for token in re.findall(r"[A-Z]{2,5}", text):  # 既知の英字ティッカー
        if token in known_tickers:
            found.add(token)
    for token in re.findall(r"\b\d{4}\b", text):  # 既知の4桁コード
        if token in known_tickers:
            found.add(token)
    return sorted(found)


def rule_based_importance(
    item: Item, affected: list[str], portfolio_tickers: set[str], buy_tickers: set[str]
) -> str:
    """ルールベースの重要度（AGENT_SPECS §6.5）。"""
    title = item.get("title", "")
    if item.get("source_type") == "disclosure" and "決算速報" in title:
        return "high"
    text = f"{title} {item.get('summary', '')}"
    if any(kw in text for kw in _HIGH_KEYWORDS):
        return "high"
    if any(t in portfolio_tickers for t in affected):
        return "high"
    if any(t in buy_tickers for t in affected):
        return "medium"
    if any(kw in text for kw in _SECTOR_KEYWORDS):
        return "medium"
    return "low"


def classify_category(item: Item, affected: list[str]) -> str:
    """macro / sector / stock の分類。"""
    if affected:
        return "stock"
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    if any(kw in text for kw in _SECTOR_KEYWORDS):
        return "sector"
    return "macro"


def _norm_news(article: Item) -> Item:
    return {
        "title": article.get("title", ""),
        "summary": article.get("summary", ""),
        "url": article.get("url", ""),
        "source": article.get("source", ""),
        "published_at": article.get("published_at", ""),
        "source_type": "news",
        "ticker": None,
    }


def _norm_disclosure(disc: Item) -> Item:
    return {
        "title": disc.get("title", ""),
        "summary": disc.get("title", ""),
        "url": disc.get("url", ""),
        "source": disc.get("source", ""),
        "published_at": disc.get("published_at", ""),
        "source_type": "disclosure",
        "ticker": disc.get("ticker") or None,
    }


def _load_tickers(engine: Engine) -> tuple[set[str], set[str], set[str]]:
    with Session(engine) as session:
        portfolio = {
            p.ticker for p in session.exec(select(Portfolio).where(Portfolio.status == "active"))
        }
        buys = {b.ticker for b in session.exec(select(BuySignal).where(col(BuySignal.is_active)))}
        universe = {u.ticker for u in session.exec(select(Universe))}
    return portfolio, buys, universe


class TopicsCollectorAgent(Agent[TopicsCollectorInput]):
    """ニュース・開示の収集と分類を行うエージェント。"""

    name = "topics_collector"
    description = "ニュース・適時開示を収集し、重要度分類・影響先紐付けして保存する。"
    required_tools = ["news", "disclosure", "llm_call"]
    default_routing = "cold"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: TopicsCollectorInput) -> AgentOutput:
        since = utcnow() - timedelta(hours=agent_input.since_hours)
        items = await self._collect(agent_input, since)

        portfolio_tickers, buy_tickers, universe_tickers = _load_tickers(self._ctx.engine)
        known = universe_tickers | portfolio_tickers | buy_tickers

        topics: list[Topic] = []
        seen_urls: set[str] = set()
        for item in items:
            url = item["url"]
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            text = f"{item['title']} {item['summary']}"
            affected = extract_affected_tickers(text, known, item.get("ticker"))
            importance = rule_based_importance(item, affected, portfolio_tickers, buy_tickers)
            judged_by = "rule"
            if importance == "low":
                upgraded = await self._llm_reinforce(item, affected)
                if upgraded is not None:
                    importance, judged_by = upgraded, "llm"
            topics.append(
                _build_topic(item, affected, importance, judged_by, portfolio_tickers, buy_tickers)
            )

        # 集計は保存前に（commit 後は ORM インスタンスが expire し属性参照できないため）
        by_cat = Counter(t.category for t in topics)
        by_imp = Counter(t.importance for t in topics)
        added = len(topics)

        if not agent_input.dry_run and topics:
            save_topics(self._ctx.engine, topics)

        return TopicsCollectorOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=f"{added} 件のトピックスを収集（high={by_imp.get('high', 0)}）",
            topics_added=added,
            by_category=dict(by_cat),
            by_importance=dict(by_imp),
        )

    async def _collect(self, agent_input: TopicsCollectorInput, since: datetime) -> list[Item]:
        items: list[Item] = []
        try:
            nout = await self._ctx.call_tool(
                "news", NewsInput(since=since, sources=agent_input.sources)
            )
            if nout.success:
                items.extend(_norm_news(a) for a in getattr(nout, "articles", []))
        except Exception as exc:
            self._log.warning("topics_news_failed", error=str(exc))
        try:
            dout = await self._ctx.call_tool("disclosure", DisclosureInput(since=since))
            if dout.success:
                items.extend(_norm_disclosure(d) for d in getattr(dout, "disclosures", []))
        except Exception as exc:
            self._log.warning("topics_disclosure_failed", error=str(exc))
        return items

    async def _llm_reinforce(self, item: Item, affected: list[str]) -> str | None:
        """低重要度記事を LLM で再評価。失敗・未登録時は None（ルール結果維持）。"""
        prompt = (
            "次のニュースの重要度を high/medium/low で判定し JSON で返してください。\n"
            f'タイトル: {item.get("title", "")}\n要約: {item.get("summary", "")}\n'
            f"影響先候補: {affected}\n"
            '出力例: {"importance": "medium", "reasoning": "..."}'
        )
        try:
            out = await self._ctx.call_tool(
                "llm_call",
                LLMCallInput(
                    prompt=prompt,
                    purpose="classification",
                    routing_hint="cold",
                    agent=self.name,
                    invocation_id=self._ctx.invocation_id,
                ),
            )
            if not out.success:
                return None
            parsed = json.loads(getattr(out, "response", "") or "{}")
            importance = parsed.get("importance")
            if importance in _IMPORTANCE and importance != "low":
                return str(importance)
        except Exception as exc:
            self._log.warning("topics_llm_reinforce_failed", error=str(exc))
        return None


def _build_topic(
    item: Item,
    affected: list[str],
    importance: str,
    judged_by: str,
    portfolio_tickers: set[str],
    buy_tickers: set[str],
) -> Topic:
    impacts = []
    for ticker in affected:
        if ticker in portfolio_tickers:
            impacts.append(f"{ticker} 保有銘柄に影響")
        elif ticker in buy_tickers:
            impacts.append(f"{ticker} 買い候補に影響")
    impact_text = " / ".join(impacts) if impacts else "影響先は明確に特定できませんでした。"
    title = item.get("title", "")
    return Topic(
        source=item.get("source", ""),
        source_url=item.get("url", ""),
        category=classify_category(item, affected),
        importance=importance,
        headline=title,
        summary=item.get("summary", ""),
        original_text_hash=str(abs(hash(title))),
        affected_tickers=affected,
        impact_text=impact_text,
        fetched_by="morning_batch",
        importance_judged_by=judged_by,
    )
