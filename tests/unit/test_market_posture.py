"""Track A: 市場 posture 計算（compute_market_posture）の単体テスト。

returns_fetcher を注入してネット非依存・決定論で検証する。breadth/uptrend の算出と
exposure_coach への配線、macro_adjustment の整合を確認する（record-only・売買は変えない）。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.discipline.market_posture import (
    compute_market_posture,
    macro_adjustment_for,
)
from trading_agent.models.universe import Universe


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "posture.sqlite")
    create_all(eng)
    return eng


def _seed_jp_universe(engine, n: int = 10) -> None:
    with Session(engine) as s:
        for i in range(n):
            s.add(
                Universe(
                    ticker=f"T{i}", name=f"name{i}", market="JP", sector="X",
                    market_cap=1e10, market_cap_jpy=1e10, avg_volume_30d=1e6, is_active=True,
                )
            )
        # US 銘柄（breadth サンプルから除外される）
        s.add(
            Universe(
                ticker="US1", name="us", market="US", sector="Y",
                market_cap=1e10, market_cap_jpy=1e10, avg_volume_30d=1e6, is_active=True,
            )
        )
        s.commit()


def test_breadth_and_uptrend_from_returns(tmp_path: Path) -> None:
    """30日リターンから breadth(上昇比率) と uptrend(+5%超比率) を算出。"""
    _eng = _engine(tmp_path)
    _seed_jp_universe(_eng, n=10)
    # 10銘柄: 3つが +10%(uptrend), 4つが +1%(上昇のみ), 3つが -2%(下落)
    returns = {f"T{i}": (10.0 if i < 3 else (1.0 if i < 7 else -2.0)) for i in range(10)}

    decision, meta = compute_market_posture(
        _eng, returns_fetcher=lambda _t: returns, regime="bull"
    )
    assert meta["breadth_score"] == 70.0   # 7/10 が上昇
    assert meta["uptrend_score"] == 30.0   # 3/10 が +5%超
    assert meta["regime"] == "bull"
    assert meta["sample_n"] == 10
    # recommendation は妥当な3値のいずれか。macro_adjustment は recommendation と整合。
    assert decision.recommendation in ("NEW_ENTRY_ALLOWED", "REDUCE_ONLY", "CASH_PRIORITY")
    assert meta["macro_adjustment"] == macro_adjustment_for(decision.recommendation)


def test_breadth_varies_recommendation(tmp_path: Path) -> None:
    """breadth が高いほど exposure が緩む（regime 単独の保守バイアスを breadth が動かす）。"""
    _eng = _engine(tmp_path)
    _seed_jp_universe(_eng, n=10)
    weak = {f"T{i}": -3.0 for i in range(10)}    # 全下落 → breadth 0
    strong = {f"T{i}": 12.0 for i in range(10)}  # 全上昇 +12% → breadth 100/uptrend 100

    d_weak, m_weak = compute_market_posture(_eng, returns_fetcher=lambda _t: weak, regime="bear")
    d_strong, m_strong = compute_market_posture(_eng, returns_fetcher=lambda _t: strong, regime="bull")
    assert m_weak["breadth_score"] == 0.0
    assert m_strong["breadth_score"] == 100.0
    # weak の ceiling <= strong の ceiling（breadth が posture を動かす）
    assert d_weak.ceiling_pct <= d_strong.ceiling_pct


def test_breadth_fetch_failure_degrades_gracefully(tmp_path: Path) -> None:
    """breadth fetch が空でも破綻しない（breadth/uptrend=None・regime のみで判定）。"""
    _eng = _engine(tmp_path)
    _seed_jp_universe(_eng, n=5)
    decision, meta = compute_market_posture(
        _eng, returns_fetcher=lambda _t: {}, regime="sideways"
    )
    assert meta["breadth_score"] is None
    assert meta["uptrend_score"] is None
    assert decision.recommendation in ("NEW_ENTRY_ALLOWED", "REDUCE_ONLY", "CASH_PRIORITY")


def test_macro_adjustment_mapping() -> None:
    assert macro_adjustment_for("NEW_ENTRY_ALLOWED") == 0.0
    assert macro_adjustment_for("REDUCE_ONLY") == -0.3
    assert macro_adjustment_for("CASH_PRIORITY") == -0.6
    assert macro_adjustment_for(None) is None
