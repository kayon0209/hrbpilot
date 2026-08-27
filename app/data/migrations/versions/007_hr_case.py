"""HR Case domain tables (Phase 4).

Creates the six HR Case Agent tables with tenant RLS policies, matching the
pattern of earlier tenant-scoped migrations.

Revision ID: 007_hr_case
Revises: 006_weekly_report_published_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007_hr_case"
down_revision: str | None = "006_weekly_report_published_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("hr_cases", "case_plans", "approval_requests", "tool_executions", "case_events", "agent_runs")

_TIMESTAMPS = (
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)


def _add_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true))"
    )


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    # Order matters: agent_runs is referenced by case_plans / tool_executions.
    op.create_table(
        "hr_cases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("subject_ref", sa.String(length=120), nullable=False, index=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="LOW"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="NEW"),
        sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_TIMESTAMPS,
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("hr_cases.id"), nullable=False, index=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="RUNNING"),
        sa.Column("steps_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("handoff_reason", sa.Text(), nullable=True),
        sa.Column("final_state", sa.Text(), nullable=True),
        *_TIMESTAMPS,
    )

    op.create_table(
        "case_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("hr_cases.id"), nullable=False, index=True),
        sa.Column("agent_run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("risk_notes", sa.Text(), nullable=True),
        sa.Column("estimated_tokens", sa.Integer(), nullable=True),
        *_TIMESTAMPS,
    )

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("hr_cases.id"), nullable=False, index=True),
        sa.Column("plan_id", sa.String(length=36), sa.ForeignKey("case_plans.id"), nullable=True),
        sa.Column("tool_name", sa.String(length=50), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("requested_by", sa.String(length=36), nullable=True),
        sa.Column("approver_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_TIMESTAMPS,
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("hr_cases.id"), nullable=False, index=True),
        sa.Column("approval_id", sa.String(length=36), sa.ForeignKey("approval_requests.id"), nullable=True),
        sa.Column("agent_run_id", sa.String(length=36), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("tool_name", sa.String(length=50), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="RUNNING"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        *_TIMESTAMPS,
        sa.UniqueConstraint("case_id", "request_id", name="uq_tool_executions_case_request"),
    )

    op.create_table(
        "case_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("hr_cases.id"), nullable=False, index=True),
        sa.Column("agent_run_id", sa.String(length=36), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=80), nullable=False),
        *_TIMESTAMPS,
        sa.UniqueConstraint("case_id", "seq", name="uq_case_events_case_seq"),
    )

    for table in TABLES:
        _add_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        _drop_rls(table)
    op.drop_table("case_events")
    op.drop_table("tool_executions")
    op.drop_table("approval_requests")
    op.drop_table("case_plans")
    op.drop_table("agent_runs")
    op.drop_table("hr_cases")
