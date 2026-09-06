"""Leader-facing "is this actually working?" verdict.

Turns the raw counters into the three things a leader asks: is the loop
closing, how fast, and what is it costing. Everything is derived from the
shared store so the answer matches the operational data exactly.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pt_shared.config import Settings


def _parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        parsed = dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def leader_summary(
    metrics: dict[str, Any],
    injections: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    durations: list[float] = []
    for session in sessions:
        if session["status"] != "completed":
            continue
        start = _parse(session.get("created_at"))
        end = _parse(session.get("updated_at"))
        if start and end and end >= start:
            durations.append((end - start).total_seconds() / 60.0)

    issues = metrics["issues_created"]
    remediated = metrics["remediated"]
    coverage = (remediated / issues * 100.0) if issues else 0.0
    acu_per_fix = (
        metrics["acu_spend"] / metrics["sessions_completed"]
        if metrics["sessions_completed"]
        else 0.0
    )

    if issues == 0:
        verdict, headline = "idle", "No vulnerabilities injected yet."
    elif metrics["sessions_total"] == 0:
        verdict, headline = (
            "degraded",
            f"{issues} issue(s) raised but no Devin session was spawned - "
            "check the blue team trigger.",
        )
    elif metrics["sessions_refused_budget"] and not metrics["sessions_active"]:
        verdict, headline = (
            "budget_blocked",
            "Remediation is paused: the ACU budget ceiling was reached.",
        )
    elif metrics["success_rate_pct"] >= 80.0 and remediated:
        verdict, headline = (
            "healthy",
            f"Autonomous remediation is working: {remediated}/{issues} "
            f"injected vulnerabilities closed by a Devin PR.",
        )
    elif metrics["sessions_active"]:
        verdict, headline = (
            "in_progress",
            f"{metrics['sessions_active']} remediation session(s) in flight "
            f"across {issues} open finding(s).",
        )
    else:
        verdict, headline = (
            "attention",
            f"{metrics['sessions_failed']} session(s) failed to produce a fix "
            "- human review needed.",
        )

    return {
        "verdict": verdict,
        "headline": headline,
        "detection_to_pr_coverage_pct": round(coverage, 1),
        "median_time_to_pr_minutes": round(_median(durations), 1),
        "mean_acu_per_remediation": round(acu_per_fix, 2),
        "budget_used_pct": metrics["budget_used_pct"],
        "budget_remaining_acu": metrics["acu_remaining"],
        "guardrails": {
            "max_acu_per_session": settings.max_acu_per_session,
            "max_concurrent_sessions": settings.max_concurrent_sessions,
            "global_budget_acu_ceiling": settings.global_budget_acu_ceiling,
        },
        "answers": [
            {
                "question": "Are injected vulnerabilities being detected?",
                "answer": f"{issues} of {len(injections)} injections produced a "
                f"labelled GitHub issue.",
            },
            {
                "question": "Is Devin acting on them autonomously?",
                "answer": f"{metrics['sessions_total']} session(s) spawned, "
                f"{metrics['sessions_active']} active, "
                f"{metrics['sessions_completed']} completed.",
            },
            {
                "question": "Are real fixes landing?",
                "answer": f"{metrics['prs_opened']} pull request(s) opened; "
                f"{remediated} finding(s) marked remediated "
                f"({round(coverage, 1)}% coverage).",
            },
            {
                "question": "What is it costing?",
                "answer": f"{metrics['acu_spend']} of "
                f"{metrics['acu_ceiling']} ACU used "
                f"({metrics['budget_used_pct']}%), "
                f"{round(acu_per_fix, 2)} ACU per remediation.",
            },
        ],
    }
