"""Tests for discipline/thesis_store.py (X-2A 本番実装).

Covers: ID generation, plan validation (D-23 stop distance), lifecycle
transitions (strict forward order), open/close position, listing, MAGI
linking, and structural SCORE:NONE enforcement (target_price column
does not exist on the SQLModel).
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlmodel import Session, SQLModel, create_engine

from trading_agent.discipline import thesis_store as ts
from trading_agent.models import Thesis, ThesisStatus, ThesisType


# === Fixtures ====================================================================
@pytest.fixture()
def session():
    """In-memory SQLite session for thesis tests."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# === ID generation ==============================================================
def test_new_thesis_id_format() -> None:
    """ID is sortable (YYYYMMDDHHMMSS-<hex6>)."""
    tid = ts.new_thesis_id()
    assert "-" in tid
    ts_part, suffix = tid.split("-")
    assert len(ts_part) == 14
    assert ts_part.isdigit()
    assert len(suffix) == 6
    int(suffix, 16)  # valid hex


def test_new_thesis_id_uniqueness() -> None:
    """Different IDs on rapid generation."""
    ids = {ts.new_thesis_id() for _ in range(20)}
    assert len(ids) == 20  # all unique


# === Validation (D-23) ==========================================================
def test_validate_plan_accepts_10pct_stop() -> None:
    ts.validate_plan(entry_price=2850.0, stop_price=2565.0)  # ~10% — D-23 ideal


def test_validate_plan_accepts_15pct_stop() -> None:
    ts.validate_plan(entry_price=2850.0, stop_price=2422.5)  # ~15% — D-23 upper target


def test_validate_plan_rejects_tight_stop() -> None:
    """3% stop is too tight (below 5% outer bound)."""
    with pytest.raises(ts.ThesisValidationError, match="5-20%"):
        ts.validate_plan(entry_price=2850.0, stop_price=2765.0)


def test_validate_plan_rejects_wide_stop() -> None:
    """25% stop is too wide (above 20% outer bound)."""
    with pytest.raises(ts.ThesisValidationError, match="5-20%"):
        ts.validate_plan(entry_price=2850.0, stop_price=2137.5)


def test_validate_plan_rejects_zero_entry() -> None:
    with pytest.raises(ts.ThesisValidationError, match="positive"):
        ts.validate_plan(entry_price=0.0, stop_price=100.0)


# === Structural SCORE:NONE enforcement ==========================================
def test_thesis_model_has_no_target_price_column() -> None:
    """target_price must NOT exist on Thesis (SCORE:NONE / D-06)."""
    columns = {c.name for c in Thesis.__table__.columns}
    assert "target_price" not in columns
    # entry_price and stop_price ARE allowed (D-23 8数値)
    assert "entry_price" in columns
    assert "stop_price" in columns


# === Create thesis ==============================================================
def test_create_thesis_idea_status(session: Session) -> None:
    """Newly created thesis starts in IDEA, with IDEA entry in lifecycle_log."""
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="EV普及で部品需要増。配当継続性◎",
        source="screening_agent",
    )
    assert t.status == ThesisStatus.IDEA
    assert t.thesis_id  # auto-generated
    assert t.risk_r == ts.DEFAULT_RISK_R_JPY  # D-23 default ¥2,000
    assert len(t.lifecycle_log) == 1
    assert t.lifecycle_log[0]["status"] == "IDEA"
    assert t.lifecycle_log[0]["by"] == "screening_agent"


def test_create_thesis_with_plan_validates(session: Session) -> None:
    """Plan with invalid stop is rejected before insert."""
    with pytest.raises(ts.ThesisValidationError, match="5-20%"):
        ts.create_thesis(
            session,
            ticker="7203",
            thesis_type=ThesisType.DIVIDEND_INCOME,
            thesis_statement="...",
            entry_price=2850.0,
            stop_price=2800.0,  # too tight
        )


# === Lifecycle transitions ======================================================
def test_transition_forward(session: Session) -> None:
    """IDEA → ENTRY_READY → ACTIVE → CLOSED works."""
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
    )
    ts.transition_thesis(session, t.thesis_id, ThesisStatus.ENTRY_READY, by="user")
    t = ts.get_thesis(session, t.thesis_id)
    assert t.status == ThesisStatus.ENTRY_READY
    assert len(t.lifecycle_log) == 2


def test_transition_skip_rejected(session: Session) -> None:
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
    )
    with pytest.raises(ts.ThesisValidationError, match="Invalid lifecycle"):
        ts.transition_thesis(session, t.thesis_id, ThesisStatus.ACTIVE, by="user")


