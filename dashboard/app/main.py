"""Blue team SOC dashboard: SOC-style observability over the shared store."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pt_shared.config import Settings, get_settings
from pt_shared.db import get_db, init_db
from pt_shared.logging_utils import get_logger
from pt_shared.store import (
    list_injections,
    list_sessions,
    metrics,
    recent_events,
)
from sqlalchemy.orm import Session

from .summary import leader_summary

SERVICE = "dashboard"
STATIC_DIR = Path(__file__).parent / "static"


def _asset_version() -> str:
    digest = hashlib.sha1()
    for filename in ("app.js", "style.css"):
        digest.update((STATIC_DIR / filename).read_bytes())
    return digest.hexdigest()[:8]


ASSET_VERSION = _asset_version()


CATEGORY_LABELS = {
    "pii_exposure": "PII Exposure",
    "rce_dependency": "Vulnerable Dependency / RCE",
    "query_injection": "SQL / Query Injection",
    "secrets_exposure": "Secrets Exposure",
    "insecure_deserialization": "Insecure Deserialization",
    "auth_bypass": "Authentication Bypass",
    "ssrf": "Server-Side Request Forgery",
    "path_traversal": "Path Traversal",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_logger(SERVICE).info("blue team SOC dashboard online")
    yield


app = FastAPI(title="Purple Team - Blue Team SOC Dashboard", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE}


@app.get("/api/overview")
def overview(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Everything the dashboard needs in a single poll."""

    data = metrics(db, settings.global_budget_acu_ceiling)
    injections = [i.as_dict() for i in list_injections(db)]
    sessions = [s.as_dict() for s in list_sessions(db)]
    return {
        "metrics": data,
        "category_labels": CATEGORY_LABELS,
        "injections": injections,
        "sessions": sessions,
        "summary": leader_summary(data, injections, sessions, settings),
        "config": {
            "target_repo": settings.superset_fork_repo,
            "demo_mode": settings.demo_mode,
            "max_acu_per_session": settings.max_acu_per_session,
            "max_concurrent_sessions": settings.max_concurrent_sessions,
            "global_budget_acu_ceiling": settings.global_budget_acu_ceiling,
            "red_team_url": settings.red_team_public_url,
            "dashboard_url": settings.dashboard_public_url,
            "red_team_port": settings.red_team_port,
            "dashboard_port": settings.dashboard_port,
        },
    }


@app.get("/api/events")
def get_events(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {"events": [e.as_dict() for e in recent_events(db, limit)]}


@app.get("/api/summary")
def get_summary(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    data = metrics(db, settings.global_budget_acu_ceiling)
    return leader_summary(
        data,
        [i.as_dict() for i in list_injections(db)],
        [s.as_dict() for s in list_sessions(db)],
        settings,
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text()
    html = html.replace(
        'href="/static/style.css"',
        f'href="/static/style.css?v={ASSET_VERSION}"',
    ).replace(
        'src="/static/app.js"',
        f'src="/static/app.js?v={ASSET_VERSION}"',
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
