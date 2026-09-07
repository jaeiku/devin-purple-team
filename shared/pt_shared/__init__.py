"""Shared library for the Devin Purple Team platform.

Provides the SQLAlchemy-backed shared datastore, structured logging, and the
Pydantic settings that every service (red / blue / dashboard) depends on. The
datastore is the single source of truth for injected vulnerabilities, GitHub
issues, spawned Devin sessions, resulting PRs, accumulated ACU spend, and the
event/audit log surfaced by the dashboard.
"""

from .config import Settings, get_settings
from .logging_utils import get_logger, log_event

__all__ = ["Settings", "get_settings", "get_logger", "log_event"]
