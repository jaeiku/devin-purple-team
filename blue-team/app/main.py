"""Blue team service: receives red-team issues and drives Devin remediation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pt_shared.config import Settings, get_settings
from pt_shared.db import get_db, init_db
from pt_shared.logging_utils import log_event
from pt_shared.store import list_sessions
from sqlalchemy.orm import Session

from . import guardrails
from .poller import PollerThread, poll_once
from .remediation import handle_issue
from .schemas import IssueEvent

SERVICE = "blue-team"

_poller: PollerThread | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _poller
    init_db()
    settings = get_settings()
    log_event(
        SERVICE,
        "service_started",
        f"blue team online, demo_mode={settings.demo_mode}, "
        f"max_acu/session={settings.max_acu_per_session}, "
        f"max_concurrent={settings.max_concurrent_sessions}, "
        f"budget_ceiling={settings.global_budget_acu_ceiling}",
        data={
            "demo_mode": settings.demo_mode,
            "max_acu_per_session": settings.max_acu_per_session,
            "max_concurrent_sessions": settings.max_concurrent_sessions,
            "global_budget_acu_ceiling": settings.global_budget_acu_ceiling,
            "devin_api_key_configured": bool(settings.devin_api_key),
        },
    )
    _poller = PollerThread(settings)
    _poller.start()
    try:
        yield
    finally:
        if _poller is not None:
            _poller.stop()


app = FastAPI(title="Purple Team - Blue Team Service", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE}


@app.get("/api/config")
def config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "target_repo": settings.superset_fork_repo,
        "demo_mode": settings.demo_mode,
        "devin_api_key_configured": bool(settings.devin_api_key),
        "guardrails": {
            "max_acu_per_session": settings.max_acu_per_session,
            "max_concurrent_sessions": settings.max_concurrent_sessions,
            "global_budget_acu_ceiling": settings.global_budget_acu_ceiling,
        },
    }


@app.get("/api/guardrails")
def guardrail_status(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Would a new session be admitted right now, and why (not)?"""

    return guardrails.evaluate(db, settings).as_dict()


@app.get("/api/sessions")
def get_sessions(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"sessions": [s.as_dict() for s in list_sessions(db)]}


@app.post("/api/events/issue-opened")
def issue_opened(
    event: IssueEvent,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Called by the GitHub Actions workflow / webhook, or by the red team in demo mode."""

    log_event(
        SERVICE,
        "issue_received",
        f"received issue #{event.issue_number}: {event.title}",
        data=event.model_dump(),
        db=db,
    )
    return handle_issue(db, event, settings)


@app.post("/api/webhooks/github")
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Raw GitHub ``issues`` webhook body, for direct webhook wiring."""

    payload = await request.json()
    action = payload.get("action")
    if action != "opened":
        return {"accepted": False, "reason": f"ignored action {action}"}
    try:
        event = IssueEvent.from_webhook(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"bad webhook payload: {exc}")
    return handle_issue(db, event, settings)


@app.post("/api/poll")
def trigger_poll(settings: Settings = Depends(get_settings)) -> dict[str, int]:
    """Force one reconciliation pass (used by the simulate script)."""

    return poll_once(settings)
