"""topics-collector エージェント（AGENT_SPECS.md §6）。

ニュース・適時開示を収集し、重複除去・重要度分類・影響先銘柄抽出・影響テキスト生成を行い
topics テーブルに保存する。

LLM（llm_call）は **低重要度記事の重要度補強のみ** に使う（任意）。未登録/失敗時はルール結果を
そのまま採用するため、Anthropic キー無しでも動作する（§6.9 Graceful Degradation）。
"""

from __future__ import annotations

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
from trading_agent.llm.json_extract import extract_json
from trading_agent.mcp_tools.disclosure import DisclosureInput
from trading_agent.mcp_tools.llm_call import LLMCallInput
from trading_agent.mcp_tools.news import NewsInput
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.signals import BuySignal
from trading_agent.models.topics import Topic
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

# v2.5 TASK-N3: 重要度キーワードを拡張（市場の話題変化に追随できるよう）
# 環境変数 TOPICS_HIGH_KEYWORDS / TOPICS_SECTOR_KEYWORDS でカンマ区切り上書き可
import os as _os_n3
_HIGH_KEYWORDS_DEFAULT = (
    # マクロ金融（旧）
    "FOMC", "利上げ", "利下げ", "rate hike", "rate cut",
    # マクロ追加
    "日銀", "BOJ", "ECB", "PMI", "CPI", "GDP", "雇用統計",
    "緊急", "暴落", "急騰", "ストップ高", "ストップ安",
    # 個別重要イベント
    "決算速報", "決算発表", "上方修正", "下方修正",
    "M&A", "TOB", "公開買付", "業務提携", "資本提携",
)
_HIGH_KEYWORDS = tuple(
    _os_n3.environ.get("TOPICS_HIGH_KEYWORDS", ",".join(_HIGH_KEYWORDS_DEFAULT)).split(",")
)
_SECTOR_KEYWORDS_DEFAULT = (
    "半導体", "セクター", "業界", "sector", "industry",
    "AI", "EV", "脱炭素", "防衛", "バイオ", "メタバース", "DX",
)
_SECTOR_KEYWORDS = tuple(
    _os_n3.environ.get("TOPICS_SECTOR_KEYWORDS", ",".join(_SECTOR_KEYWORDS_DEFAULT)).split(",")
)
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
    """テキストから影響先ティッカーを抽出する（v2.1 TASK-S2: 厳格化）。

    旧版は「2-5 文字大文字」「4 桁数字」を universe に部分マッチ → false positive 多発
    （"USA"・"BUY"・"2024 年"・"3,000 万円" 等が銘柄扱いされる問題）。

    新版は **明示記法のみ** を採用：
      - 米株: `$NVDA`（$ プレフィックス必須）
      - 米株: `NYSE:NVDA` / `NASDAQ:NVDA`（取引所プレフィックス）
      - JP 株: `(7203)` / `（7203）` / `7203.T`
      - explicit_ticker（API からの明示指定）
    上記以外は紐付けない（false positive < false negative の方が安全）。
    """
    found: set[str] = set()
    if explicit_ticker:
        found.add(explicit_ticker)
    # 米株：$NVDA 形式
    found.update(re.findall(r"\$([A-Z]{1,5})\b", text))
    # 米株：NYSE: / NASDAQ: プレフィックス
    found.update(re.findall(r"(?:NYSE|NASDAQ):\s*([A-Z]{1,5})\b", text))
    # JP 株：(7203) または （7203）
    found.update(re.findall(r"[（(](\d{4})[)）]", text))
    # JP 株：7203.T 形式
    found.update(re.findall(r"\b(\d{4})\.T\b", text))

    # known_tickers でフィルタ（universe にない ticker は除外）
    return sorted(t for t in found if t in known_tickers)


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
    """macro / sector / stock の分類。

    v2.5 TASK-T2: 複合カテゴリ（stock + macro マッチでも単一分類）の限界を明示。
    将来は複数カテゴリ list を返す設計に拡張余地あり（DB 構造変更を伴うため別件）。
    """
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    has_sector_kw = any(kw in text for kw in _SECTOR_KEYWORDS)
    has_high_kw = any(kw in text for kw in _HIGH_KEYWORDS)
    # 優先順: マクロイベント（FOMC 等）> 個別銘柄 > セクター > その他マクロ
    if affected and not has_high_kw:
        return "stock"
    if has_high_kw:
        return "macro"
    if affected:
        return "stock"
    if has_sector_kw:
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

    # LLM 補強の連続失敗をトリップする閾値。これを超えたら以降の LLM 呼出をスキップし
    # ルールベースで進む（cold path が壊れている時にバッチ全体を道連れにしない安全装置）。
    _LLM_FAILURE_TRIP = 5

    # 1バッチあたりの LLM 補強の上限。topics は数百件来ることがあり、低重要度全件を LLM で
    # 再評価すると簡単に日次予算を食い潰す。上位 N 件だけ補強する（ニュース順 = 新しい順を想定）。
    _LLM_CALL_CAP_PER_BATCH = 30

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)
        self._llm_failure_count = 0
        self._llm_circuit_open = False
        self._llm_call_count = 0

    async def execute(self, agent_input: TopicsCollectorInput) -> AgentOutput:
        # 各バッチで状態をリセット（再実行時にトリップ状態を引きずらない）
        self._llm_failure_count = 0
        self._llm_circuit_open = False
        self._llm_call_count = 0

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
        """低重要度記事を LLM で再評価。失敗・未登録時は None（ルール結果維持）。

        サーキットブレーカー：同一バッチで連続失敗が `_LLM_FAILURE_TRIP` を超えたら
        以降の呼出をスキップ（cold path 障害でバッチを長時間ハングさせない）。
        バッチ上限：1バッチで `_LLM_CALL_CAP_PER_BATCH` 件を超えたら以降スキップ
        （ニュースが数百件来た時に日次予算を瞬時に消費するのを防ぐ）。
        """
        if self._llm_circuit_open:
            return None
        if self._llm_call_count >= self._LLM_CALL_CAP_PER_BATCH:
            return None
        self._llm_call_count += 1

        # v2.10: ハルシネーション抑止指示を明示（CASPER と同じ厳格化）
        # 与えられた材料以外の事実・数値・将来予測を生成させない。
        # 「影響先候補」に無い銘柄を LLM が「関連がありそう」と推論で追加するのを禁止。
        prompt = (
            "あなたはニュースの重要度を判定するアシスタントです。\n\n"
            "【厳守事項：ハルシネーション禁止】\n"
            "  - 与えられたタイトル・要約に書かれていない事実・数値・将来予測を生成しないこと\n"
            "  - 影響先候補に含まれない銘柄を「関連がありそう」と推論で追加しないこと\n"
            "  - 売買推奨・目標株価・EPS 等の数値を捏造しないこと\n"
            "  - 情報が不十分な場合は 'low' と判定し reasoning に「情報不足」と明記すること\n\n"
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
                self._record_llm_failure("llm_call_unsuccessful")
                return None
            parsed = extract_json(getattr(out, "response", None))
            importance = parsed.get("importance")
            if importance in _IMPORTANCE and importance != "low":
                # 成功 → カウンタリセット（散発的失敗ならトリップしない）
                self._llm_failure_count = 0
                return str(importance)
            # 期待値が取れなかった＝レスポンス形式問題。失敗としてカウント
            if not parsed:
                self._record_llm_failure("empty_or_unparseable_json")
        except Exception as exc:
            self._log.warning("topics_llm_reinforce_failed", error=str(exc))
            self._record_llm_failure(str(exc))
        return None

    def _record_llm_failure(self, reason: str) -> None:
        """LLM 呼出失敗をカウントし、閾値超過でサーキットを開く。"""
        self._llm_failure_count += 1
        if (
            not self._llm_circuit_open
            and self._llm_failure_count >= self._LLM_FAILURE_TRIP
        ):
            self._llm_circuit_open = True
            self._log.warning(
                "topics_llm_circuit_open",
                consecutive_failures=self._llm_failure_count,
                last_reason=reason,
                note="以降の LLM 補強をスキップしルールベースで進行",
            )


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
