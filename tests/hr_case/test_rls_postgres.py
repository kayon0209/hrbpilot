"""Database-level assurance that application-owned HR data cannot bypass RLS."""

import os

import pytest
from sqlalchemy import text

from app.data.database import get_engine

_TABLES_REQUIRING_FORCED_RLS = frozenset(
    {
        "agent_runs",
        "approval_requests",
        "async_tasks",
        "audit_logs",
        "case_events",
        "case_plans",
        "chat_sessions",
        "connector_identity_bindings",
        "connector_intake_events",
        "culture_contents",
        "data_sources",
        "document_chunks",
        "documents",
        "employee_requests",
        "eval_results",
        "hr_cases",
        "insight_reports",
        "interview_digests",
        "knowledge_bases",
        "knowledge_feedback_candidates",
        "manager_org_scopes",
        "org_units",
        "token_ledger",
        "tool_executions",
        "weekly_reports",
    }
)


@pytest.mark.asyncio
async def test_tenant_tables_force_row_level_security() -> None:
    """Removing FORCE RLS lets the app's table-owner role bypass tenant policy."""
    if not os.environ.get("HRBP_RUN_DB_SECURITY_TESTS"):
        pytest.skip("set HRBP_RUN_DB_SECURITY_TESTS=true for PostgreSQL RLS verification")

    async with get_engine().connect() as connection:
        rows = await connection.execute(
            text("SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY(:names) ORDER BY relname"),
            {"names": list(_TABLES_REQUIRING_FORCED_RLS)},
        )

    states = dict(rows.all())
    assert set(states) == _TABLES_REQUIRING_FORCED_RLS
    assert {name for name, forced in states.items() if not forced} == set()
