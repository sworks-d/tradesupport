"""テーマ強度の単体テスト（v2.10 Phase 4）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.topics import Topic
from trading_agent.portfolio import theme_strength as ts


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "theme.sqlite")
    create_all(eng)
    return eng


def _add_topic(
    eng,
    headline: str,
    summary: str = "",
    days_ago: int = 0,
    impact_text: str = "",
) -> None:
    with Session(eng, expire_on_commit=False) as s:
        now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
        s.add(
            Topic(
                collected_at=now,
                source="test",
                source_url="https://example.com",
                category="stock",
                importance="medium",
                headline=headline,
                summary=summary,
                impact_text=impact_text,
                original_text_hash=f"hash-{headline}-{days_ago}",
                fetched_by="test",
                importance_judged_by="test",
            )
        )
        s.commit()


class TestKeywordMatch:
    def test_AI_キーワードマッチ(self) -> None:
        m = ts._match_themes("生成AIブームで NVIDIA 株上昇")
        assert "AI" in m
        # NVIDIA は半導体にも入っているのでマッチ
        assert "半導体" in m

    def test_キーワード未ヒットは空集合(self) -> None:
        assert ts._match_themes("特に何もないニュース") == set()

    def test_None_空文字は空集合(self) -> None:
        assert ts._match_themes("") == set()


class TestComputeThemeStrength:
    def test_topics無しでinsufficient_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        result = ts.compute_theme_strength(eng)
        assert result["status"] == "insufficient_data"
        assert result["total_topics_30d"] == 0

    def test_AI記事1件で集計される(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_topic(eng, "ChatGPT 旋風で OpenAI 注目", days_ago=1)
        result = ts.compute_theme_strength(eng)
        assert result["status"] == "active"
        assert result["themes"]["AI"]["mentions_7d"] == 1
        assert result["themes"]["AI"]["mentions_30d"] == 1
        assert "AI" in result["top_themes"]

    def test_過去30日内に半導体記事複数_intensity計算(
        self, tmp_path: Path
    ) -> None:
        eng = _engine(tmp_path)
        for d in [1, 3, 5, 10, 20]:
            _add_topic(eng, f"半導体製造 day {d}", days_ago=d)
        result = ts.compute_theme_strength(eng)
        assert result["themes"]["半導体"]["mentions_30d"] == 5
        # 過去 7 日内は 3 件（1, 3, 5）
        assert result["themes"]["半導体"]["mentions_7d"] == 3
        # intensity は log10(6)*40 ≈ 31.1
        assert 25 < result["themes"]["半導体"]["intensity"] < 35

    def test_テーマ未ヒットの記事はカウントされない_推測しない(
        self, tmp_path: Path
    ) -> None:
        eng = _engine(tmp_path)
        _add_topic(eng, "天気予報", days_ago=1)
        _add_topic(eng, "経済全般 (テーマ不明)", days_ago=2)
        result = ts.compute_theme_strength(eng)
        # 全テーマの mentions_30d はゼロ
        for theme, m in result["themes"].items():
            assert m["mentions_30d"] == 0
        assert result["status"] == "no_matches"

    def test_momentum_の符号(self, tmp_path: Path) -> None:
        """7d ペースが 30d 平均より速いなら momentum > 0。"""
        eng = _engine(tmp_path)
        # 過去 7 日に 5 件、それ以前 23 日に 2 件 → 7d ペース速い
        for d in [1, 2, 3, 4, 5]:
            _add_topic(eng, f"防衛装備品契約 day {d}", days_ago=d)
        for d in [15, 25]:
            _add_topic(eng, f"防衛装備品契約 day {d}", days_ago=d)
        result = ts.compute_theme_strength(eng)
        d = result["themes"]["防衛"]
        assert d["mentions_7d"] == 5
        assert d["mentions_30d"] == 7
        # 7d ペース ×30/7 = 21.4 / 30d 7 = 3.06 → momentum ≈ 2.06
        assert d["momentum"] > 1.0  # 強い加速

    def test_top_themes_は最大3件(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        for i in range(5):
            _add_topic(eng, f"AI 記事 {i}", days_ago=i + 1)
        for i in range(3):
            _add_topic(eng, f"半導体 ニュース {i}", days_ago=i + 1)
        result = ts.compute_theme_strength(eng)
        assert len(result["top_themes"]) <= 3
        assert "AI" in result["top_themes"]
        assert "半導体" in result["top_themes"]
