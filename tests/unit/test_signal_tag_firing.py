"""signal_tag 発火率の可視化（phase_c_status._signal_tag_firing）の単体テスト。

欺瞞防止: 「タグがそもそも何件立っているか（発火率）」を verified decision 母数で出す。
0% が続く＝辞書/開示接続が機能していない兆候を silently empty にしない。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from scripts.phase_c_status import _signal_tag_firing
from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "firing.sqlite")
    create_all(eng)
    return eng


def _add(engine, *, tags, verified=True, event_diag=None):
    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=dt.date(2026, 6, 6), ticker="3697", action="buy", status="awaiting")
        d.entry_signal_tags = tags
        if event_diag is not None:
            d.signal_tag_sources = {"_event_diag": event_diag}
        if verified:
            d.verified_at = utcnow()
        s.add(d)
        s.commit()


def test_by_tag_breakdown(tmp_path: Path) -> None:
    """tag 種別ごとの発火数が一括でなく分解集計される（M1 観測 hardening）。"""
    engine = _engine(tmp_path)
    _add(engine, tags=["event_upward_revision"])
    _add(engine, tags=["event_dividend_hike", "earnings_accel"])
    _add(engine, tags=["news_positive"])
    f = _signal_tag_firing(engine)
    bt = f["by_tag"]
    assert bt["event_upward_revision"] == 1
    assert bt["event_dividend_hike"] == 1
    assert bt["earnings_accel"] == 1
    assert bt["news_positive"] == 1
    assert bt["event_downward_revision"] == 0
    assert f["structured"]["upward_revision"] == 1


def test_no_fire_reasons_aggregated(tmp_path: Path) -> None:
    """_event_diag の no-fire 理由が forecast/dividend 別に集計される（なぜ発火しないか可視化）。"""
    engine = _engine(tmp_path)
    _add(engine, tags=[], event_diag={"forecast": "fin_not_fetched", "dividend": "fin_not_fetched"})
    _add(engine, tags=[], event_diag={"forecast": "no_change", "dividend": "missing_shares"})
    _add(engine, tags=[], event_diag={"forecast": "fin_not_fetched", "dividend": "split_suspected"})
    _add(engine, tags=[])  # diag 無し（旧 decision）

    nfr = _signal_tag_firing(engine)["no_fire_reasons"]
    assert nfr["diag_recorded"] == 3  # diag を持つ verified のみ
    assert nfr["forecast"]["fin_not_fetched"] == 2  # rate-limit を理由別に観測
    assert nfr["forecast"]["no_change"] == 1
    assert nfr["dividend"]["missing_shares"] == 1
    assert nfr["dividend"]["split_suspected"] == 1


def test_counts_news_and_structured_firing(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _add(engine, tags=["news_positive"])
    _add(engine, tags=["news_negative", "earnings_accel"])
    _add(engine, tags=["earnings_accel"])
    _add(engine, tags=[])  # 無タグ

    f = _signal_tag_firing(engine)
    assert f["verified_decisions"] == 4
    assert f["news"]["positive"] == 1
    assert f["news"]["negative"] == 1
    assert f["news"]["no_tag"] == 2  # news系タグが無い2件
    assert f["news"]["fire_rate_pct"] == 50.0  # 2/4
    assert f["structured"]["earnings_accel"] == 2
    assert f["structured"]["fire_rate_pct"] == 50.0  # 2/4


def test_unverified_decisions_excluded(tmp_path: Path) -> None:
    """verified_at が無い decision は母数に含めない（verify を経ていない＝タグ付与対象外）。"""
    engine = _engine(tmp_path)
    _add(engine, tags=["news_positive"], verified=True)
    _add(engine, tags=["news_positive"], verified=False)
    f = _signal_tag_firing(engine)
    assert f["verified_decisions"] == 1
    assert f["news"]["positive"] == 1


def test_empty_db_does_not_divide_by_zero(tmp_path: Path) -> None:
    f = _signal_tag_firing(_engine(tmp_path))
    assert f["verified_decisions"] == 0
    assert f["news"]["fire_rate_pct"] == 0.0
    assert f["structured"]["fire_rate_pct"] == 0.0


def test_zero_firing_is_visible(tmp_path: Path) -> None:
    """タグが1件も立たない場合、no_tag=母数・発火0% が明示される（silently empty 防止）。"""
    engine = _engine(tmp_path)
    _add(engine, tags=[])
    _add(engine, tags=[])
    f = _signal_tag_firing(engine)
    assert f["news"]["no_tag"] == 2
    assert f["news"]["fire_rate_pct"] == 0.0
