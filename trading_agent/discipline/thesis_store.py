"""Investment thesis lifecycle store (X-2A 本番実装).

Inspired by tradermonty/claude-trading-skills/trader-memory-core (MIT).
Concept-only borrowing per D-21: schema translated to tradesupport's
source_refs/data_asof + SCORE:NONE contract.

Persistence: SQLModel `Thesis` table in tradesupport's main SQLite DB
(consistent with existing 17 tables). See `trading_agent/models/thesis.py`.

Design constraints (D-06 / D-21 / D-23 / SCORE:NONE):
  - **target_price は列ごと存在しない**（構造的に SCORE:NONE を強制）
  - Numbers (entry/stop/risk_r/size) are ALL code-derived
  - thesis_statement may be LLM-written but is human-editable
  - stop_price within 5-20% of entry (D-23 8数値 #7 推奨は10-15%)
  - risk_r defaults to ¥2,000 (D-23 1R for ¥100k principal)
  - Lifecycle is strictly IDEA → ENTRY_READY → ACTIVE → CLOSED (no skip/reverse)
  - lifecycle_log is append-only audit trail
"""
from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Iterable
from datetime import date, datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.thesis import (
    THESIS_STATUS_ORDER,
    Thesis,
    ThesisStatus,
    ThesisType,
)

# === Defaults from D-23 ===
DEFAULT_RISK_R_JPY = 2000.0  # 1R for ¥100k principal


