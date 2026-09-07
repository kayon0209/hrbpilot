"""Persist idempotent in-app notification deliveries for governed tools.

Revision ID: 032_in_app_notifications
Revises: 031_tool_dispatch_fence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032_in_app_notifications"
down_revision: str | None = "031_tool_dispatch_fence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("tool_execution_id", sa.String(length=36), nullable=False),
        sa.Column("template", sa.String(length=80), nullable=False),
        sa.Column("delivery_key", sa.String(length=160), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_in_app_notifications_tenant_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["hr_cases.tenant_id", "hr_cases.id"],
            name="fk_in_app_notifications_tenant_case",
        ),
        sa.UniqueConstraint("tenant_id", "delivery_key", name="uq_in_app_notifications_tenant_delivery"),
    )
    # PostgreSQL requires the referenced columns of a composite FK to be
    # explicitly unique.  ToolExecution predates its tenant-scoped child
    # relations, so establish that parent invariant in the same transaction.
    op.create_unique_constraint("uq_tool_executions_tenant_id", "tool_executions", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_in_app_notifications_tenant_execution",
        "in_app_notifications",
        "tool_executions",
        ["tenant_id", "tool_execution_id"],
        ["tenant_id", "id"],
    )
    # Migrations run under the database administrator in the container, while
    # the API and Worker deliberately use the non-superuser application role.
    # RLS constrains this grant to the current tenant context.  The grant is
    # conditional on that role existing: CI provisions an RLS-bound role with
    # a different name, and the workflow grants it schema-wide privileges.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrbp') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE in_app_notifications TO hrbp';
            END IF;
        END
        $$;
        """
    )
    op.create_index(
        "ix_in_app_notifications_recipient_unread",
        "in_app_notifications",
        ["tenant_id", "recipient_user_id", "read_at"],
    )
    _add_rls("in_app_notifications")


def downgrade() -> None:
    op.drop_table("in_app_notifications")
    op.drop_constraint("uq_tool_executions_tenant_id", "tool_executions", type_="unique")
