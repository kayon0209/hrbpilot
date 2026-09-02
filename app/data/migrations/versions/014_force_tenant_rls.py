"""Force RLS for business tenant tables owned by the application role.

Revision ID: 014_force_tenant_rls
Revises: 013_schema_drift_repair
"""

from collections.abc import Sequence

from alembic import op

revision: str = "014_force_tenant_rls"
down_revision: str | None = "013_schema_drift_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    "agent_runs",
    "approval_requests",
    "audit_logs",
    "case_events",
    "case_plans",
    "chat_sessions",
    "culture_contents",
    "data_sources",
    "employee_requests",
    "eval_results",
    "hr_cases",
    "insight_reports",
    "interview_digests",
    "knowledge_feedback_candidates",
    "manager_org_scopes",
    "org_units",
    "token_ledger",
    "tool_executions",
    "weekly_reports",
)


def upgrade() -> None:
    # ``users`` is deliberately excluded: the login flow performs an
    # authentication lookup before tenant context exists. It needs a separate,
    # tightly scoped identity-resolution design rather than an owner bypass.
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
