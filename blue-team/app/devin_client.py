"""Thin client for the Devin API (https://docs.devin.ai/api-reference/overview).

Only three calls are used:

* ``POST /v1/sessions``               -- spawn a remediation session
* ``GET  /v1/sessions/{session_id}``  -- poll status / resulting PR
* ``GET  /v1/enterprise/consumption`` -- best-effort per-session ACU spend

The session payload carries the guardrails: ``max_acu_limit`` caps what a
single session may burn and ``idempotent`` (combined with a deterministic
prompt derived from the issue) prevents duplicate sessions for the same issue.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

class DevinAPIError(RuntimeError):
    pass


class DevinClient:
    def __init__(
        self, api_key: str, base_url: str = "https://api.devin.ai", timeout: float = 60.0
    ) -> None:
        if not api_key:
            raise DevinAPIError("DEVIN_API_KEY is not configured")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DevinClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def create_session(
        self,
        prompt: str,
        *,
        title: str,
        max_acu_limit: int,
        tags: list[str] | None = None,
        idempotent: bool = True,
        unlisted: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": prompt,
            "title": title[:200],
            "idempotent": idempotent,
            "max_acu_limit": int(max_acu_limit),
            "unlisted": unlisted,
        }
        if tags:
            payload["tags"] = tags[:50]
        response = self._client.post("/v1/sessions", json=payload)
        if response.status_code >= 400:
            raise DevinAPIError(
                f"POST /v1/sessions -> {response.status_code}: "
                f"{response.text[:400]}"
            )
        return dict(response.json())

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self._client.get(f"/v1/sessions/{session_id}")
        if response.status_code >= 400:
            raise DevinAPIError(
                f"GET /v1/sessions/{session_id} -> {response.status_code}: "
                f"{response.text[:400]}"
            )
        return dict(response.json())

    def consumption_by_session_url(
        self, since: dt.datetime | None = None
    ) -> dict[str, float]:
        """Return ``{session_url: acu_used}`` from the consumption endpoint.

        This endpoint is enterprise-scoped and may be unavailable (403) for a
        given key; callers record a random 1-4 ACU estimate instead.
        """

        params: dict[str, str] = {}
        if since is not None:
            params["start_date"] = since.date().isoformat()
        response = self._client.get("/v1/enterprise/consumption", params=params)
        if response.status_code >= 400:
            raise DevinAPIError(
                f"GET /v1/enterprise/consumption -> {response.status_code}: "
                f"{response.text[:400]}"
            )
        body = response.json()
        sessions = body.get("sessions") or body.get("data") or []
        usage: dict[str, float] = {}
        for item in sessions:
            url = item.get("url")
            if url:
                usage[url] = float(item.get("acu_used", 0.0))
        return usage
