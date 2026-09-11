"""Tenant context for HR Case agent read tools.

The legacy agent loop (``agent_loop.run_plan``) executes read tools through a
process-global registry whose executor signature is ``(params) -> dict`` — there
is no place to pass the tenant.  Read executors nevertheless need a tenant to
scope retrieval (Milvus + PostgreSQL FTS both filter on ``tenant_id``).

A ContextVar carries the tenant for the duration of one ``run_plan`` call, so a
concurrent run of another tenant can never observe the wrong scope.  The value
is bound by ``run_plan`` and reset in a ``finally`` block.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_tenant: ContextVar[str | None] = ContextVar("hr_case_read_tenant", default=None)


def bind_read_tenant(tenant_id: str) -> Token:
    """Bind the tenant for the current task; returns a reset token."""
    return _current_tenant.set(tenant_id)


def reset_read_tenant(token: Token) -> None:
    """Restore the previous tenant binding."""
    _current_tenant.reset(token)


def current_read_tenant() -> str | None:
    return _current_tenant.get()
