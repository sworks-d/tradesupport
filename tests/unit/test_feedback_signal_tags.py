"""Track B: summarize_feedback の by_signal_tags 集計（tag 別 shadow 成績）の単体テスト。

純関数（DB 非依存）。1 record が複数 tag を持てば各 tag に計上、n>=20 判定の素データを返す。
"""

from __future__ import annotations

from trading_agent.reporting.feedback import (
    compare_signal_tags_vs_baseline,
    summarize_feedback,
)


def _rec(tags, outcome, r):
    return {
        "hit_or_miss": outcome,
        "r_multiple": r,
        "entry_signal_tags": tags,
        "personality": "REI",
        "gendo_stance": "推し",
        "exit_reason": "time_exit",
    }


def test_by_signal_tags_aggregates_hit_rate_and_avg_r() -> None:
    records = [
        _rec(["sector_rs"], "hit", 2.0),
        _rec(["sector_rs"], "miss", -1.0),
        _rec(["sector_rs"], "hit", 1.0),
    ]
    out = summarize_feedback(records)["by_signal_tags"]
    assert out["sector_rs"]["n"] == 3
    assert out["sector_rs"]["hit"] == 2
    assert out["sector_rs"]["miss"] == 1
    assert abs(out["sector_rs"]["hit_rate"] - 2 / 3) < 1e-9
    assert abs(out["sector_rs"]["avg_r"] - (2.0 - 1.0 + 1.0) / 3) < 1e-9


def test_multiple_tags_counted_separately() -> None:
    records = [_rec(["sector_rs", "earnings_accel"], "hit", 1.5)]
    out = summarize_feedback(records)["by_signal_tags"]
    assert out["sector_rs"]["n"] == 1 and out["earnings_accel"]["n"] == 1
    assert out["sector_rs"]["hit"] == 1 and out["earnings_accel"]["hit"] == 1


def test_no_tags_yields_empty_signal_tag_section() -> None:
    records = [_rec([], "hit", 1.0), _rec(None, "miss", -1.0)]
    out = summarize_feedback(records)
    assert out["by_signal_tags"] == {}
    # 既存軸は影響を受けない（回帰防止）
    assert out["by_personality"]["REI"]["n"] == 2


def test_r_multiple_none_excluded_from_avg_but_counted_in_n() -> None:
    records = [_rec(["sector_rs"], "neutral", None), _rec(["sector_rs"], "hit", 2.0)]
    out = summarize_feedback(records)["by_signal_tags"]
    assert out["sector_rs"]["n"] == 2
    # r_sum は 2.0 のみ。avg_r = 2.0 / n(2) = 1.0
    assert abs(out["sector_rs"]["avg_r"] - 1.0) < 1e-9


# === codex #3: tag 有無の対照成績（正味エッジ）===


def test_compare_signal_tags_vs_baseline_net_edge() -> None:
    """タグあり cohort と なし cohort の hit_rate/avg_r 差（net edge）を出す。"""
    records = [
        _rec(["sector_rs"], "hit", 2.0),   # with: hit
        _rec(["sector_rs"], "hit", 1.0),   # with: hit
        _rec([], "miss", -1.0),            # without: miss
        _rec([], "miss", -1.0),            # without: miss
    ]
    out = compare_signal_tags_vs_baseline(records)
    assert out["sector_rs"]["with"]["n"] == 2
    assert out["sector_rs"]["with"]["hit_rate"] == 1.0
    assert out["sector_rs"]["without"]["n"] == 2
    assert out["sector_rs"]["without"]["hit_rate"] == 0.0
    assert out["sector_rs"]["net_hit_rate"] == 1.0   # 1.0 - 0.0
    assert out["sector_rs"]["net_avg_r"] == 2.5      # 1.5 - (-1.0)
    assert "control不足" in out["sector_rs"]["verdict"]  # without_n=2 < 5


def test_compare_verdict_requires_both_cohorts_n20() -> None:
    """codex P1: 判定可は with/without 両側 n>=20。control 薄いと control不足。"""
    # with 25 (全 hit) / without 2 → control不足（net=with-0 の誤読防止）
    recs = [_rec(["t"], "hit", 1.0) for _ in range(25)] + [_rec([], "miss", -1.0) for _ in range(2)]
    assert "control不足" in compare_signal_tags_vs_baseline(recs)["t"]["verdict"]
    # with 25 / without 25 → 判定可
    recs2 = [_rec(["t"], "hit", 1.0) for _ in range(25)] + [_rec([], "miss", -1.0) for _ in range(25)]
    assert compare_signal_tags_vs_baseline(recs2)["t"]["verdict"] == "判定可"
    # with 5 / without 25 → サンプル不足（with 側 <20）
    recs3 = [_rec(["t"], "hit", 1.0) for _ in range(5)] + [_rec([], "miss", -1.0) for _ in range(25)]
    assert "サンプル不足" in compare_signal_tags_vs_baseline(recs3)["t"]["verdict"]


def test_compare_signal_tags_vs_baseline_empty_when_no_tags() -> None:
    records = [_rec([], "hit", 1.0), _rec([], "miss", -1.0)]
    assert compare_signal_tags_vs_baseline(records) == {}


def test_compare_signal_tags_control_excludes_only_that_tag() -> None:
    """control(without) は『その tag を持たない』集合。別 tag を持つ record は control に入る。"""
    records = [
        _rec(["sector_rs"], "hit", 1.0),
        _rec(["earnings_accel"], "miss", -1.0),  # sector_rs から見れば without 側
    ]
    out = compare_signal_tags_vs_baseline(records)
    assert out["sector_rs"]["with"]["n"] == 1
    assert out["sector_rs"]["without"]["n"] == 1  # earnings_accel record が control に入る
    assert out["earnings_accel"]["with"]["n"] == 1
    assert out["earnings_accel"]["without"]["n"] == 1
