"""Deterministic vulnerability injection into the Superset fork.

An injection is a two-step operation:

1. commit the synthetic vulnerable file to a numbered branch
   (``red-team/<vuln_id>/run-<n>``) in the configured fork, and
2. open a GitHub issue labelled ``security`` / ``red-team`` / ``severity:*``
   describing the vulnerability so the blue team can detect it.

Each call creates a new numbered instance. Within an instance, the GitHub
issue lookup by exact instance-specific title prevents a duplicate issue if a
request is retried after a partial failure. State is recorded in the shared
store keyed by ``vuln_id`` and instance number.

``DEMO_MODE=true`` short-circuits the GitHub calls and records a simulated
injection so the whole flow can be demonstrated with no credentials.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
from pt_shared.config import Settings
from pt_shared.github import GitHubClient, GitHubError
from pt_shared.logging_utils import log_event
from pt_shared.models import Injection, InjectionStatus, Severity
from pt_shared.store import next_instance
from sqlalchemy.orm import Session

from . import catalog
from .catalog import Vulnerability

SERVICE = "red-team"

SEVERITY_COLORS = {
    "critical": "b60205",
    "high": "d93f0b",
    "medium": "fbca04",
    "low": "0e8a16",
}


class InjectionError(RuntimeError):
    pass


def _simulated_issue_number(vuln: Vulnerability, instance: int) -> int:
    """Stable pseudo issue number so demo runs are reproducible."""

    digest = hashlib.sha256(
        f"{vuln.vuln_id}:{instance}".encode()
    ).hexdigest()
    return 9000 + int(digest[:4], 16) % 900


def _labels(vuln: Vulnerability) -> list[str]:
    return [
        "security",
        "red-team",
        f"severity:{vuln.severity}",
        f"category:{vuln.category}",
    ]


def _issue_title(vuln: Vulnerability, instance: int) -> str:
    return f"[red-team][{vuln.severity}] {vuln.title} (run #{instance})"


def _new_injection(
    db: Session, vuln: Vulnerability, settings: Settings
) -> Injection:
    instance = next_instance(db, vuln.vuln_id)
    record = Injection(
        vuln_id=vuln.vuln_id,
        instance=instance,
        title=vuln.title,
        category=vuln.category,
        severity=Severity(vuln.severity),
        cwe=vuln.cwe,
        repo=settings.superset_fork_repo,
        branch=vuln.branch_for(instance),
        file_path=vuln.file_path,
    )
    db.add(record)
    db.flush()
    return record


def inject(
    db: Session, vuln_id: str, settings: Settings, notify_blue_team: bool = True
) -> dict[str, Any]:
    """Inject one new numbered instance of a catalog vulnerability."""

    vuln = catalog.get(vuln_id)
    record = _new_injection(db, vuln, settings)

    if settings.demo_mode or not settings.github_token:
        result = _inject_simulated(db, vuln, record, settings)
    else:
        result = _inject_live(db, vuln, record, settings)

    if settings.demo_mode and notify_blue_team and record.issue_number:
        # Commit first: the blue team writes to the same store, and holding an
        # open write transaction across the handoff would deadlock SQLite.
        db.commit()
        _notify_blue_team(db, record, settings)
    elif not settings.demo_mode and record.issue_number:
        log_event(
            SERVICE,
            "issue_opened_on_github",
            f"issue #{record.issue_number} opened on GitHub; "
            "blue team picks it up from GitHub",
            data={
                "issue_number": record.issue_number,
                "issue_url": record.issue_url,
            },
            level="info",
            db=db,
        )

    return result


def _inject_simulated(
    db: Session, vuln: Vulnerability, record: Injection, settings: Settings
) -> dict[str, Any]:
    repo = settings.superset_fork_repo
    number = _simulated_issue_number(vuln, record.instance)
    record.simulated = True
    record.commit_sha = hashlib.sha1(
        f"{vuln.vuln_id}:{record.instance}:{vuln.content}".encode()
    ).hexdigest()
    record.commit_url = (
        f"https://github.com/{repo}/commit/{record.commit_sha}"
    )
    record.issue_number = number
    record.issue_url = f"https://github.com/{repo}/issues/{number}"
    record.status = InjectionStatus.issue_opened
    db.flush()

    log_event(
        SERVICE,
        "injection_simulated",
        f"[DEMO] injected {vuln.vuln_id} run #{record.instance} into "
        f"{repo} and opened issue #{number}",
        data={
            "vuln_id": vuln.vuln_id,
            "category": vuln.category,
            "severity": vuln.severity,
            "branch": record.branch,
            "file_path": vuln.file_path,
            "issue_number": number,
        },
        db=db,
    )
    return {
        "injection": record.as_dict(),
        "instance": record.instance,
        "created": True,
        "simulated": True,
    }


def _inject_live(
    db: Session, vuln: Vulnerability, record: Injection, settings: Settings
) -> dict[str, Any]:
    repo = settings.superset_fork_repo
    try:
        with GitHubClient(settings) as gh:
            gh.ensure_branch(record.branch)
            commit = gh.put_file(
                path=vuln.file_path,
                content=vuln.content,
                branch=record.branch,
                message=(
                    f"red-team: inject synthetic {vuln.category} "
                    f"vulnerability ({vuln.vuln_id})"
                ),
            )
            record.commit_sha = commit.get("sha", "")
            record.commit_url = commit.get("html_url", "")
            record.status = InjectionStatus.committed
            db.flush()

            for label in _labels(vuln):
                color = SEVERITY_COLORS.get(vuln.severity, "5319e7")
                if label in ("security", "red-team"):
                    color = "5319e7"
                gh.ensure_label(label, color, vuln.category)

            title = _issue_title(vuln, record.instance)
            issue = gh.find_issue_by_title(title)
            if issue is None:
                issue = gh.create_issue(
                    title=title,
                    body=catalog.issue_body(
                        vuln, repo, record.commit_url, record.branch
                    ),
                    labels=_labels(vuln),
                    assignees=[settings.effective_issue_assignee],
                )
                created_issue = True
            else:
                created_issue = False

            record.issue_number = int(issue["number"])
            record.issue_url = str(issue["html_url"])
            record.status = InjectionStatus.issue_opened
            record.simulated = False
            db.flush()
    except GitHubError as exc:
        record.status = InjectionStatus.failed
        db.flush()
        log_event(
            SERVICE,
            "injection_failed",
            f"injection of {vuln.vuln_id} failed: {exc}",
            level="error",
            data={"vuln_id": vuln.vuln_id},
            db=db,
        )
        raise InjectionError(str(exc)) from exc

    log_event(
        SERVICE,
        "injection_committed",
        f"injected {vuln.vuln_id} run #{record.instance} into "
        f"{repo}@{record.branch}, "
        f"issue #{record.issue_number}",
        data={
            "vuln_id": vuln.vuln_id,
            "category": vuln.category,
            "severity": vuln.severity,
            "branch": record.branch,
            "file_path": vuln.file_path,
            "issue_number": record.issue_number,
            "issue_created": created_issue,
        },
        db=db,
    )
    return {
        "injection": record.as_dict(),
        "instance": record.instance,
        "created": True,
        "issue_created": created_issue,
        "simulated": False,
    }


def _notify_blue_team(
    db: Session, record: Injection, settings: Settings
) -> None:
    """Stand in for GitHub's issue-opened event during demo mode.

    Live deployments use the GitHub issue watcher, Actions workflow or raw
    webhook instead; the blue team is idempotent per issue number.
    """

    payload = {
        "issue_number": record.issue_number,
        "issue_url": record.issue_url,
        "title": record.title,
        "category": record.category,
        "severity": record.severity.value,
        "repo": record.repo,
        "file_path": record.file_path,
        "branch": record.branch,
        "labels": ["security", "red-team", f"severity:{record.severity.value}"],
        "vuln_id": record.vuln_id,
    }
    try:
        response = httpx.post(
            f"{settings.blue_team_url.rstrip('/')}/api/events/issue-opened",
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        log_event(
            SERVICE,
            "blue_team_notified",
            f"handed issue #{record.issue_number} to the blue team",
            data={"issue_number": record.issue_number, "response": response.json()},
            db=db,
        )
    except Exception as exc:  # network/blue-team down must not fail injection
        log_event(
            SERVICE,
            "blue_team_notify_failed",
            f"could not notify blue team for issue "
            f"#{record.issue_number}: {exc}",
            level="warning",
            data={"issue_number": record.issue_number},
            db=db,
        )
