"""SQLAlchemy models for the shared purple-team datastore."""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class InjectionStatus(str, enum.Enum):
    pending = "pending"
    committed = "committed"
    issue_opened = "issue_opened"
    remediated = "remediated"
    failed = "failed"


class SessionStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    blocked = "blocked"
    completed = "completed"
    failed = "failed"
    refused_budget = "refused_budget"


class Injection(Base):
    """One numbered vulnerability instance injected into the Superset fork."""

    __tablename__ = "injections"
    __table_args__ = (
        UniqueConstraint(
            "vuln_id", "instance", name="uq_injection_vuln_instance"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vuln_id: Mapped[str] = mapped_column(String(128), index=True)
    instance: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[Severity] = mapped_column(Enum(Severity), index=True)
    cwe: Mapped[str] = mapped_column(String(32), default="")
    repo: Mapped[str] = mapped_column(String(128))
    branch: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[str] = mapped_column(String(400))
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    commit_url: Mapped[str] = mapped_column(String(500), default="")
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[InjectionStatus] = mapped_column(
        Enum(InjectionStatus), default=InjectionStatus.pending, index=True
    )
    simulated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sessions: Mapped[list["DevinSession"]] = relationship(
        back_populates="injection"
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "vuln_id": self.vuln_id,
            "instance": self.instance,
            "title": self.title,
            "category": self.category,
            "severity": self.severity.value,
            "cwe": self.cwe,
            "repo": self.repo,
            "branch": self.branch,
            "file_path": self.file_path,
            "commit_sha": self.commit_sha,
            "commit_url": self.commit_url,
            "issue_number": self.issue_number,
            "issue_url": self.issue_url,
            "status": self.status.value,
            "simulated": self.simulated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DevinSession(Base):
    """A Devin session spawned by the blue team to remediate an issue."""

    __tablename__ = "devin_sessions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_session_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    session_url: Mapped[str] = mapped_column(String(500), default="")
    issue_number: Mapped[int] = mapped_column(Integer, index=True)
    issue_url: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(32), default="")
    injection_id: Mapped[int | None] = mapped_column(
        ForeignKey("injections.id"), nullable=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.queued, index=True
    )
    status_detail: Mapped[str] = mapped_column(String(200), default="")
    acu_limit: Mapped[float] = mapped_column(Float, default=0.0)
    acu_consumed: Mapped[float] = mapped_column(Float, default=0.0)
    pr_url: Mapped[str] = mapped_column(String(500), default="")
    simulated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    injection: Mapped[Injection | None] = relationship(back_populates="sessions")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "session_id": self.session_id,
            "session_url": self.session_url,
            "issue_number": self.issue_number,
            "issue_url": self.issue_url,
            "category": self.category,
            "severity": self.severity,
            "injection_id": self.injection_id,
            "status": self.status.value,
            "status_detail": self.status_detail,
            "acu_limit": self.acu_limit,
            "acu_consumed": self.acu_consumed,
            "pr_url": self.pr_url,
            "simulated": self.simulated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Event(Base):
    """Lightweight structured log/event stream surfaced by the dashboard."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    service: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "service": self.service,
            "level": self.level,
            "event": self.event,
            "message": self.message,
            "data": self.data or {},
        }