def test_transition_reverse_rejected(session: Session) -> None:
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.GROWTH_MOMENTUM,
        thesis_statement="...",
    )
    ts.transition_thesis(session, t.thesis_id, ThesisStatus.ENTRY_READY, by="user")
    ts.transition_thesis(session, t.thesis_id, ThesisStatus.ACTIVE, by="user")
    with pytest.raises(ts.ThesisValidationError, match="Invalid lifecycle"):
        ts.transition_thesis(session, t.thesis_id, ThesisStatus.ENTRY_READY, by="user")


# === open / close ===============================================================
def test_open_position_auto_advances_idea(session: Session) -> None:
    """open_position from IDEA auto-advances through ENTRY_READY."""
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
        entry_price=2850.0,
        stop_price=2565.0,
    )
    t = ts.open_position(
        session,
        t.thesis_id,
        actual_price=2845.0,
        actual_date=dt.date(2026, 5, 26),
        actual_shares=10.0,
    )
    assert t.status == ThesisStatus.ACTIVE
    assert t.actual_entry_price == 2845.0
    assert t.actual_shares == 10.0
    # Lifecycle audit captures both transitions
    statuses = [e["status"] for e in t.lifecycle_log]
    assert "IDEA" in statuses
    assert "ENTRY_READY" in statuses
    assert "ACTIVE" in statuses


def test_close_position_computes_realized_return(session: Session) -> None:
    """close_position computes return from actual_entry_price."""
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.MEAN_REVERSION,
        thesis_statement="...",
    )
    ts.open_position(
        session,
        t.thesis_id,
        actual_price=1000.0,
        actual_date=dt.date(2026, 5, 1),
        actual_shares=10.0,
    )
    t = ts.close_position(
        session,
        t.thesis_id,
        exit_price=1100.0,
        exit_date=dt.date(2026, 5, 26),
        classification="TRUE_POSITIVE",
        lessons=["mean reversion held"],
    )
    assert t.status == ThesisStatus.CLOSED
    assert t.actual_exit_price == 1100.0
    assert t.realized_return_pct == pytest.approx(10.0)  # +10%
    assert t.postmortem_classification == "TRUE_POSITIVE"
    assert t.postmortem_lessons == ["mean reversion held"]


def test_close_position_rejects_non_active(session: Session) -> None:
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
    )
    with pytest.raises(ts.ThesisValidationError, match="requires ACTIVE"):
        ts.close_position(
            session,
            t.thesis_id,
            exit_price=1100.0,
            exit_date=dt.date(2026, 5, 26),
        )


# === Listing ====================================================================
def test_list_theses_filters_by_status_and_ticker(session: Session) -> None:
    ts.create_thesis(
        session, ticker="7203", thesis_type=ThesisType.DIVIDEND_INCOME, thesis_statement="a"
    )
    t2 = ts.create_thesis(
        session, ticker="QQQ", thesis_type=ThesisType.GROWTH_MOMENTUM, thesis_statement="b"
    )
    ts.transition_thesis(session, t2.thesis_id, ThesisStatus.ENTRY_READY, by="user")

    idea_only = ts.list_theses(session, status=ThesisStatus.IDEA)
    assert {t.ticker for t in idea_only} == {"7203"}

    qqq_only = ts.list_theses(session, ticker="QQQ")
    assert len(qqq_only) == 1
    assert qqq_only[0].status == ThesisStatus.ENTRY_READY


# === MAGI link ==================================================================
def test_link_decision_append_only(session: Session) -> None:
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
    )
    ts.link_decision(session, t.thesis_id, 101)
    ts.link_decision(session, t.thesis_id, 102)
    ts.link_decision(session, t.thesis_id, 101)  # duplicate ignored
    t = ts.get_thesis(session, t.thesis_id)
    assert t.magi_decision_ids == [101, 102]


# === Review queue ===============================================================
def test_list_review_due(session: Session) -> None:
    t = ts.create_thesis(
        session,
        ticker="7203",
        thesis_type=ThesisType.DIVIDEND_INCOME,
        thesis_statement="...",
    )
    ts.open_position(
        session,
        t.thesis_id,
        actual_price=1000.0,
        actual_date=dt.date(2026, 5, 1),
        actual_shares=10.0,
    )
    # Set review due in the past
    t.review_due_date = dt.date(2026, 5, 20)
    session.add(t)
    session.commit()

    due = ts.list_review_due(session, on_date=dt.date(2026, 5, 26))
    assert len(due) == 1
    assert due[0].thesis_id == t.thesis_id

    # No due if checking before the due date
    none_due = ts.list_review_due(session, on_date=dt.date(2026, 5, 15))
    assert none_due == []
