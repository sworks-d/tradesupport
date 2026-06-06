"""上場廃止 closed_price 入力 → 元 buy Decision 還元（record_delisted_exit）の単体テスト。

survivorship-bias 是正: 上場廃止の損失が track_record に乗る（漏れない）ことを検証。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.paper_exec import record_delisted_exit


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "delist.sqlite")
    create_all(eng)
    return eng


def _seed_delisted(eng, *, buy_price=1000.0, qty=100, closed_price=None) -> tuple[int, int]:
    """closed_reason='delisted'・closed_price=None の Portfolio + 紐付く buy Decision を作る。"""
    with Session(eng, expire_on_commit=False) as s:
        s.add(Universe(
            ticker="X", name="X", market="JP", sector="Ind",
            market_cap=1e10, market_cap_jpy=1e10, avg_volume_30d=1e6, is_active=False,
        ))
        d = Decision(
            date=dt.date(2026, 5, 1), ticker="X", action="buy", status="filled",
            entry_price=buy_price, stop_pct=0.10, expected_return=0.20,
            target_period_days=60, evaluation_date=dt.date(2026, 7, 1), hit_or_miss="pending",
        )
        s.add(d)
        s.commit()
        s.refresh(d)
        p = Portfolio(
            ticker="X", personality="REI", buy_date=dt.date(2026, 5, 1), buy_price=buy_price,
            qty=qty, currency="JPY", strategy_category="中期", target_period_days=60,
            target_pct=0.20, stop_loss_pct=0.10, target_date=dt.date(2026, 7, 1), thesis="t",
            status="closed", closed_reason="delisted", closed_price=closed_price,
            broker_mode="paper", decision_id=d.id,
        )
        s.add(p)
        s.commit()
        return int(p.id), int(d.id)


def test_worthless_delisting_records_miss(tmp_path: Path) -> None:
    """無価値で上場廃止（closed_price=0）→ buy Decision が -100% の miss を取得（損失が乗る）。"""
    eng = _engine(tmp_path)
    pid, did = _seed_delisted(eng, buy_price=1000.0)
    res = record_delisted_exit(eng, portfolio_id=pid, closed_price=0.0)
    assert res["ok"] is True
    assert res["hit_or_miss"] == "miss"
    assert abs(res["actual_return"] - (-1.0)) < 1e-9  # (0-1000)/1000
    with Session(eng) as s:
        buy = s.get(Decision, did)
        assert buy.hit_or_miss == "miss" and buy.actual_return == -1.0
        assert buy.evaluated_at is not None
        p = s.get(Portfolio, pid)
        assert p.closed_price == 0.0  # 入力された price が反映


def test_exchange_ratio_price_records_partial_loss(tmp_path: Path) -> None:
    """株式交換等で closed_price>0（部分損失）→ その実価格で還元。"""
    eng = _engine(tmp_path)
    pid, _ = _seed_delisted(eng, buy_price=1000.0)
    res = record_delisted_exit(eng, portfolio_id=pid, closed_price=850.0)
    assert res["ok"] is True
    assert abs(res["actual_return"] - (-0.15)) < 1e-9  # (850-1000)/1000
    assert res["hit_or_miss"] == "miss"  # -15% ≤ -stop(10%)


def test_none_or_negative_price_rejected(tmp_path: Path) -> None:
    """closed_price 未入力(None)/負値は還元しない（H10 推測しない）。"""
    eng = _engine(tmp_path)
    pid, did = _seed_delisted(eng)
    assert record_delisted_exit(eng, portfolio_id=pid, closed_price=None).get("error") == "invalid_price"
    assert record_delisted_exit(eng, portfolio_id=pid, closed_price=-5.0).get("error") == "invalid_price"
    with Session(eng) as s:
        assert s.get(Decision, did).hit_or_miss == "pending"  # 還元されていない


def test_reentry_same_price_noop(tmp_path: Path) -> None:
    """再入力・同一価格 → already_recorded で no-op（buy も Portfolio.closed_price も不変）。"""
    eng = _engine(tmp_path)
    pid, did = _seed_delisted(eng, buy_price=1000.0)
    record_delisted_exit(eng, portfolio_id=pid, closed_price=0.0)
    with Session(eng) as s:
        first = s.get(Decision, did).actual_return
    res2 = record_delisted_exit(eng, portfolio_id=pid, closed_price=0.0)
    assert res2.get("already_recorded") is True
    with Session(eng) as s:
        assert s.get(Decision, did).actual_return == first
        assert s.get(Portfolio, pid).closed_price == 0.0


def test_reentry_price_mismatch_rejected(tmp_path: Path) -> None:
    """再入力・異価格 → already_recorded_price_mismatch エラー。Portfolio.closed_price も buy も不変（codex P1 desync 防止）。"""
    eng = _engine(tmp_path)
    pid, did = _seed_delisted(eng, buy_price=1000.0)
    record_delisted_exit(eng, portfolio_id=pid, closed_price=0.0)
    with Session(eng) as s:
        first = s.get(Decision, did).actual_return
    res2 = record_delisted_exit(eng, portfolio_id=pid, closed_price=500.0)
    assert res2.get("error") == "already_recorded_price_mismatch"
    with Session(eng) as s:
        assert s.get(Portfolio, pid).closed_price == 0.0   # 上書きされない
        assert s.get(Decision, did).actual_return == first  # buy も不変（desync なし）


def test_non_delisted_close_rejected(tmp_path: Path) -> None:
    """closed_reason が delisted でない Portfolio は対象外（誤適用防止）。"""
    eng = _engine(tmp_path)
    pid, _ = _seed_delisted(eng)
    with Session(eng, expire_on_commit=False) as s:
        p = s.get(Portfolio, pid)
        p.closed_reason = "stop_loss"
        s.add(p)
        s.commit()
    assert record_delisted_exit(eng, portfolio_id=pid, closed_price=0.0).get("error") == "not_a_delisted_close"
