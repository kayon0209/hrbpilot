"""HR Case state machine (Phase 4) — the single source of truth.

The API layer may never set ``HRCase.status`` directly; all transitions go
through :func:`transition`, which validates against this table and raises
:class:`InvalidTransition` otherwise.

Flow (upgrade plan Phase 4)::

    NEW → TRIAGED
    TRIAGED → EVIDENCE_READY | NEEDS_CLARIFICATION | HUMAN_REVIEW_REQUIRED
    EVIDENCE_READY → PLAN_READY
    NEEDS_CLARIFICATION → TRIAGED                (clarification answered)
    HUMAN_REVIEW_REQUIRED → HANDED_OFF | TRIAGED (reviewer redirects)
    PLAN_READY → AWAITING_APPROVAL
    AWAITING_APPROVAL → EXECUTING | PLAN_READY   (rejected → back to plan)
    EXECUTING → RESOLVED | FAILED
    FAILED → EXECUTING                           (safe retry)
    any → HANDED_OFF                             (bounded agent gives up)
"""

from app.shared.errors import AppError

NEW = "NEW"
TRIAGED = "TRIAGED"
EVIDENCE_READY = "EVIDENCE_READY"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
PLAN_READY = "PLAN_READY"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
EXECUTING = "EXECUTING"
RESOLVED = "RESOLVED"
HANDED_OFF = "HANDED_OFF"
FAILED = "FAILED"

TERMINAL_STATES = frozenset({RESOLVED, HANDED_OFF})


class InvalidTransitionError(AppError):
    """Raised when a status change is not allowed by the state machine."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(
            f"Cannot transition case from {current} to {target}",
            code="INVALID_CASE_TRANSITION",
            status_code=422,
        )
        self.current = current
        self.target = target


TRANSITIONS: dict[str, frozenset[str]] = {
    NEW: frozenset({TRIAGED}),
    TRIAGED: frozenset({EVIDENCE_READY, NEEDS_CLARIFICATION, HUMAN_REVIEW_REQUIRED}),
    EVIDENCE_READY: frozenset({PLAN_READY}),
    NEEDS_CLARIFICATION: frozenset({TRIAGED}),
    HUMAN_REVIEW_REQUIRED: frozenset({HANDED_OFF, TRIAGED}),
    PLAN_READY: frozenset({AWAITING_APPROVAL}),
    AWAITING_APPROVAL: frozenset({EXECUTING, PLAN_READY}),
    EXECUTING: frozenset({RESOLVED, FAILED}),
    FAILED: frozenset({EXECUTING}),
    RESOLVED: frozenset(),
    HANDED_OFF: frozenset(),
}


def transition(current: str, target: str) -> str:
    """Validate and return the target state, or raise InvalidTransition."""
    allowed = TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise InvalidTransitionError(current, target)
    return target
