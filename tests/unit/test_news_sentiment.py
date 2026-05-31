"""news_sentiment のテスト（v2.10 C3 / M9）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.llm.news_sentiment import (
    NewsItem,
    SentimentScore,
    _dedup_by_title,
    _is_important,
    analyze_news,
    reset_cache,
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ns.sqlite")
    create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_cache()
    yield
    reset_cache()


class TestKeywordFilter:
    def test_important_keyword_detected(self):
        n = NewsItem(title="A 社が上方修正を発表")
        assert _is_important(n) is True

    def test_negative_keyword_detected(self):
        n = NewsItem(title="B 社が下方修正、株価下落")
        assert _is_important(n) is True

    def test_unimportant_no_keyword(self):
        n = NewsItem(title="C 社が新オフィス移転")
        assert _is_important(n) is False


class TestDedup:
    def test_same_title_prefix_dedup(self):
        items = [
            NewsItem(title="FRB が利下げを示唆 (Bloomberg)"),
            NewsItem(title="FRB が利下げを示唆 (Reuters)"),
            NewsItem(title="FRB が利下げを示唆 (日経)"),
        ]
        result = _dedup_by_title(items)
        assert len(result) == 1


class TestAnalyzeNews:
    def test_empty_returns_neutral(self, engine):
        result = analyze_news(engine, "7203", [])
        assert result.score == 50.0
        assert result.used_llm is False

    def test_no_keyword_returns_neutral(self, engine):
        news = [NewsItem(title="新オフィス開設のお知らせ")]
        result = analyze_news(engine, "7203", news)
        assert result.score == 50.0
        assert result.used_llm is False

    def test_keyword_triggers_llm(self, engine):
        news = [NewsItem(title="トヨタが上方修正、年間利益が過去最高に")]
        with patch(
            "trading_agent.llm.news_sentiment._call_haiku_sentiment",
            return_value=(75.0, "上方修正で positive"),
        ):
            result = analyze_news(engine, "7203", news)
        assert result.score == 75.0
        assert result.used_llm is True

    def test_cache_avoids_repeat_llm(self, engine):
        news = [NewsItem(title="トヨタが上方修正", body="詳細情報")]
        call_count = [0]

        def mock_haiku(ticker, news_text):
            call_count[0] += 1
            return 70.0, "positive"

        with patch(
            "trading_agent.llm.news_sentiment._call_haiku_sentiment",
            side_effect=mock_haiku,
        ):
            r1 = analyze_news(engine, "7203", news)
            r2 = analyze_news(engine, "7203", news)
        assert r1.score == 70.0
        assert r2.score == 70.0
        # 2 回目は cache hit
        assert call_count[0] == 1

    def test_llm_failure_falls_back_to_neutral(self, engine):
        news = [NewsItem(title="トヨタが上方修正")]
        with patch(
            "trading_agent.llm.news_sentiment._call_haiku_sentiment",
            return_value=None,
        ):
            result = analyze_news(engine, "7203", news)
        assert result.score == 50.0

    def test_budget_exceeded_falls_back(self, engine):
        # 既存 budget を超過させる
        news = [NewsItem(title="A 社が上方修正")]
        from trading_agent.models.analytics import CostLog
        from trading_agent.utils.time_utils import today_jst

        with Session(engine) as s:
            s.add(
                CostLog(
                    date=today_jst(),
                    model="haiku",
                    agent="test",
                    purpose="budget_test",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0,
                    cost_jpy=10_000.0,  # 大幅超過
                    invocation_id="test_burst",
                )
            )
            s.commit()

        with patch(
            "trading_agent.llm.news_sentiment._call_haiku_sentiment",
            return_value=(80.0, "should_not_be_called"),
        ) as m:
            result = analyze_news(engine, "7203", news)
        # 予算超過なので LLM 呼ばれず 50 にフォールバック
        assert result.score == 50.0
        m.assert_not_called()

    def test_dedup_reduces_processing(self, engine):
        news = [
            NewsItem(title="A 社が上方修正を発表 (Bloomberg)"),
            NewsItem(title="A 社が上方修正を発表 (Reuters)"),
        ]
        call_count = [0]

        def mock_haiku(ticker, news_text):
            call_count[0] += 1
            return 70.0, "positive"

        with patch(
            "trading_agent.llm.news_sentiment._call_haiku_sentiment",
            side_effect=mock_haiku,
        ):
            analyze_news(engine, "X", news)
        # 同じタイトル 2 件 → dedup で 1 件のみ LLM
        assert call_count[0] == 1
