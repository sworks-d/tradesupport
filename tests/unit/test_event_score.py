"""(c) fundamental_event_score（screening/event_score.py）の単体テスト。

compute_fundamental_event_score: タグ → 0-100（50中立）。bucket_event_score: 単一真実源・
None/version不一致 → unscored（p-hacking 防止 + silent mis-bucket 回避）。record-only・¥0。
"""

from __future__ import annotations

from trading_agent.screening.event_score import (
    EVENT_SCORE_VERSION,
    bucket_event_score,
    compute_fundamental_event_score,
)


def test_no_tags_is_neutral_50() -> None:
    assert compute_fundamental_event_score([]) == 50.0
    assert compute_fundamental_event_score(None) == 50.0


def test_positive_events_raise_score() -> None:
    assert compute_fundamental_event_score(["event_upward_revision"]) == 75.0
    assert compute_fundamental_event_score(["event_dividend_hike"]) == 65.0
    assert compute_fundamental_event_score(["earnings_accel"]) == 65.0


def test_negative_events_lower_score() -> None:
    assert compute_fundamental_event_score(["event_downward_revision"]) == 25.0
    assert compute_fundamental_event_score(["event_dividend_cut"]) == 35.0
    both_neg = ["event_downward_revision", "event_dividend_cut"]
    assert compute_fundamental_event_score(both_neg) == 10.0


def test_news_alone_stays_in_mid_band() -> None:
    """news 見出し（弱い）は単独では tail(low<=40 / high>=60)に届かせない設計（±8）。"""
    assert compute_fundamental_event_score(["news_positive"]) == 58.0  # mid（<60）
    assert compute_fundamental_event_score(["news_negative"]) == 42.0  # mid（>40）


def test_clamped_to_0_100() -> None:
    allpos = ["event_upward_revision", "event_dividend_hike", "earnings_accel", "news_positive"]
    assert compute_fundamental_event_score(allpos) == 100.0  # 50+25+15+15+8=113 → 100
    allneg = ["event_downward_revision", "event_dividend_cut", "news_negative", "news_negative"]
    assert compute_fundamental_event_score(allneg) >= 0.0


def test_unknown_tags_ignored() -> None:
    assert compute_fundamental_event_score(["sector_rs", "pead_candidate", "foo"]) == 50.0


def test_bucket_low_mid_high() -> None:
    v = EVENT_SCORE_VERSION
    assert bucket_event_score(40.0, v).bucket == "low"   # <=40 境界
    assert bucket_event_score(25.0, v).bucket == "low"
    assert bucket_event_score(50.0, v).bucket == "mid"
    assert bucket_event_score(59.9, v).bucket == "mid"
    assert bucket_event_score(60.0, v).bucket == "high"  # >=60 境界
    assert bucket_event_score(90.0, v).bucket == "high"


def test_bucket_returns_versions() -> None:
    r = bucket_event_score(75.0, EVENT_SCORE_VERSION)
    assert r.bucket == "high"
    assert r.score_version == EVENT_SCORE_VERSION
    assert r.cutpoint_version == "cutpoint_v1"


def test_bucket_none_score_is_unscored() -> None:
    r = bucket_event_score(None, EVENT_SCORE_VERSION)
    assert r.bucket == "unscored"
    assert r.cutpoint_version is None  # bucket していないので cut version は付かない


def test_bucket_unknown_version_is_unscored() -> None:
    """version が registry に無い/None → unscored（誤った閾値で bucket しない・p-hacking 防止）。"""
    assert bucket_event_score(75.0, "event_score_v99").bucket == "unscored"
    assert bucket_event_score(75.0, None).bucket == "unscored"
