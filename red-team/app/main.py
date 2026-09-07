"""Red team service: catalog UI + deterministic vulnerability injection API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pt_shared.config import Settings, get_settings
from pt_shared.db import get_db, init_db
from pt_shared.logging_utils import log_event
from pt_shared.store import list_injections
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import catalog
from .injector import InjectionError, inject

SERVICE = "red-team"
STATIC_DIR = Path(__file__).parent / "static"


def _asset_version() -> str:
    digest = hashlib.sha1()
    for filename in ("app.js", "style.css"):
        digest.update((STATIC_DIR / filename).read_bytes())
    return digest.hexdigest()[:8]


ASSET_VERSION = _asset_version()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings = get_settings()
    log_event(
        SERVICE,
        "service_started",
        f"red team online, target={settings.superset_fork_repo}, "
        f"demo_mode={settings.demo_mode}",
        data={
            "target_repo": settings.superset_fork_repo,
            "demo_mode": settings.demo_mode,
            "catalog_size": len(catalog.CATALOG),
        },
    )
    yield


app = FastAPI(title="Purple Team - Red Team Service", lifespan=lifespan)


class InjectRequest(BaseModel):
    vuln_id: str
    notify_blue_team: bool = True


class InjectAllRequest(BaseModel):
    vuln_ids: list[str] | None = None
    notify_blue_team: bool = True


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE}


@app.get("/api/config")
def config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "target_repo": settings.superset_fork_repo,
        "demo_mode": settings.demo_mode,
        "github_token_configured": bool(settings.github_token),
        "red_team_url": settings.red_team_public_url,
        "dashboard_url": settings.dashboard_public_url,
        "red_team_port": settings.red_team_port,
        "dashboard_port": settings.dashboard_port,
    }


@app.get("/api/catalog")
def get_catalog() -> dict[str, Any]:
    return {
        "categories": catalog.CATEGORY_LABELS,
        "vulnerabilities": [v.as_dict() for v in catalog.CATALOG],
    }


@app.get("/api/catalog/{vuln_id}")
def get_catalog_entry(vuln_id: str) -> dict[str, Any]:
    try:
        return catalog.get(vuln_id).as_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown vuln_id {vuln_id}")


@app.get("/api/injections")
def get_injections(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"injections": [i.as_dict() for i in list_injections(db)]}


@app.post("/api/inject")
def post_inject(
    body: InjectRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.vuln_id not in catalog.CATALOG_BY_ID:
        raise HTTPException(
            status_code=404, detail=f"unknown vuln_id {body.vuln_id}"
        )
    try:
        return inject(db, body.vuln_id, settings, body.notify_blue_team)
    except InjectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/inject-all")
def post_inject_all(
    body: InjectAllRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Inject the whole catalog (or a subset) in one deterministic pass."""

    vuln_ids = body.vuln_ids or [v.vuln_id for v in catalog.CATALOG]
    results: list[dict[str, Any]] = []
    for vuln_id in vuln_ids:
        if vuln_id not in catalog.CATALOG_BY_ID:
            results.append({"vuln_id": vuln_id, "error": "unknown vuln_id"})
            continue
        try:
            results.append(inject(db, vuln_id, settings, body.notify_blue_team))
        except InjectionError as exc:
            results.append({"vuln_id": vuln_id, "error": str(exc)})
    return {"results": results}


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
    return HTMLResponse(html)
