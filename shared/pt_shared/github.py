"""Minimal GitHub REST client used by the red and blue team services.

Only the handful of endpoints the platform needs are implemented, so there is
no heavyweight dependency. Every call is scoped to the configured Superset
fork; the client refuses to touch any other repository.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from .config import Settings


class GitHubError(RuntimeError):
    pass


class RepoNotAllowedError(GitHubError):
    """Raised when a caller tries to act on a repo other than the fork."""


def parse_pr_number(url: str) -> int | None:
    match = re.search(r"/pull/(\d+)", url)
    return int(match.group(1)) if match else None


class GitHubClient:
    def __init__(self, settings: Settings, timeout: float = 30.0) -> None:
        self._settings = settings
        self._repo = settings.superset_fork_repo
        self._client = httpx.Client(
            base_url=settings.github_api_base.rstrip("/"),
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {settings.github_token}",
            },
        )

    # --- guardrail --------------------------------------------------------

    def _assert_allowed(self, repo: str) -> None:
        """Hard guardrail: only the configured Superset fork may be written."""

        if repo != self._repo:
            raise RepoNotAllowedError(
                f"refusing to write to {repo!r}; only "
                f"{self._repo!r} is allowed"
            )

    # --- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(
                f"{method} {path} -> {response.status_code}: {response.text[:400]}"
            )
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- repo / git -------------------------------------------------------

    def get_repo(self) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self._repo}").json()

    def default_branch(self) -> str:
        return str(self.get_repo().get("default_branch", "master"))

    def branch_exists(self, branch: str) -> bool:
        response = self._client.get(f"/repos/{self._repo}/git/ref/heads/{branch}")
        return response.status_code == 200

    def ensure_branch(self, branch: str, base_branch: str | None = None) -> str:
        """Create ``branch`` off the default branch if it does not exist."""

        self._assert_allowed(self._repo)
        ref = self._client.get(f"/repos/{self._repo}/git/ref/heads/{branch}")
        if ref.status_code == 200:
            return str(ref.json()["object"]["sha"])

        base = base_branch or self.default_branch()
        base_ref = self._request("GET", f"/repos/{self._repo}/git/ref/heads/{base}")
        base_sha = base_ref.json()["object"]["sha"]
        created = self._request(
            "POST",
            f"/repos/{self._repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        return str(created.json()["object"]["sha"])

    def get_file(self, path: str, ref: str) -> dict[str, Any] | None:
        response = self._client.get(
            f"/repos/{self._repo}/contents/{path}", params={"ref": ref}
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(
                f"GET contents/{path} -> {response.status_code}: "
                f"{response.text[:200]}"
            )
        return dict(response.json())

    def put_file(
        self, path: str, content: str, branch: str, message: str
    ) -> dict[str, Any]:
        """Create or update a file on ``branch``; idempotent by content."""

        self._assert_allowed(self._repo)
        existing = self.get_file(path, ref=branch)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        if existing is not None and existing.get("content"):
            current = existing["content"].replace("\n", "")
            if current == encoded:
                return {
                    "unchanged": True,
                    "sha": existing.get("sha", ""),
                    "html_url": existing.get("html_url", ""),
                }

        payload: dict[str, Any] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if existing is not None:
            payload["sha"] = existing["sha"]
        response = self._request(
            "PUT", f"/repos/{self._repo}/contents/{path}", json=payload
        )
        body = response.json()
        commit = body.get("commit", {})
        return {
            "unchanged": False,
            "sha": commit.get("sha", ""),
            "html_url": commit.get("html_url", ""),
        }

    # --- labels / issues --------------------------------------------------

    def ensure_label(self, name: str, color: str, description: str = "") -> None:
        self._assert_allowed(self._repo)
        response = self._client.get(f"/repos/{self._repo}/labels/{name}")
        if response.status_code == 200:
            return
        self._client.post(
            f"/repos/{self._repo}/labels",
            json={"name": name, "color": color, "description": description[:100]},
        )

    def find_issue_by_title(self, title: str) -> dict[str, Any] | None:
        """Look for an existing open or closed issue with the exact title."""

        for state in ("open", "closed"):
            page = 1
            while page <= 5:
                response = self._request(
                    "GET",
                    f"/repos/{self._repo}/issues",
                    params={"state": state, "per_page": 100, "page": page},
                )
                issues = response.json()
                if not issues:
                    break
                for issue in issues:
                    if "pull_request" in issue:
                        continue
                    if issue.get("title") == title:
                        return dict(issue)
                page += 1
        return None

    def list_open_issues(self, label: str) -> list[dict[str, Any]]:
        """List open issues with a label, excluding pull requests."""

        found: list[dict[str, Any]] = []
        for page in range(1, 6):
            response = self._request(
                "GET",
                f"/repos/{self._repo}/issues",
                params={
                    "state": "open",
                    "labels": label,
                    "per_page": 100,
                    "page": page,
                },
            )
            issues = response.json()
            if not issues:
                break
            found.extend(
                dict(issue)
                for issue in issues
                if "pull_request" not in issue
            )
        return found

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        self._assert_allowed(self._repo)
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "labels": labels,
        }
        if assignees:
            payload["assignees"] = assignees
        response = self._request(
            "POST",
            f"/repos/{self._repo}/issues",
            json=payload,
        )
        return dict(response.json())

    def get_issue(self, number: int) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/repos/{self._repo}/issues/{number}").json()
        )

    def get_pull(self, number: int) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/repos/{self._repo}/pulls/{number}").json()
        )

    def comment_issue(self, number: int, body: str) -> dict[str, Any]:
        self._assert_allowed(self._repo)
        response = self._request(
            "POST",
            f"/repos/{self._repo}/issues/{number}/comments",
            json={"body": body},
        )
        return dict(response.json())

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            f"/repos/{self._repo}/issues/{number}/comments",
            params={"per_page": 100},
        )
        return list(response.json())
