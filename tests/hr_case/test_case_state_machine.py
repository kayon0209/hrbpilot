"""HR Case state machine tests (Phase 4).

Locks every legal transition and the rejection of every shortcut — the API
layer must never be able to skip states or resurrect terminal cases.
"""

from itertools import pairwise

import pytest

from app.scenarios.hr_case_agent import state
from app.scenarios.hr_case_agent.state import TRANSITIONS, InvalidTransitionError, transition


def test_happy_path_reaches_resolved():
    path = ["NEW", "TRIAGED", "EVIDENCE_READY", "PLAN_READY", "AWAITING_APPROVAL", "EXECUTING", "RESOLVED"]
    for current, target in pairwise(path):
        assert transition(current, target) == target


def test_clarification_loop():
    assert transition("TRIAGED", "NEEDS_CLARIFICATION") == "NEEDS_CLARIFICATION"
    assert transition("NEEDS_CLARIFICATION", "TRIAGED") == "TRIAGED"


def test_rejection_returns_to_plan_ready():
    assert transition("AWAITING_APPROVAL", "PLAN_READY") == "PLAN_READY"


def test_failed_allows_safe_retry_only():
    assert transition("FAILED", "EXECUTING") == "EXECUTING"
    with pytest.raises(InvalidTransitionError):
        transition("FAILED", "RESOLVED")
    with pytest.raises(InvalidTransitionError):
        transition("FAILED", "NEW")


def test_terminal_states_are_frozen():
    assert state.RESOLVED in state.TERMINAL_STATES
    assert state.HANDED_OFF in state.TERMINAL_STATES
    assert TRANSITIONS[state.RESOLVED] == frozenset()
    assert TRANSITIONS[state.HANDED_OFF] == frozenset()
    with pytest.raises(InvalidTransitionError):
        transition("RESOLVED", "EXECUTING")
    with pytest.raises(InvalidTransitionError):
        transition("HANDED_OFF", "TRIAGED")


def test_state_skipping_is_rejected():
    illegal = [
        ("NEW", "EXECUTING"),
        ("NEW", "RESOLVED"),
        ("TRIAGED", "AWAITING_APPROVAL"),
        ("TRIAGED", "EXECUTING"),
        ("EVIDENCE_READY", "AWAITING_APPROVAL"),
        ("PLAN_READY", "EXECUTING"),
        ("NEEDS_CLARIFICATION", "EXECUTING"),
        ("HUMAN_REVIEW_REQUIRED", "RESOLVED"),
    ]
    for current, target in illegal:
        with pytest.raises(InvalidTransitionError):
            transition(current, target)


def test_unknown_states_rejected():
    with pytest.raises(InvalidTransitionError):
        transition("MADE_UP", "TRIAGED")
    with pytest.raises(InvalidTransitionError):
        transition("NEW", "MADE_UP")
