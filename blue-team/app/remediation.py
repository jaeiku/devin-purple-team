"""Blue team remediation: turn a red-team GitHub issue into a Devin session."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pt_shared.config import Settings
from pt_shared.github import GitHubClient, GitHubError
from pt_shared.logging_utils import log_event
from pt_shared.models import DevinSession, SessionStatus
from pt_shared.store import get_injection_by_issue, get_session_by_key
from sqlalchemy.orm import Session

from . import guardrails
from .devin_client import DevinAPIError, DevinClient
from .schemas import IssueEvent

SERVICE = "blue-team"

QUALIFYING_LABELS = {"red-team", "security"}


def qualifies(labels: list[str]) -> bool:
    """Only act on issues the red team raised (or any security issue)."""

    return bool(QUALIFYING_LABELS & {label.lower() for label in labels})


def build_prompt(event: IssueEvent, settings: Settings) -> str:
    """Deterministic remediation prompt handed to Devin.

    Deterministic matters twice over: it keeps `idempotent=true` on the Devin
    API meaningful, and it keeps demo runs reproducible.
    """

    repo = event.repo or settings.superset_fork_repo
    location = (
        f"The vulnerable code is in `{event.file_path}` on branch "
        f"`{event.branch}`.\n"
        if event.file_path
        else ""
    )
    return f"""You are remediating a security vulnerability in the repository `{repo}`.

GitHub issue: #{event.issue_number} ({event.issue_url})
Vulnerability category: {event.category or "unknown"}
Severity: {event.severity or "unknown"}
Title: {event.title}

{location}Read the GitHub issue at {event.issue_url} in full. It contains the \
description, the exact file that is vulnerable, and remediation guidance.

Your task:
1. Fix the vulnerability described in issue #{event.issue_number}. Apply the \
remediation guidance in the issue; keep the module's public function \
signatures unchanged so existing callers keep working.
2. Do not modify any file unrelated to this vulnerability, and do not touch \
upstream Apache Superset internals.
3. Open a pull request against the repository's default branch whose \
description explains the vulnerability, the fix, and includes the line \
"Closes #{event.issue_number}" so the issue is closed on merge.

