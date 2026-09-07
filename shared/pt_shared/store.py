"""Query helpers over the shared datastore.

These are the read/write primitives used by the red team (injection
instances), the blue team (budget + concurrency guardrails) and the dashboard
(aggregate metrics).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    DevinSession,
    Event,
    Injection,
    InjectionStatus,
    PRState,
    SessionStatus,
)

ACTIVE_SESSION_STATUSES = (
    SessionStatus.queued,
    SessionStatus.running,
    SessionStatus.blocked,
)
# Sessions that actually occupy a concurrency slot and reserve budget. Queued
# sessions are waiting for a slot, so counting them would wedge the queue.
IN_FLIGHT_SESSION_STATUSES = (SessionStatus.running, SessionStatus.blocked)
TERMINAL_SESSION_STATUSES = (SessionStatus.completed, SessionStatus.failed)


# --- injections -----------------------------------------------------------


def list_injections_for_vuln(db: Session, vuln_id: str) -> list[Injection]:
    return list(
        db.scalars(
            select(Injection)
            .where(Injection.vuln_id == vuln_id)
            .order_by(Injection.instance)
        ).all()
    )


def next_instance(db: Session, vuln_id: str) -> int:
    current = db.scalar(
        select(func.max(Injection.instance)).where(
            Injection.vuln_id == vuln_id
        )
    )
    return int(current or 0) + 1


def list_injections(db: Session) -> list[Injection]:
    return list(
        db.scalars(select(Injection).order_by(Injection.created_at.desc())).all()
    )


def get_injection_by_issue(db: Session, issue_number: int) -> Injection | None:
    return db.scalar(
        select(Injection).where(Injection.issue_number == issue_number)
    )


# --- sessions -------------------------------------------------------------


def get_session_by_key(db: Session, idempotency_key: str) -> DevinSession | None:
    return db.scalar(
        select(DevinSession).where(
            DevinSession.idempotency_key == idempotency_key
        )
    )


def list_sessions(db: Session) -> list[DevinSession]:
    return list(
        db.scalars(
            select(DevinSession).order_by(DevinSession.created_at.desc())
        ).all()
    )


def active_session_count(db: Session) -> int:
    """Sessions occupying a concurrency slot right now (queued ones excluded)."""

    return int(
        db.scalar(
            select(func.count(DevinSession.id)).where(
                DevinSession.status.in_(IN_FLIGHT_SESSION_STATUSES)
            )
        )
        or 0
    )


def queued_session_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(DevinSession.id)).where(
                DevinSession.status == SessionStatus.queued
            )
        )
        or 0
    )


def total_acu_spend(db: Session) -> float:
    """Accumulated ACU spend across every session ever created.

    Sessions that are still running are counted at ``max(consumed, 0)`` plus
    nothing extra; the caller reserves headroom separately when admitting a
    new session so an in-flight session cannot silently blow the ceiling.
    """

    return float(db.scalar(select(func.sum(DevinSession.acu_consumed))) or 0.0)


def committed_acu(db: Session) -> float:
    """Spend already incurred plus the unspent cap of in-flight sessions."""

    total = 0.0
    for row in db.scalars(select(DevinSession)).all():
        if row.status in IN_FLIGHT_SESSION_STATUSES:
            total += max(row.acu_consumed, row.acu_limit)
        else:
            total += row.acu_consumed
    return float(total)


# --- events ---------------------------------------------------------------


def recent_events(db: Session, limit: int = 100) -> list[Event]:
    return list(
        db.scalars(select(Event).order_by(Event.ts.desc()).limit(limit)).all()
    )


# --- aggregates -----------------------------------------------------------


def metrics(db: Session, budget_ceiling: float) -> dict[str, Any]:
    """Dashboard summary metrics."""

    injections = list_injections(db)
    sessions = list_sessions(db)

    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for inj in injections:
        by_category[inj.category] = by_category.get(inj.category, 0) + 1
        by_severity[inj.severity.value] = by_severity.get(inj.severity.value, 0) + 1

    issues_open = sum(
        1 for i in injections if i.status == InjectionStatus.issue_opened
    )
    remediated = sum(
        1 for i in injections if i.status == InjectionStatus.remediated
    )
    active = [s for s in sessions if s.status in IN_FLIGHT_SESSION_STATUSES]
    queued = [s for s in sessions if s.status == SessionStatus.queued]
    completed = [s for s in sessions if s.status == SessionStatus.completed]
    failed = [s for s in sessions if s.status == SessionStatus.failed]
    refused = [s for s in sessions if s.status == SessionStatus.refused_budget]
    prs = [s.pr_url for s in sessions if s.pr_url]
    prs_merged = sum(1 for s in sessions if s.pr_state == PRState.merged)
    prs_awaiting_review = sum(
        1 for s in sessions if s.pr_state == PRState.open
    )
    prs_closed = sum(1 for s in sessions if s.pr_state == PRState.closed)
    stages: dict[str, int] = {}
    for session in sessions:
        stage = session.stage.value
        stages[stage] = stages.get(stage, 0) + 1

    spend = total_acu_spend(db)
    finished = len(completed) + len(failed)
    success_rate = (len(completed) / finished * 100.0) if finished else 0.0

    return {
        "injections_total": len(injections),
        "issues_created": sum(1 for i in injections if i.issue_number),
        "issues_open": issues_open,
        "remediated": remediated,
        "injections_by_category": by_category,
        "injections_by_severity": by_severity,
        # Budget-refused records never became Devin sessions.
        "sessions_total": len(sessions) - len(refused),
        "sessions_active": len(active),
        "sessions_queued": len(queued),
        "sessions_completed": len(completed),
        "sessions_failed": len(failed),
        "sessions_refused_budget": len(refused),
        "success_rate_pct": round(success_rate, 1),
        "prs_opened": len(prs),
        "prs_merged": prs_merged,
        "prs_awaiting_review": prs_awaiting_review,
        "prs_closed": prs_closed,
        "pr_urls": prs,
        "stages": stages,
        "acu_spend": round(spend, 2),
        "acu_committed": round(committed_acu(db), 2),
        "acu_ceiling": budget_ceiling,
        "acu_remaining": round(max(budget_ceiling - spend, 0.0), 2),
        "budget_used_pct": round(
            (spend / budget_ceiling * 100.0) if budget_ceiling else 0.0, 1
        ),
    }
