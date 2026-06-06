"""Forward 診断ハーネス（codex #4）の単体テスト。series_fetcher 注入で決定論検証。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.reporting.forward_diagnosis import compute_forward_diagnosis


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "fwd.sqlite")
    create_all(eng)
    return eng


def _seed_filled(
    engine, ticker, *, entry_price, tags, exposure=None,
    entry_date=dt.date(2026, 1, 1), broker="paper", via="ds_dispatch",
    score=None, score_version=None,
):
    with Session(engine, expire_on_commit=False) as s:
        d = Decision(date=entry_date, ticker=ticker, action="buy", status="ordered")
        d.entry_date = entry_date
        d.entry_price = entry_price
        d.filled_via = via
        d.entry_broker_mode = broker
        d.entry_signal_tags = tags
        d.entry_exposure_recommendation = exposure
        d.fundamental_event_score = score
        d.event_score_version = score_version
        s.add(d)
        s.commit()


def test_excess_return_vs_topix(tmp_path: Path) -> None:
    """銘柄 +10% / TOPIX +2% → 超過 +8%。tag/exposure/overall に集計。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["sector_rs"], exposure="REDUCE_ONLY")

    def fetcher(_tickers, _start):
        return {"AAA": [100.0, 105.0, 110.0], "1306.T": [200.0, 202.0, 204.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,))
    o = res["overall"]["2"]
    assert o["n"] == 1
    assert o["hit_rate"] == 1.0  # 超過 > 0
    assert abs(o["avg_excess"] - 0.08) < 1e-6  # 0.10 - 0.02
    assert res["by_tag"]["sector_rs"]["2"]["n"] == 1
    assert abs(res["by_tag"]["sector_rs"]["2"]["avg_excess"] - 0.08) < 1e-6
    assert res["by_exposure"]["REDUCE_ONLY"]["2"]["n"] == 1


def test_by_score_bucket_slice(tmp_path: Path) -> None:
    """(d): score bucket 別に horizon 超過 + current_mtm を集計。閾値は (c) 単一真実源。

    event_score_v1 cut: low<=40 / high>=60。score None は unscored に分離（捏造しない）。
    """
    eng = _engine(tmp_path)
    v = "event_score_v1"
    _seed_filled(eng, "HI", entry_price=100.0, tags=[], score=75.0, score_version=v)   # high
    _seed_filled(eng, "LO", entry_price=100.0, tags=[], score=30.0, score_version=v)   # low
    _seed_filled(eng, "NS", entry_price=100.0, tags=[], score=None, score_version=None)  # unscored

    def fetcher(_tickers, _start):
        return {
            "HI": [100.0, 110.0, 120.0],
            "LO": [100.0, 90.0, 80.0],
            "NS": [100.0, 105.0, 110.0],
            "1306.T": [200.0, 202.0, 204.0],
        }

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,))
    bsb = res["by_score_bucket"]
    # 各バケットに 1 件ずつ・mid は 0
    assert bsb["high"]["horizons"]["2"]["n"] == 1
    assert bsb["low"]["horizons"]["2"]["n"] == 1
    assert bsb["unscored"]["horizons"]["2"]["n"] == 1
    assert bsb["mid"]["horizons"]["2"]["n"] == 0
    # high は超過プラス（+20% vs +2%）・low はマイナス
    assert bsb["high"]["horizons"]["2"]["avg_excess"] > 0
    assert bsb["low"]["horizons"]["2"]["avg_excess"] < 0
    # current_mtm も bucket 別に出る
    assert bsb["high"]["current_mtm"]["n"] == 1
    # meta: version_dist と cutpoint_versions
    meta = res["score_bucket_meta"]
    assert meta["version_dist"].get(v) == 2
    assert meta["version_dist"].get("none") == 1
    assert "cutpoint_v1" in meta["cutpoint_versions"]
    # 既存キーは不変（純追加）
    assert "overall" in res and "by_tag" in res and "current_mtm" in res


