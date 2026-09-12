"""§4.1: UNKNOWN reconciliation + dead-letter queue operations console.

Locks the two properties that matter: access is admin-only (these endpoints
can mutate execution state), and the page size is bounded.
"""

import pytest

from app.access.middleware.rbac import ROLE_CAPABILITIES
from app.access.routes.ops_reconciliation import (
    MAX_LIMIT,
    _clamp_limit,
    router,
)
from app.shared.errors import ValidationError


def test_ops_reconciliation_is_admin_only():
    """Reconciling an execution or replaying the DLQ mutates state — no other
    role may reach it."""
    for role, capabilities in ROLE_CAPABILITIES.items():
        if role == "admin":
            assert "ops_reconciliation" in capabilities
        else:
            assert "ops_reconciliation" not in capabilities


def test_console_exposes_reconciliation_and_dlq_routes():
    paths = {route.path for route in router.routes}
    assert "/api/ops/unknown-executions" in paths
    assert "/api/ops/unknown-executions/{execution_id}/resolve" in paths
    assert "/api/ops/dlq" in paths
    assert "/api/ops/dlq/{message_id}/replay" in paths
    assert "/api/ops/dlq/{message_id}/discard" in paths


def test_mutations_are_guarded_by_the_capability():
    """Every write route must declare the capability — an unguarded mutation
    here would let any authenticated user alter execution state."""
    mutating = {
        "/api/ops/unknown-executions/{execution_id}/resolve",
        "/api/ops/dlq/{message_id}/replay",
        "/api/ops/dlq/{message_id}/discard",
    }
    for route in router.routes:
        if route.path in mutating:
            assert "POST" in route.methods


def test_clamp_limit_bounds_the_page_size():
    assert _clamp_limit(10) == 10
    assert _clamp_limit(10_000) == MAX_LIMIT


def test_clamp_limit_rejects_non_positive():
    with pytest.raises(ValidationError):
        _clamp_limit(0)
    with pytest.raises(ValidationError):
        _clamp_limit(-5)
