"""Request/response models for the blue team API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class IssueEvent(BaseModel):
    """Normalised ``issues: opened`` event.

    Accepts both the compact payload sent by the red team service / GitHub
    Actions workflow and a raw GitHub webhook body (see ``from_webhook``).
    """

    issue_number: int
    issue_url: str = ""
    title: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    repo: str = ""
    category: str = ""
    severity: str = ""
    file_path: str = ""
    branch: str = ""
    vuln_id: str = ""

    @model_validator(mode="after")
    def _derive_from_labels(self) -> "IssueEvent":
        for label in self.labels:
            if label.startswith("severity:") and not self.severity:
                object.__setattr__(self, "severity", label.split(":", 1)[1])
            if label.startswith("category:") and not self.category:
                object.__setattr__(self, "category", label.split(":", 1)[1])
        return self

    @classmethod
    def from_webhook(cls, payload: dict[str, Any]) -> "IssueEvent":
        issue = payload.get("issue", payload)
        repo = ""
        if isinstance(payload.get("repository"), dict):
            repo = str(payload["repository"].get("full_name", ""))
        labels = [
            label["name"] if isinstance(label, dict) else str(label)
            for label in issue.get("labels", [])
        ]
        return cls(
            issue_number=int(issue["number"]),
            issue_url=str(issue.get("html_url", "")),
            title=str(issue.get("title", "")),
            body=str(issue.get("body") or ""),
            labels=labels,
            repo=repo,
        )
