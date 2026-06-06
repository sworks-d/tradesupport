"""ニュース/ファンダ→利益エッジ統合 readout（phase_c_status._edge_readout）の単体テスト。

発火→評価→正味エッジ→score bucket を1ビューに統合し、small-n を status/display_only フラグで
欺瞞なく可視化する（UI は判定せずフラグを忠実描画）。record-only・DB のみ。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from scripts.phase_c_status import _edge_readout, _edge_status, _score_bucket_outcomes
from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.screening.event_score import EVENT_SCORE_VERSION
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "edge.sqlite")
    create_all(eng)
    return eng


def _add(
    engine, *, tags=None, score=None, version=None, hit=None, ret=None, verified=True,
    filled_via="ds_dispatch", broker_mode="paper",
):
    # finding B: _score_bucket_outcomes は official paper のみ集計するため、既定で
    # official(ds_dispatch)/paper を付与する。非official 除外の検証はこの2引数で上書き。
    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=dt.date(2026, 6, 6), ticker="3697", action="buy", status="awaiting")
        d.entry_signal_tags = tags or []
        d.filled_via = filled_via
        d.entry_broker_mode = broker_mode
        if score is not None:
            d.fundamental_event_score = score
            d.event_score_version = version or EVENT_SCORE_VERSION
        if hit is not None:
            d.hit_or_miss = hit
        if ret is not None:
            d.actual_return = ret
        if verified:
            d.verified_at = utcnow()
        s.add(d)
        s.commit()


def test_edge_status_thresholds() -> None:
    assert _edge_status(0) == ("insufficient", True)
    assert _edge_status(29) == ("insufficient", True)
    assert _edge_status(30) == ("exploratory", True)
    assert _edge_status(99) == ("exploratory", True)
    assert _edge_status(100) == ("candidate", False)  # 候補のみ display_only=False


def test_edge_readout_schema_flat_with_flags(tmp_path: Path) -> None:
    """by_signal はフラット配列で各行に n/status/display_only フラグを持つ（UI 1:1 描画用）。"""
    engine = _engine(tmp_path)
    _add(engine, tags=["earnings_accel"])
    r = _edge_readout(engine)
    assert isinstance(r["by_signal"], list)
    assert {row["signal"] for row in r["by_signal"]} >= {
        "event_upward_revision", "event_dividend_hike", "earnings_accel",
        "news_positive", "news_negative",
    }
    for row in r["by_signal"]:
        assert set(row) >= {"signal", "kind", "fired", "n", "status", "display_only"}
        assert row["status"] in ("insufficient", "exploratory", "candidate")
        assert isinstance(row["display_only"], bool)
    # earnings_accel が 1 件発火しているのを上流カウントで観測
    ea = next(row for row in r["by_signal"] if row["signal"] == "earnings_accel")
    assert ea["fired"] == 1
    assert ea["status"] == "insufficient"  # 評価0 → insufficient


def test_empty_data_not_deceptive(tmp_path: Path) -> None:
    """データ空でも壊れず、全 status=insufficient・メトリクスは null（嘘を作らない）。"""
    r = _edge_readout(_engine(tmp_path))
    assert r["verified_decisions"] == 0
    assert all(row["status"] == "insufficient" for row in r["by_signal"])
    assert all(row["hit_rate"] is None for row in r["by_signal"])
    assert r["by_score_bucket"] == []  # 評価済ゼロ


def test_score_bucket_outcomes(tmp_path: Path) -> None:
    """評価済 decision を score の bucket 別に集計（bucket_event_score=単一真実源）。"""
    engine = _engine(tmp_path)
    _add(engine, score=75.0, hit="hit", ret=0.1)   # high
    _add(engine, score=50.0, hit="miss", ret=-0.05)  # mid
    _add(engine, score=None, hit="hit", ret=0.2)   # unscored（version None）
    _add(engine, score=80.0, hit="hit", ret=0.15)  # high

    rows = _score_bucket_outcomes(engine)
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket["high"]["n"] == 2
    assert by_bucket["high"]["hit_rate"] == 1.0
    assert by_bucket["mid"]["n"] == 1
    assert "unscored" in by_bucket  # 採点なしは unscored に分離（捏造混入しない）
    for r in rows:
        assert r["status"] in ("insufficient", "exploratory", "candidate")


def test_unevaluated_excluded_from_buckets(tmp_path: Path) -> None:
    """hit_or_miss 未確定（pending）は bucket 集計に入らない（評価済のみ）。"""
    engine = _engine(tmp_path)
    _add(engine, score=75.0, hit="pending")
    assert _score_bucket_outcomes(engine) == []


def test_non_official_excluded_from_buckets(tmp_path: Path) -> None:
    """finding B: 非official(paper_auto)/非paper は bucket 集計に入らない＝forward と母集団一致。

    gate と同規律で legacy/非official の汚染を排除し、phase_c の by_score_bucket(mature)と
    forward runner の by_score_bucket(interim)を同じ official paper 母集団に揃える。
    """
    engine = _engine(tmp_path)
    _add(engine, score=75.0, hit="hit", ret=0.1, filled_via="paper_auto")  # 非official → 除外
    _add(engine, score=80.0, hit="hit", ret=0.1, broker_mode="live")        # 非paper → 除外
    _add(engine, score=78.0, hit="hit", ret=0.1)                            # official paper → 計上
    rows = _score_bucket_outcomes(engine)
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket["high"]["n"] == 1  # official paper の1件のみ