def test_by_score_bucket_version_mismatch_to_unscored(tmp_path: Path) -> None:
    """未知 score_version は 1 閾値で誤バケットせず unscored に落とす（p-hacking 防止）。"""
    eng = _engine(tmp_path)
    # score は high 域だが version が registry 外 → unscored
    _seed_filled(
        eng, "XX", entry_price=100.0, tags=[], score=80.0, score_version="event_score_v999"
    )

    def fetcher(_tickers, _start):
        return {"XX": [100.0, 110.0, 120.0], "1306.T": [200.0, 202.0, 204.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,))
    bsb = res["by_score_bucket"]
    assert bsb["unscored"]["horizons"]["2"]["n"] == 1
    assert bsb["high"]["horizons"]["2"]["n"] == 0  # 誤バケットしない
    assert res["score_bucket_meta"]["version_dist"].get("event_score_v999") == 1


def test_unreached_horizon_skipped(tmp_path: Path) -> None:
    """series が horizon に届かない（未到達）→ 集計に乗せない（推測しない）。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["sector_rs"])

    def fetcher(_tickers, _start):
        # 系列が 2 本だけ → horizon 5 には届かない
        return {"AAA": [100.0, 101.0], "1306.T": [200.0, 201.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(5,))
    assert res["overall"]["5"]["n"] == 0
    assert res["overall"]["5"]["avg_excess"] is None
    assert res["by_tag"] == {}  # どの horizon も未到達 → tag 集計も空


def test_only_official_paper_included(tmp_path: Path) -> None:
    """legacy(broker_mode None) / live は対象外。official paper のみ。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["sector_rs"], broker="paper")
    _seed_filled(eng, "BBB", entry_price=100.0, tags=["sector_rs"], broker="live")
    _seed_filled(eng, "CCC", entry_price=100.0, tags=["sector_rs"], broker=None, via=None)

    def fetcher(tickers, _start):
        return {t: [100.0, 110.0, 120.0] for t in tickers} | {"1306.T": [200.0, 202.0, 204.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,), broker_mode="paper")
    assert res["decisions_n"] == 1  # AAA のみ
    assert res["overall"]["2"]["n"] == 1


def test_current_mtm_for_young_position(tmp_path: Path) -> None:
    """codex P2: 固定 horizon 未到達でも entry→今日の暫定超過(current_mtm)は出る。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["sector_rs"])

    def fetcher(_tickers, _start):
        # 3 本(entry + 2日) → horizon 5 未到達だが current は出せる
        return {"AAA": [100.0, 105.0, 110.0], "1306.T": [200.0, 202.0, 204.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(5,))
    assert res["overall"]["5"]["n"] == 0  # 固定 horizon は未到達
    cm = res["current_mtm"]
    assert cm["n"] == 1
    assert abs(cm["avg_excess"] - 0.08) < 1e-6  # 0.10 - 0.02
    assert cm["avg_bars_held"] == 2.0


def test_tag_vs_control_net_excess(tmp_path: Path) -> None:
    """codex P2/#3: tag with/without の超過差(forward 正味エッジ)を horizon 別に出す。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["sector_rs"])  # tag あり
    _seed_filled(eng, "BBB", entry_price=100.0, tags=[])             # tag なし(control)

    def fetcher(_tickers, _start):
        return {
            "AAA": [100.0, 100.0, 110.0],   # +10% → 超過 +8%
            "BBB": [100.0, 100.0, 95.0],    # -5%  → 超過 -7%
            "1306.T": [200.0, 200.0, 204.0],  # +2%
        }

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,))
    c = res["tag_vs_control"]["sector_rs"]["2"]
    assert c["with_n"] == 1 and c["without_n"] == 1
    assert abs(c["with_avg_excess"] - 0.08) < 1e-6
    assert abs(c["without_avg_excess"] - (-0.07)) < 1e-6
    assert abs(c["net_avg_excess"] - 0.15) < 1e-6  # 0.08 - (-0.07)


def test_negative_excess_counts_as_loss(tmp_path: Path) -> None:
    """銘柄が TOPIX に負ければ超過 < 0 で勝率に入らない。"""
    eng = _engine(tmp_path)
    _seed_filled(eng, "AAA", entry_price=100.0, tags=["earnings_accel"])

    def fetcher(_tickers, _start):
        return {"AAA": [100.0, 98.0, 95.0], "1306.T": [200.0, 204.0, 210.0]}

    res = compute_forward_diagnosis(eng, series_fetcher=fetcher, horizons=(2,))
    o = res["overall"]["2"]
    assert o["n"] == 1
    assert o["hit_rate"] == 0.0  # 銘柄 -5% vs TOPIX +5% → 超過 -10% < 0
    assert o["avg_excess"] < 0
