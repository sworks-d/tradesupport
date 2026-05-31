"""News sentiment analyzer（v2.10 C3）。

ニュースから 0-100 スケールのセンチメントスコアを算出。
コスト削減のため D1-D8 を統合：

  D1 keyword フィルタ（重要キーワード含むニュースのみ LLM 投入）
  D2 入力トークン削減（見出し + リード 300 文字）
  D3 出力 JSON schema 固定（score + 短理由）
  D5 24h キャッシュ（content_hash → score）
  D6 類似ニュース dedup（title prefix 一致でグループ化）
  D8 銘柄絞り（呼び出し側で対象を絞る前提）

安全装置:
  - BudgetGuard で予算チェック → 超過時は固定値 50 (中立) にフォールバック
  - LLM 失敗時も 50 にフォールバック
  - cache hit 時は LLM 呼び出しゼロ

スコア仕様:
  - 0: 強い negative
  - 50: 中立 / ニュースなし
  - 100: 強い positive
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.llm.budget import BudgetGuard
from trading_agent.models.analytics import CostLog
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("llm.news_sentiment")

# D1: 重要 keyword（含まれるニュースだけ LLM に投入。それ以外は 50 中立）
_IMPORTANT_KEYWORDS = [
    # ポジティブ系
    "上方修正", "増益", "増収", "黒字転換", "業績好調", "買収", "提携", "新製品",
    "好決算", "増配", "自社株買い", "IPO", "上場", "受注", "契約",
    # ネガティブ系
    "下方修正", "減益", "減収", "赤字", "業績悪化", "不祥事", "粉飾",
    "リコール", "提訴", "決算延期", "上場廃止", "減配", "希薄化", "倒産",
    # 中立だが影響大
    "決算", "業績予想", "配当", "増資", "減資",
]

_KEYWORD_PATTERN = re.compile("|".join(re.escape(k) for k in _IMPORTANT_KEYWORDS))


@dataclass
class NewsItem:
    """ニュース 1 件分。"""

    title: str
    body: str = ""  # リード文・本文（無くてもよい）
    url: str = ""
    published_at: dt.datetime | None = None

    @property
    def content_hash(self) -> str:
        """D5: キャッシュキー（title + body 先頭 200 文字）。"""
        key = (self.title + (self.body[:200] if self.body else "")).strip().lower()
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class SentimentScore:
    """1 件のニュース判定結果。"""

    score: float  # 0-100
    reason: str
    used_llm: bool  # True: LLM 呼んだ / False: キャッシュ or フォールバック


# モジュール内キャッシュ（プロセス内 TTL 24h）
_CACHE: dict[str, tuple[dt.datetime, SentimentScore]] = {}
_CACHE_TTL = dt.timedelta(hours=24)


def _cache_get(hash_key: str) -> SentimentScore | None:
    now = utcnow()
    entry = _CACHE.get(hash_key)
    if entry is None:
        return None
    when, score = entry
    if (now - when) > _CACHE_TTL:
        del _CACHE[hash_key]
        return None
    return score


def _cache_put(hash_key: str, score: SentimentScore) -> None:
    _CACHE[hash_key] = (utcnow(), score)


def reset_cache() -> None:
    """テスト用キャッシュリセット。"""
    _CACHE.clear()


def _dedup_by_title(news_list: list[NewsItem]) -> list[NewsItem]:
    """D6: 類似タイトルを 1 件に集約。

    "(" 以前 + 先頭 15 文字を key にして、出版元違い (Bloomberg/Reuters/日経) でも同じ内容なら 1 件に。
    """
    seen: dict[str, NewsItem] = {}
    for n in news_list:
        # "(" 以前を取って 15 文字に切り詰め（出版元タグ除去 + 先頭一致）
        core = n.title.split("(")[0].strip()[:15].lower()
        if core not in seen:
            seen[core] = n
    return list(seen.values())


def _is_important(news: NewsItem) -> bool:
    """D1: 重要 keyword が含まれるか。"""
    text = (news.title + " " + news.body[:200]).lower()
    return bool(_KEYWORD_PATTERN.search(text))


def _truncate_input(news: NewsItem, max_chars: int = 300) -> str:
    """D2: 入力テキストを 300 文字に制限。"""
    text = news.title
    if news.body:
        text += " | " + news.body[:200]
    return text[:max_chars]


def _estimate_cost_jpy(input_tokens: int, output_tokens: int = 30) -> float:
    """Haiku 単価でコスト試算。"""
    # Haiku: $1/MTok in + $5/MTok out, $1 = ¥150 仮
    cost_usd = (input_tokens / 1_000_000) * 1.0 + (output_tokens / 1_000_000) * 5.0
    return cost_usd * 150.0


def _call_haiku_sentiment(
    ticker: str, news_text: str
) -> tuple[float, str] | None:
    """Haiku を呼んで sentiment + reason を返す。失敗時 None。

    D3: 出力 JSON schema 固定。
    """
    try:
        from trading_agent.llm.providers.haiku_fallback import HaikuFallbackClient

        client = HaikuFallbackClient()
        prompt = (
            f"銘柄 {ticker} に対するニュースのセンチメント (-1.0 〜 +1.0) と 50 字以内の理由を JSON で返してください。"
            f"ニュース: {news_text}\n"
            f'出力形式: {{"score": <number>, "reason": "<text>"}}'
        )
        response = client.complete(prompt, max_tokens=80)
        # JSON 抽出（モデルが余計な前後文を付ける場合）
        match = re.search(r"\{[^}]+\}", response)
        if not match:
            return None
        data = json.loads(match.group(0))
        score_raw = float(data.get("score", 0))
        # -1.0〜+1.0 を 0〜100 に変換
        score_0_100 = max(0, min(100, 50 + score_raw * 50))
        reason = str(data.get("reason", ""))[:80]
        return score_0_100, reason
    except Exception as exc:
        _log.warning("haiku_sentiment_call_failed", error_type=type(exc).__name__)
        return None


def analyze_news(
    engine: Engine,
    ticker: str,
    news_list: list[NewsItem],
    *,
    routing_hint: str | None = None,
) -> SentimentScore:
    """指定 ticker のニュース群から sentiment スコア (0-100) を返す。

    Args:
        engine: DB エンジン（BudgetGuard 用）
        ticker: 対象銘柄
        news_list: ニュースのリスト
        routing_hint: budget guard 用ヒント（"critical" で予算超過でも通す）
    """
    if not news_list:
        return SentimentScore(score=50.0, reason="ニュース無し→中立", used_llm=False)

    # D6: 重複 dedup
    deduped = _dedup_by_title(news_list)

    # D1: 重要 keyword フィルタ
    important = [n for n in deduped if _is_important(n)]
    if not important:
        return SentimentScore(
            score=50.0,
            reason=f"重要キーワード無し ({len(deduped)} 件除外)",
            used_llm=False,
        )

    # 各ニュースのスコアを集計（cache or LLM）
    scores: list[float] = []
    reasons: list[str] = []
    llm_calls = 0
    guard = BudgetGuard(engine)

    for news in important[:5]:  # 最大 5 件まで（コスト制御）
        # D5: cache check
        cached = _cache_get(news.content_hash)
        if cached is not None:
            scores.append(cached.score)
            reasons.append(cached.reason)
            continue

        # D2: 入力制限
        news_text = _truncate_input(news)
        estimated_cost = _estimate_cost_jpy(input_tokens=len(news_text) // 3)

        # 予算チェック
        ok, msg = guard.can_proceed(estimated_cost, routing_hint=routing_hint)
        if not ok:
            _log.info("news_sentiment_budget_skip", ticker=ticker, msg=msg)
            scores.append(50.0)
            reasons.append("予算超過→中立")
            continue

        # LLM 呼び出し
        result = _call_haiku_sentiment(ticker, news_text)
        llm_calls += 1
        if result is None:
            scores.append(50.0)
            reasons.append("LLM 失敗→中立")
            continue
        score, reason = result
        scores.append(score)
        reasons.append(reason)
        # cache
        _cache_put(news.content_hash, SentimentScore(score, reason, True))
        # CostLog 記録（invocation_id を content_hash で unique 化）
        try:
            tokens_in = max(1, len(news_text) // 3)
            with Session(engine) as s:
                s.add(
                    CostLog(
                        date=utcnow().date(),
                        model="haiku",
                        agent="news_sentiment",
                        purpose="sentiment_analysis",
                        tokens_in=tokens_in,
                        tokens_out=30,
                        cost_usd=estimated_cost / 150.0,
                        cost_jpy=estimated_cost,
                        invocation_id=f"ns_{ticker}_{news.content_hash}",
                    )
                )
                s.commit()
        except Exception as exc:
            _log.warning("cost_log_failed", error_type=type(exc).__name__)

    # 平均スコア
    if not scores:
        return SentimentScore(score=50.0, reason="判定不能", used_llm=False)
    avg = sum(scores) / len(scores)
    return SentimentScore(
        score=avg,
        reason=f"{len(scores)} 件平均 (LLM {llm_calls} 回)",
        used_llm=llm_calls > 0,
    )