# === ID generation ===
def new_thesis_id() -> str:
    """Time-sortable thesis ID (ULID-ish without external dep).

    Format: YYYYMMDDHHMMSS-<6 hex chars>
    Sortable by creation time, collision-resistant for low rates.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{ts}-{suffix}"


# === Validation ===
class ThesisValidationError(ValueError):
    """Thesis spec validation failed."""


def validate_plan(entry_price: float, stop_price: float) -> None:
    """D-23 stop distance guard (10-15% target, 5-20% outer bound).

    Raises ThesisValidationError if entry/stop are invalid.
    """
    if entry_price <= 0 or stop_price <= 0:
        raise ThesisValidationError(
            f"entry_price and stop_price must be positive, got "
            f"entry={entry_price}, stop={stop_price}"
        )
    distance_pct = abs(entry_price - stop_price) / entry_price
    if distance_pct < 0.05 or distance_pct > 0.20:
        raise ThesisValidationError(
            f"stop distance must be 5-20% of entry (D-23 10-15% target). "
            f"got {distance_pct * 100:.1f}% (entry={entry_price}, stop={stop_price})"
        )


# === Lifecycle ===
def _validate_transition(current: ThesisStatus, to: ThesisStatus) -> None:
    """Strict lifecycle: IDEA → ENTRY_READY → ACTIVE → CLOSED only."""
    if THESIS_STATUS_ORDER[to] != THESIS_STATUS_ORDER[current] + 1:
        raise ThesisValidationError(
            f"Invalid lifecycle transition {current} → {to}. "
            "Lifecycle is strictly IDEA → ENTRY_READY → ACTIVE → CLOSED."
        )


def _append_log(thesis: Thesis, status: ThesisStatus, by: str, reason: str | None) -> None:
    """Append to lifecycle_log (append-only audit)."""
    # SQLModel JSON columns require the attribute to be re-assigned for change tracking
    log = list(thesis.lifecycle_log)
    log.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "status": status.value,
            "by": by,
            "reason": reason,
        }
    )
    thesis.lifecycle_log = log
    thesis.updated_at = datetime.now(timezone.utc)


# === Public API ===
def create_thesis(
    session: Session,
    *,
    ticker: str,
    thesis_type: ThesisType,
    thesis_statement: str,
    entry_price: float | None = None,
    stop_price: float | None = None,
    risk_r: float | None = None,
    size_shares: int = 0,
    source_refs: list[dict] | None = None,
    data_asof: str = "",
    source: str = "manual",
) -> Thesis:
    """Create a new thesis in IDEA status. Returns the persisted Thesis.

    If entry_price and stop_price are provided, validates D-23 stop distance.
    Numbers must be code-derived; this function does NOT call LLM.
    """
    if entry_price is not None and stop_price is not None:
        validate_plan(entry_price, stop_price)

    thesis = Thesis(
        thesis_id=new_thesis_id(),
        ticker=ticker,
        thesis_type=thesis_type,
        thesis_statement=thesis_statement,
        status=ThesisStatus.IDEA,
        entry_price=entry_price,
        stop_price=stop_price,
        risk_r=risk_r if risk_r is not None else DEFAULT_RISK_R_JPY,
        size_shares=size_shares,
        source_refs=list(source_refs or []),
        data_asof=data_asof,
    )
    _append_log(thesis, ThesisStatus.IDEA, by=source, reason=None)
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return thesis


def transition_thesis(
    session: Session,
    thesis_id: str,
    to: ThesisStatus,
    by: str,
    reason: str | None = None,
) -> Thesis:
    """Move a thesis forward in lifecycle. Strict order, append-only audit.

    Raises ThesisValidationError on invalid transition.
    """
    thesis = session.get(Thesis, thesis_id)
    if thesis is None:
        raise ThesisValidationError(f"thesis_id not found: {thesis_id}")
    _validate_transition(thesis.status, to)
    thesis.status = to
    _append_log(thesis, to, by=by, reason=reason)
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return thesis


def open_position(
    session: Session,
    thesis_id: str,
    *,
    actual_price: float,
    actual_date: date,
    actual_shares: float,
    by: str = "paper_exec",
) -> Thesis:
    """Transition IDEA/ENTRY_READY → ACTIVE with fill details.

    If thesis is in IDEA, automatically transitions through ENTRY_READY first.
    """
    thesis = session.get(Thesis, thesis_id)
    if thesis is None:
        raise ThesisValidationError(f"thesis_id not found: {thesis_id}")
    if thesis.status == ThesisStatus.IDEA:
        thesis.status = ThesisStatus.ENTRY_READY
        _append_log(thesis, ThesisStatus.ENTRY_READY, by=by, reason="auto via open_position")
    if thesis.status != ThesisStatus.ENTRY_READY:
        raise ThesisValidationError(
            f"open_position requires ENTRY_READY (got {thesis.status})"
        )
    thesis.actual_entry_price = actual_price
    thesis.actual_entry_date = actual_date
    thesis.actual_shares = actual_shares
    thesis.status = ThesisStatus.ACTIVE
    _append_log(thesis, ThesisStatus.ACTIVE, by=by, reason=None)
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return thesis


def close_position(
    session: Session,
    thesis_id: str,
    *,
    exit_price: float,
    exit_date: date,
    classification: str | None = None,
    lessons: Iterable[str] | None = None,
    by: str = "paper_exec",
    reason: str | None = None,
) -> Thesis:
    """Transition ACTIVE → CLOSED with exit details and postmortem fields.

    `classification` is one of TRUE_POSITIVE/FALSE_POSITIVE/MISSED_OPPORTUNITY/REGIME_MISMATCH
    (set by signal-postmortem翻案 = X-2D, optional in X-2A).
    Realized return is computed from actual_entry_price.
    """
    thesis = session.get(Thesis, thesis_id)
    if thesis is None:
        raise ThesisValidationError(f"thesis_id not found: {thesis_id}")
    if thesis.status != ThesisStatus.ACTIVE:
        raise ThesisValidationError(
            f"close_position requires ACTIVE (got {thesis.status})"
        )
    thesis.actual_exit_price = exit_price
    thesis.actual_exit_date = exit_date
    if thesis.actual_entry_price and thesis.actual_entry_price > 0:
        thesis.realized_return_pct = (
            (exit_price - thesis.actual_entry_price) / thesis.actual_entry_price * 100.0
        )
    if classification is not None:
        thesis.postmortem_classification = classification
    if lessons is not None:
        thesis.postmortem_lessons = list(lessons)
    thesis.status = ThesisStatus.CLOSED
    _append_log(thesis, ThesisStatus.CLOSED, by=by, reason=reason)
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    return thesis


def get_thesis(session: Session, thesis_id: str) -> Thesis | None:
    """Fetch by ID. Returns None if not found."""
    return session.get(Thesis, thesis_id)


def list_theses(
    session: Session,
    *,
    status: ThesisStatus | None = None,
    ticker: str | None = None,
    limit: int = 100,
) -> list[Thesis]:
    """List theses, newest first."""
    stmt = select(Thesis)
    if status is not None:
        stmt = stmt.where(col(Thesis.status) == status)
    if ticker is not None:
        stmt = stmt.where(col(Thesis.ticker) == ticker)
    stmt = stmt.order_by(col(Thesis.created_at).desc()).limit(limit)
    return list(session.exec(stmt).all())


def list_review_due(session: Session, on_date: date | None = None) -> list[Thesis]:
    """List ACTIVE theses whose review_due_date is ≤ on_date (default today)."""
    on_date = on_date or datetime.now(timezone.utc).date()
    stmt = (
        select(Thesis)
        .where(col(Thesis.status) == ThesisStatus.ACTIVE)
        .where(col(Thesis.review_due_date).is_not(None))
        .where(col(Thesis.review_due_date) <= on_date)
        .order_by(col(Thesis.review_due_date).asc())
    )
    return list(session.exec(stmt).all())


def link_decision(session: Session, thesis_id: str, decision_id: int) -> Thesis:
    """Link a MAGI decision to this thesis (append-only)."""
    thesis = session.get(Thesis, thesis_id)
    if thesis is None:
        raise ThesisValidationError(f"thesis_id not found: {thesis_id}")
    ids = list(thesis.magi_decision_ids)
    if decision_id not in ids:
        ids.append(decision_id)
        thesis.magi_decision_ids = ids
        thesis.updated_at = datetime.now(timezone.utc)
        session.add(thesis)
        session.commit()
        session.refresh(thesis)
    return thesis


__all__ = [
    "DEFAULT_RISK_R_JPY",
    "ThesisValidationError",
    "new_thesis_id",
    "validate_plan",
    "create_thesis",
    "transition_thesis",
    "open_position",
    "close_position",
    "get_thesis",
    "list_theses",
    "list_review_due",
    "link_decision",
]
