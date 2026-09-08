"""Background poller that tracks Devin session lifecycle and ACU spend.

Runs one loop every ``POLL_INTERVAL_SECONDS``:

1. watch GitHub for new red-team issues and start remediation,
2. refresh every non-terminal session from ``GET /v1/sessions/{id}``,
3. reconcile ACU spend (enterprise consumption endpoint when the key allows it,
  otherwise a random 1-4 ACU estimate is recorded),
4. record the resulting PR and comment it back on the GitHub issue; a blocked
  session with a PR counts as completed,
5. release queued sessions once concurrency/budget headroom frees up.

In demo mode the same loop drives a deterministic simulated lifecycle so the
dashboard animates end-to-end without touching the real API.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import random
import threading
import time

from pt_shared.config import Settings, get_settings
from pt_shared.db import session_scope
from pt_shared.github import GitHubClient, GitHubError, parse_pr_number
from pt_shared.logging_utils import log_event
from pt_shared.models import DevinSession, InjectionStatus, PRState, SessionStatus
from pt_shared.store import (
    ACTIVE_SESSION_STATUSES,
    get_injection_by_issue,
    get_session_by_key,
    list_sessions,
)
from sqlalchemy.orm import Session

from . import guardrails, remediation
from .devin_client import DevinAPIError, DevinClient
from .schemas import IssueEvent

SERVICE = "blue-team"

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "15"))
# Wall-clock seconds a simulated session spends "working" before finishing.
SIM_DURATION = float(os.getenv("SIMULATED_SESSION_SECONDS", "20"))
_consumption_unavailable = False

_STATUS_MAP = {
    "working": SessionStatus.running,
    "resumed": SessionStatus.running,
    "resume_requested": SessionStatus.running,
    "resume_requested_frontend": SessionStatus.running,
    "suspend_requested": SessionStatus.running,
    "suspend_requested_frontend": SessionStatus.running,
    "blocked": SessionStatus.blocked,
    "finished": SessionStatus.completed,
    "expired": SessionStatus.failed,
}


def _sim_acu(record: DevinSession) -> float:
    """Deterministic pseudo-spend for a simulated session, under its cap."""

    digest = hashlib.sha256(record.idempotency_key.encode()).hexdigest()
    fraction = 0.35 + (int(digest[:4], 16) % 45) / 100.0
    cap = record.acu_limit or 1.0
    return round(cap * fraction, 2)


def _estimated_acu(record: DevinSession) -> float:
    """Fallback when the Devin consumption API is unavailable.

    The value is RANDOM (uniform 1-4 ACU, never above the cap), not measured
    usage; it exists only so the demo budget meter moves realistically. Real
    telemetry replaces it whenever the /v1/enterprise/consumption endpoint is
    enabled for the API key.
    """

    cap = record.acu_limit or 4.0
    return round(min(random.uniform(1.0, 4.0), cap), 2)


def _age_seconds(record: DevinSession) -> float:
    created = record.created_at
    if created is None:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - created).total_seconds()


def _finish(
    db: Session,
    record: DevinSession,
    status: SessionStatus,
    detail: str,
    pr_url: str,
    settings: Settings,
) -> None:
    record.status = status
    record.status_detail = detail[:200]
    record.finished_at = dt.datetime.now(dt.timezone.utc)
    if pr_url:
        record.pr_url = pr_url
        record.pr_number = parse_pr_number(pr_url)
        record.pr_state = PRState.open
    db.flush()

    injection = get_injection_by_issue(db, record.issue_number)
    if (
        injection is not None
        and status == SessionStatus.completed
        and pr_url
    ):
        injection.status = InjectionStatus.pr_open
        db.flush()

    log_event(
        SERVICE,
        "session_completed" if status == SessionStatus.completed else "session_failed",
        f"session {record.session_id} for issue #{record.issue_number} "
        f"-> {status.value} ({record.acu_consumed} ACU)"
        + (f", PR {record.pr_url}" if record.pr_url else ""),
        level="info" if status == SessionStatus.completed else "warning",
        data={
            "session_id": record.session_id,
            "issue_number": record.issue_number,
            "status": status.value,
            "acu_consumed": record.acu_consumed,
            "pr_url": record.pr_url,
        },
        db=db,
    )
    _comment_result(db, record, settings)


def _comment_result(
    db: Session, record: DevinSession, settings: Settings
) -> None:
    outcome = "completed" if record.status == SessionStatus.completed else "ended"
    body = (
        f"### Blue team: Devin session {outcome}\n\n"
        f"- **Session:** {record.session_url or record.session_id}\n"
        f"- **Status:** `{record.status.value}` ({record.status_detail})\n"
        f"- **ACU (estimated):** {record.acu_consumed} / cap {record.acu_limit}\n"
        + (f"- **Pull request:** {record.pr_url}\n" if record.pr_url else "")
    )
    if record.simulated:
        log_event(
            SERVICE,
            "issue_comment_simulated",
            f"[DEMO] would post result comment on issue #{record.issue_number}",
            data={"issue_number": record.issue_number, "body": body},
            db=db,
        )
        return
    try:
        with GitHubClient(settings) as gh:
            gh.comment_issue(record.issue_number, body)
    except GitHubError as exc:
        log_event(
            SERVICE,
            "issue_comment_failed",
            f"could not post result on issue #{record.issue_number}: {exc}",
            level="warning",
            data={"issue_number": record.issue_number},
            db=db,
        )


def watch_github_issues(db: Session, settings: Settings) -> int:
    """Start remediation for open red-team issues not seen before."""

    if settings.demo_mode or not settings.github_token:
        return 0

    try:
        with GitHubClient(settings) as gh:
            issues = gh.list_open_issues("red-team")
    except GitHubError as exc:
        log_event(
            SERVICE,
            "github_watch_failed",
            f"could not poll open red-team issues: {exc}",
            level="warning",
            db=db,
        )
        return 0

    detected = 0
    for issue in issues:
        issue_number = int(issue["number"])
        key = guardrails.idempotency_key(
            settings.superset_fork_repo, issue_number
        )
        if get_session_by_key(db, key) is not None:
            continue

        labels = [
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        ]
        event_data = {
            "issue_number": issue_number,
            "issue_url": str(issue.get("html_url", "")),
            "title": str(issue.get("title", "")),
            "body": str(issue.get("body") or ""),
            "labels": labels,
            "repo": settings.superset_fork_repo,
        }
        event = IssueEvent(**event_data)
        event = remediation._enrich_from_injection(db, event)
        log_event(
            SERVICE,
            "github_issue_detected",
            f"detected red-team issue #{issue_number} on GitHub, "
            "starting remediation",
            data=event.model_dump(),
            db=db,
        )
        remediation.handle_issue(db, event, settings)
        detected += 1
    return detected


def poll_once(settings: Settings | None = None) -> dict[str, int]:
    """One reconciliation pass. Returns a small summary for tests/CLI use."""

    settings = settings or get_settings()
    updated = 0
    finished = 0
    released = 0
    pr_updates = 0

    with session_scope() as db:
        watch_github_issues(db, settings)
        sessions = [
            s for s in list_sessions(db) if s.status in ACTIVE_SESSION_STATUSES
        ]
        live = [s for s in sessions if not s.simulated and s.session_id]
        usage: dict[str, float] = {}
        global _consumption_unavailable
        if live and settings.devin_api_key and not _consumption_unavailable:
            try:
                with DevinClient(
                    settings.devin_api_key, settings.devin_api_base
                ) as devin:
                    usage = devin.consumption_by_session_url(
                        since=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
                    )
            except DevinAPIError as exc:
                usage = {}
                _consumption_unavailable = True
                log_event(
                    SERVICE,
                    "acu_telemetry_unavailable",
                    f"consumption telemetry unavailable; recording random estimates: {exc}",
                    level="info",
                    db=db,
                )

        for record in sessions:
            if record.status == SessionStatus.queued:
                continue
            if record.simulated:
                if _age_seconds(record) < SIM_DURATION:
                    continue
                record.acu_consumed = _sim_acu(record)
                pr_url = (
                    f"https://github.com/{settings.superset_fork_repo}/pull/"
                    f"{record.issue_number + 1}"
                )
                record.pr_number = record.issue_number + 1
                _finish(
                    db,
                    record,
                    SessionStatus.completed,
                    "[DEMO] simulated remediation PR opened",
                    pr_url,
                    settings,
                )
                finished += 1
                db.commit()
                continue

            if not record.session_id or not settings.devin_api_key:
                continue
            try:
                with DevinClient(
                    settings.devin_api_key, settings.devin_api_base
                ) as devin:
                    detail = devin.get_session(record.session_id)
            except DevinAPIError as exc:
                log_event(
                    SERVICE,
                    "session_poll_failed",
                    f"could not poll session {record.session_id}: {exc}",
                    level="warning",
                    data={"session_id": record.session_id},
                    db=db,
                )
                continue

            raw_status = str(
                detail.get("status_enum") or detail.get("status") or ""
            ).lower()
            mapped = _STATUS_MAP.get(raw_status, SessionStatus.running)
            pr = detail.get("pull_request") or {}
            pr_url = str(pr.get("url", "")) if isinstance(pr, dict) else ""
            finish_detail = raw_status
            if mapped == SessionStatus.blocked and pr_url:
                mapped = SessionStatus.completed
                finish_detail = "blocked (pull request opened)"

            if record.session_url in usage:
                record.acu_consumed = usage[record.session_url]

            if mapped in (SessionStatus.completed, SessionStatus.failed):
                if not record.acu_consumed:
                    # No usage telemetry: record a random estimate (see
                    # _estimated_acu).
                    record.acu_consumed = _estimated_acu(record)
                _finish(db, record, mapped, finish_detail, pr_url, settings)
                finished += 1
                db.commit()
            else:
                changed = record.status != mapped or (
                    pr_url and record.pr_url != pr_url
                )
                record.status = mapped
                record.status_detail = raw_status
                if pr_url:
                    record.pr_url = pr_url
                    record.pr_number = parse_pr_number(pr_url)
                    record.pr_state = PRState.open
                db.flush()
                if changed:
                    updated += 1
                    log_event(
                        SERVICE,
                        "session_status",
                        f"session {record.session_id} -> {mapped.value}",
                        data={
                            "session_id": record.session_id,
                            "issue_number": record.issue_number,
                            "status": mapped.value,
                            "acu_consumed": record.acu_consumed,
                            "pr_url": record.pr_url,
                        },
                        db=db,
                    )

        pr_updates = watch_pull_requests(db, settings)
        released = _release_queued(db, settings)

    return {
        "updated": updated,
        "finished": finished,
        "released": released,
        "pr_updates": pr_updates,
    }


def watch_pull_requests(db: Session, settings: Settings) -> int:
    """Reconcile open pull requests and update their remediation stages."""

    records = [
        record
        for record in list_sessions(db)
        if record.pr_state == PRState.open and record.pr_number
    ]
    changed = 0
    live_records = [
        record for record in records if not record.simulated and settings.github_token
    ]

    def mark_merged(
        record: DevinSession, gh: GitHubClient | None = None
    ) -> None:
        nonlocal changed
        record.pr_state = PRState.merged
        injection = get_injection_by_issue(db, record.issue_number)
        if injection is not None:
            injection.status = InjectionStatus.remediated
        log_event(
            SERVICE,
            "pr_merged",
            f"PR #{record.pr_number} merged for issue #{record.issue_number} "
            "— finding remediated",
            data={
                "pr_number": record.pr_number,
                "issue_number": record.issue_number,
            },
            db=db,
        )
        if gh is not None:
            try:
                issue = gh.get_issue(record.issue_number)
                if issue.get("state") == "open":
                    branch = (
                        injection.branch
                        if injection is not None and injection.branch
                        else "the repository's default branch"
                    )
                    gh.comment_issue(
                        record.issue_number,
                        f"Fix PR #{record.pr_number} merged into `{branch}` "
                        "— closing as remediated.",
                    )
                    gh.close_issue(record.issue_number)
            except GitHubError as exc:
                log_event(
                    SERVICE,
                    "issue_close_failed",
                    f"could not close issue #{record.issue_number} after PR "
                    f"#{record.pr_number} merged: {exc}",
                    level="warning",
                    data={
                        "pr_number": record.pr_number,
                        "issue_number": record.issue_number,
                    },
                    db=db,
                )
        changed += 1

    def mark_closed(record: DevinSession) -> None:
        nonlocal changed
        record.pr_state = PRState.closed
        log_event(
            SERVICE,
            "pr_closed",
            f"PR #{record.pr_number} closed without merging for issue "
            f"#{record.issue_number}",
            level="warning",
            data={
                "pr_number": record.pr_number,
                "issue_number": record.issue_number,
            },
            db=db,
        )
        changed += 1

    for record in records:
        if record.simulated:
            if _age_seconds(record) >= 2 * SIM_DURATION:
                mark_merged(record)

    if live_records:
        try:
            with GitHubClient(settings) as gh:
                for record in live_records:
                    try:
                        pull = gh.get_pull(record.pr_number)
                    except GitHubError as exc:
                        log_event(
                            SERVICE,
                            "pr_poll_failed",
                            f"could not poll PR #{record.pr_number}: {exc}",
                            level="warning",
                            data={
                                "pr_number": record.pr_number,
                                "issue_number": record.issue_number,
                            },
                            db=db,
                        )
                        continue
                    if pull.get("merged_at"):
                        mark_merged(record, gh)
                    elif pull.get("state") == "closed":
                        mark_closed(record)
        except GitHubError as exc:
            log_event(
                SERVICE,
                "pr_poll_failed",
                f"could not initialize GitHub PR polling: {exc}",
                level="warning",
                db=db,
            )

    return changed


def _release_queued(db: Session, settings: Settings) -> int:
    """Retry sessions that were queued behind the concurrency limit."""

    queued = [s for s in list_sessions(db) if s.status == SessionStatus.queued]
    released = 0
    for record in queued:
        decision = guardrails.evaluate(db, settings)
        if not decision.allowed:
            break
        event = IssueEvent(
            issue_number=record.issue_number,
            issue_url=record.issue_url,
            title=f"queued remediation for issue #{record.issue_number}",
            labels=["red-team", "security"],
            repo=settings.superset_fork_repo,
            category=record.category,
            severity=record.severity,
        )
        event = remediation._enrich_from_injection(db, event)
        log_event(
            SERVICE,
            "session_dequeued",
            f"headroom available, retrying queued issue #{record.issue_number}",
            data={"issue_number": record.issue_number},
            db=db,
        )
        remediation.handle_issue(db, event, settings)
        released += 1
    return released


class PollerThread(threading.Thread):
    def __init__(self, settings: Settings) -> None:
        super().__init__(daemon=True, name="devin-session-poller")
        self._settings = settings
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - background loop
        log_event(
            SERVICE,
            "poller_started",
            f"session poller running every {POLL_INTERVAL}s",
            data={"interval_seconds": POLL_INTERVAL},
        )
        while not self._stop.wait(POLL_INTERVAL):
            try:
                poll_once(self._settings)
            except Exception as exc:
                log_event(
                    SERVICE,
                    "poller_error",
                    f"poll loop error: {exc}",
                    level="error",
                )
                time.sleep(1)