Constraints:
- Work only in `{repo}`.
- Keep the change minimal and focused; this is a targeted security fix.
- If you cannot fix it, stop and explain why rather than making speculative \
changes.
"""


def _enrich_from_injection(db: Session, event: IssueEvent) -> IssueEvent:
    injection = get_injection_by_issue(db, event.issue_number)
    if injection is None:
        return event
    return event.model_copy(
        update={
            "category": event.category or injection.category,
            "severity": event.severity or injection.severity.value,
            "file_path": event.file_path or injection.file_path,
            "branch": event.branch or injection.branch,
            "vuln_id": event.vuln_id or injection.vuln_id,
        }
    )


def _record_refusal(
    db: Session,
    event: IssueEvent,
    key: str,
    decision: guardrails.Decision,
    settings: Settings,
) -> DevinSession:
    injection = get_injection_by_issue(db, event.issue_number)
    record = DevinSession(
        idempotency_key=key,
        issue_number=event.issue_number,
        issue_url=event.issue_url,
        category=event.category or "",
        severity=event.severity or "",
        injection_id=injection.id if injection else None,
        status=(
            SessionStatus.queued
            if decision.code == "concurrency_limit"
            else SessionStatus.refused_budget
        ),
        status_detail=decision.reason,
        acu_limit=0.0,
        simulated=settings.demo_mode,
    )
    db.add(record)
    db.flush()
    log_event(
        SERVICE,
        f"session_{decision.code}",
        f"issue #{event.issue_number}: {decision.reason}",
        level="warning",
        data={"issue_number": event.issue_number, **decision.as_dict()},
        db=db,
    )
    return record


def handle_issue(
    db: Session, event: IssueEvent, settings: Settings, verify: bool = False
) -> dict[str, Any]:
    """Entry point for an ``issues: opened`` event."""

    if verify and not settings.demo_mode and settings.github_token:
        try:
            with GitHubClient(settings) as gh:
                issue = gh.get_issue(event.issue_number)
        except GitHubError as exc:
            log_event(
                SERVICE,
                "issue_rejected_unverified",
                f"could not verify issue #{event.issue_number} on GitHub: {exc}",
                level="warning",
                data={"issue_number": event.issue_number},
                db=db,
            )
            return {"accepted": False, "reason": "issue_not_verified"}
        if issue.get("state") != "open":
            log_event(
                SERVICE,
                "issue_rejected_unverified",
                f"issue #{event.issue_number} is not open on GitHub",
                level="warning",
                data={"issue_number": event.issue_number},
                db=db,
            )
            return {"accepted": False, "reason": "issue_not_verified"}
        event = event.model_copy(
            update={
                "labels": [
                    str(label.get("name", ""))
                    for label in issue.get("labels", [])
                    if isinstance(label, dict)
                ],
                "title": str(issue.get("title", "")),
                "issue_url": str(issue.get("html_url", "")),
            }
        )

    event = _enrich_from_injection(db, event)
    repo = event.repo or settings.superset_fork_repo
    if repo != settings.superset_fork_repo:
        log_event(
            SERVICE,
            "issue_rejected_repo",
            f"ignoring issue from unmanaged repo {repo}",
            level="warning",
            data={"repo": repo, "issue_number": event.issue_number},
            db=db,
        )
        return {"accepted": False, "reason": "repo_not_managed"}

    if not qualifies(event.labels):
        log_event(
            SERVICE,
            "issue_ignored",
            f"issue #{event.issue_number} has no red-team/security label",
            data={"issue_number": event.issue_number, "labels": event.labels},
            db=db,
        )
        return {"accepted": False, "reason": "label_mismatch"}

    key = guardrails.idempotency_key(repo, event.issue_number)
    existing = get_session_by_key(db, key)
    if existing is not None and existing.status not in (
        SessionStatus.queued,
        SessionStatus.refused_budget,
    ):
        log_event(
            SERVICE,
            "session_deduplicated",
            f"issue #{event.issue_number} already has session "
            f"{existing.session_id or existing.id}",
            data={"issue_number": event.issue_number, "session": existing.as_dict()},
            db=db,
        )
        return {"accepted": True, "duplicate": True, "session": existing.as_dict()}

    decision = guardrails.evaluate(db, settings)
    if not decision.allowed:
        if existing is not None:
            existing.status_detail = decision.reason
            db.flush()
            record = existing
        else:
            record = _record_refusal(db, event, key, decision, settings)
        return {
            "accepted": False,
            "reason": decision.code,
            "decision": decision.as_dict(),
            "session": record.as_dict(),
        }

    injection = get_injection_by_issue(db, event.issue_number)
    record = existing or DevinSession(
        idempotency_key=key,
        issue_number=event.issue_number,
        issue_url=event.issue_url,
        category=event.category or "",
        severity=event.severity or "",
        injection_id=injection.id if injection else None,
    )
    record.acu_limit = decision.acu_reserved
    if existing is not None:
        # Session start time restarts when a queued session is finally admitted.
        record.created_at = dt.datetime.now(dt.timezone.utc)
    record.status = SessionStatus.running
    record.status_detail = "session requested"
    record.simulated = settings.demo_mode or not settings.devin_api_key
    if existing is None:
        db.add(record)
    db.flush()
    # Commit the reservation before any outbound API call so the write lock is
    # never held across the network round trip.
    db.commit()

    prompt = build_prompt(event, settings)
    title = f"[blue-team] Remediate {event.category or 'security issue'} " f"#{event.issue_number}"

    if record.simulated:
        record.session_id = f"devin-sim-{event.issue_number}"
        record.session_url = f"https://app.devin.ai/sessions/{record.session_id}"
        record.status_detail = "[DEMO] simulated session"
        db.flush()
        log_event(
            SERVICE,
            "session_created",
            f"[DEMO] simulated Devin session for issue #{event.issue_number}",
            data={
                "issue_number": event.issue_number,
                "session_id": record.session_id,
                "acu_limit": record.acu_limit,
                "category": event.category,
            },
            db=db,
        )
    else:
        try:
            with DevinClient(
                settings.devin_api_key, settings.devin_api_base
            ) as devin:
                created = devin.create_session(
                    prompt=prompt,
                    title=title,
                    max_acu_limit=max(int(decision.acu_reserved), 1),
                    tags=[
                        "purple-team",
                        "blue-team",
                        f"issue-{event.issue_number}",
                        f"category-{event.category or 'unknown'}",
                    ],
                    idempotent=True,
                )
        except DevinAPIError as exc:
            record.status = SessionStatus.failed
            record.status_detail = f"devin api error: {exc}"[:200]
            db.flush()
            log_event(
                SERVICE,
                "session_create_failed",
                f"could not create session for issue "
                f"#{event.issue_number}: {exc}",
                level="error",
                data={"issue_number": event.issue_number},
                db=db,
            )
            return {"accepted": False, "reason": "devin_api_error", "error": str(exc)}

        record.session_id = str(created.get("session_id", ""))
        record.session_url = str(created.get("url", ""))
        record.status_detail = "session created"
        db.flush()
        log_event(
            SERVICE,
            "session_created",
            f"Devin session {record.session_id} created for issue "
            f"#{event.issue_number}",
            data={
                "issue_number": event.issue_number,
                "session_id": record.session_id,
                "session_url": record.session_url,
                "acu_limit": record.acu_limit,
                "is_new_session": created.get("is_new_session"),
                "category": event.category,
            },
            db=db,
        )

    db.commit()
    _comment_on_issue(db, record, settings)
    return {"accepted": True, "duplicate": False, "session": record.as_dict()}


def _comment_on_issue(
    db: Session, record: DevinSession, settings: Settings
) -> None:
    """Post a status comment linking the Devin session back on the issue."""

    body = (
        "### Blue team: autonomous remediation started\n\n"
        f"- **Devin session:** {record.session_url or record.session_id}\n"
        f"- **ACU cap for this session:** {record.acu_limit}\n"
        f"- **Category:** `{record.category}` | **Severity:** "
        f"`{record.severity}`\n\n"
        "A pull request will be linked here when the session produces one."
    )
    if record.simulated:
        log_event(
            SERVICE,
            "issue_comment_simulated",
            f"[DEMO] would comment on issue #{record.issue_number}",
            data={"issue_number": record.issue_number, "body": body},
            db=db,
        )
        return

    try:
        with GitHubClient(settings) as gh:
            gh.comment_issue(record.issue_number, body)
        log_event(
            SERVICE,
            "issue_commented",
            f"posted session link on issue #{record.issue_number}",
            data={"issue_number": record.issue_number},
            db=db,
        )
    except GitHubError as exc:
        log_event(
            SERVICE,
            "issue_comment_failed",
            f"could not comment on issue #{record.issue_number}: {exc}",
            level="warning",
            data={"issue_number": record.issue_number},
            db=db,
        )
