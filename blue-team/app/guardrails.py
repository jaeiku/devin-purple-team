"""Budget and concurrency guardrails enforced before any session is created.

Three independent checks, evaluated in order, all backed by the shared store:

1. **Idempotency** -- one Devin session per GitHub issue, ever. The key is
   derived from the repo + issue number, so retries, workflow re-runs and
   duplicate webhooks all collapse onto the same record.
2. **Concurrency** -- at most ``MAX_CONCURRENT_SESSIONS`` sessions in a
   non-terminal state at any time.
3. **Budget** -- a new session is admitted only if already-incurred spend plus
   the unspent caps of in-flight sessions plus this session's own cap stays
   within ``GLOBAL_BUDGET_ACU_CEILING``. Reserving the cap up front (rather
   than charging only what has been spent) is what makes the ceiling a hard
   ceiling instead of an after-the-fact observation.
"""

from __future__ import annotations

from dataclasses import dataclass

from pt_shared.config import Settings
from pt_shared.store import active_session_count, committed_acu
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    code: str
    acu_reserved: float = 0.0
    active_sessions: int = 0
    acu_committed: float = 0.0
    acu_ceiling: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "code": self.code,
            "acu_reserved": self.acu_reserved,
            "active_sessions": self.active_sessions,
            "acu_committed": self.acu_committed,
            "acu_ceiling": self.acu_ceiling,
        }


def idempotency_key(repo: str, issue_number: int) -> str:
    return f"{repo}#issue-{issue_number}"


def evaluate(db: Session, settings: Settings) -> Decision:
    """Decide whether a new remediation session may be created right now."""

    active = active_session_count(db)
    committed = committed_acu(db)
    reserve = float(settings.max_acu_per_session)
    ceiling = float(settings.global_budget_acu_ceiling)

    if active >= settings.max_concurrent_sessions:
        return Decision(
            allowed=False,
            code="concurrency_limit",
            reason=(
                f"{active} session(s) already in flight, limit is "
                f"{settings.max_concurrent_sessions}; queued for retry"
            ),
            active_sessions=active,
            acu_committed=committed,
            acu_ceiling=ceiling,
        )

    if committed + reserve > ceiling:
        return Decision(
            allowed=False,
            code="budget_ceiling",
            reason=(
                f"committed ACU {committed:.1f} + reserve {reserve:.1f} would "
                f"exceed the global ceiling of {ceiling:.1f}"
            ),
            acu_reserved=reserve,
            active_sessions=active,
            acu_committed=committed,
            acu_ceiling=ceiling,
        )

    return Decision(
        allowed=True,
        code="ok",
        reason="within concurrency and budget limits",
        acu_reserved=reserve,
        active_sessions=active,
        acu_committed=committed,
        acu_ceiling=ceiling,
    )
