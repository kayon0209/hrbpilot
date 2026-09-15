"""Agent task projection tables for the MCP intent-to-task gateway.

Revision ID: 043_agent_tasks
Revises: 042_mcp_security_hardening

Two tables, mirroring the app models in ``app/data/models/agent_task.py``:

- ``agent_tasks``: server-side projection of one external-agent intent
  (frozen draft → confirmation → approval → execution), bound to the
  requesting client/installation so revocation covers in-flight tasks;
- ``agent_task_events``: append-only, per-task monotonic event log.

RLS mirrors the HR case tables: tenant is always known when these rows are
written (the principal exists before any task row can be created), so tenant
isolation is both effective and required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "043_agent_tasks"
down_revision: str | None = "042_mcp_security_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASKS = "agent_tasks"
_EVENTS = "agent_task_events"


def _grant_to_app_role(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrbp') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO hrbp';
            END IF;
        END
        $$;
        """
    )


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation_{table} ON {table} USING (tenant_id = current_setting('app.tenant_id'))"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _disable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        _TASKS,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("requester_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("installation_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("source_surface", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("goal_summary", sa.String(length=500), nullable=False),
        sa.Column("interpreted_goal", sa.String(length=500), nullable=True),
        sa.Column("canonical_params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("risk_level", sa.String(length=10), nullable=False, server_default="medium"),
        sa.Column("risk_reasons_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("missing_fields_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("planned_steps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("approval_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("case_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("work_task_id", sa.String(length=36), nullable=True),
        sa.Column("async_task_id", sa.String(length=36), nullable=True),
        sa.Column("submit_idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=50), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'input_required', 'ready_for_confirmation', "
            "'awaiting_approval', 'queued', 'running', 'succeeded', 'failed', "
            "'cancelled', 'expired')",
            name="ck_agent_tasks_status",
        ),
        sa.CheckConstraint("draft_version >= 1", name="ck_agent_tasks_draft_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_tasks_tenant_id"),
    )
    op.create_index(
        "uq_agent_tasks_submit_key",
        _TASKS,
        ["tenant_id", "requester_user_id", "submit_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("submit_idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_agent_tasks_requester_status", _TASKS, ["tenant_id", "requester_user_id", "status"])

    op.create_table(
        _EVENTS,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("task_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("task_id", "seq", name="uq_agent_task_events_task_seq"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [_TASKS + ".tenant_id", _TASKS + ".id"],
            name="fk_agent_task_events_tenant_task",
        ),
    )

    _enable_rls(_TASKS)
    _enable_rls(_EVENTS)
    _grant_to_app_role(_TASKS)
    _grant_to_app_role(_EVENTS)


def downgrade() -> None:
    _disable_rls(_EVENTS)
    _disable_rls(_TASKS)
    op.drop_table(_EVENTS)
    op.drop_index("ix_agent_tasks_requester_status", table_name=_TASKS)
    op.drop_index("uq_agent_tasks_submit_key", table_name=_TASKS)
    op.drop_table(_TASKS)
