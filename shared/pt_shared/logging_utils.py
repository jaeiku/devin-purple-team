"""Structured JSON logging that also persists events to the shared store.

Every service logs to stdout as single-line JSON (easy to ship to a SIEM) and
mirrors the same record into the ``events`` table so the purple-team dashboard
can render a live event feed without a log pipeline.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import Any

from .models import Event

_CONFIGURED: set[str] = set()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": getattr(record, "service", record.name),
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data:
            payload["data"] = data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(service: str) -> logging.Logger:
    logger = logging.getLogger(service)
    if service not in _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _CONFIGURED.add(service)
    return logger


def log_event(
    service: str,
    event: str,
    message: str,
    *,
    level: str = "info",
    data: dict[str, Any] | None = None,
    db: Any | None = None,
) -> None:
    """Emit a structured log line and persist it to the shared event store.

    ``db`` may be an active SQLAlchemy session; when omitted a short-lived one
    is opened. Persistence failures never break the calling service.
    """

    logger = get_logger(service)
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        message,
        extra={"service": service, "event": event, "data": data or {}},
    )

    row = Event(
        service=service,
        level=level,
        event=event,
        message=message,
        data=data or {},
    )
    try:
        if db is not None:
            db.add(row)
            db.flush()
        else:
            from .db import session_scope

            with session_scope() as session:
                session.add(row)
    except Exception:  # pragma: no cover - logging must never raise
        if db is not None:
            # Keep the caller's session usable: a failed flush leaves it in a
            # state where every subsequent query raises.
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(
            "failed to persist event",
            extra={"service": service, "event": "event_persist_failed", "data": {}},
        )
