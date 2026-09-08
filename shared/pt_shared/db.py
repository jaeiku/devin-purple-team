"""Shared datastore engine/session management.

Supports both SQLite (single-file, shared docker volume) and Postgres via
``DATABASE_URL``. ``init_db`` is safe to call from every service on startup.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlparse

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_SCHEMA_LOCK = 0x70757270  # advisory lock id guarding schema creation

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        path = urlparse(url).path
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - glue
            cursor = dbapi_conn.cursor()
            # WAL keeps concurrent readers (dashboard) from blocking writers.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return engine

    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _SessionLocal


def _create_all(engine: Engine) -> None:
    """Create tables under a lock so parallel service startups don't race."""

    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            # All three services call create_all at once against a fresh
            # database; without serialisation they collide creating enums.
            conn.exec_driver_sql(
                "SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,)
            )
        Base.metadata.create_all(conn)
        preparer = engine.dialect.identifier_preparer
        for table in Base.metadata.sorted_tables:
            existing = {
                column["name"] for column in inspect(conn).get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing:
                    continue
                type_sql = column.type.compile(dialect=engine.dialect)
                conn.exec_driver_sql(
                    f"ALTER TABLE {preparer.quote(table.name)} "
                    f"ADD COLUMN {preparer.quote(column.name)} {type_sql}"
                )


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create tables, waiting for the database to accept connections."""

    last_error: Exception | None = None
    for _ in range(retries):
        try:
            _create_all(get_engine())
            return
        except (OperationalError, DBAPIError) as exc:  # DB still starting up.
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"database unavailable: {last_error}")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope around a series of operations."""

    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""

    with session_scope() as session:
        yield session
