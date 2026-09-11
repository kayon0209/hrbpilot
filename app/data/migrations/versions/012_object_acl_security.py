"""Object ownership, organisation scopes, and security policy repair.

Revision ID: 012_object_acl_security
Revises: 011_data_sources

Existing work rows cannot be attributed safely, so ownership columns remain
NULL for historical rows and all readers fail closed on NULL.  The migration
also removes credential blobs produced by the retired placeholder cipher.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012_object_acl_security"
down_revision: str | None = "011_data_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.create_table(
        "org_units",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["parent_id"], ["org_units.id"], name="fk_org_units_parent_id"),
    )
    op.create_index("ix_org_units_parent_id", "org_units", ["parent_id"])

    op.add_column("users", sa.Column("org_unit_id", sa.String(length=36), nullable=True))
    op.create_index("ix_users_org_unit_id", "users", ["org_unit_id"])
    op.create_foreign_key("fk_users_org_unit_id", "users", "org_units", ["org_unit_id"], ["id"])

    op.create_table(
        "manager_org_scopes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("manager_user_id", sa.String(length=36), nullable=False),
        sa.Column("org_unit_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"], name="fk_manager_org_scopes_manager"),
        sa.ForeignKeyConstraint(["org_unit_id"], ["org_units.id"], name="fk_manager_org_scopes_org_unit"),
        sa.UniqueConstraint("tenant_id", "manager_user_id", "org_unit_id", name="uq_manager_org_scope"),
    )
    op.create_index("ix_manager_org_scopes_manager_user_id", "manager_org_scopes", ["manager_user_id"])
    op.create_index("ix_manager_org_scopes_org_unit_id", "manager_org_scopes", ["org_unit_id"])

    op.add_column("async_tasks", sa.Column("created_by", sa.String(length=36), nullable=True))
    op.create_index("ix_async_tasks_created_by", "async_tasks", ["created_by"])
    op.create_foreign_key("fk_async_tasks_created_by", "async_tasks", "users", ["created_by"], ["id"])

    op.add_column("weekly_reports", sa.Column("created_by", sa.String(length=36), nullable=True))
    op.create_index("ix_weekly_reports_created_by", "weekly_reports", ["created_by"])
    op.create_foreign_key("fk_weekly_reports_created_by", "weekly_reports", "users", ["created_by"], ["id"])

    # Placeholder-XOR material is unsafe and cannot be migrated to a KMS key.
    op.execute("UPDATE data_sources SET credential_encrypted = NULL, credential_ref = NULL")

    for table in (
        "hr_cases",
        "case_events",
        "case_plans",
        "approval_requests",
        "tool_executions",
        "agent_runs",
        "token_ledger",
        "knowledge_feedback_candidates",
        "employee_requests",
        "data_sources",
        "org_units",
        "manager_org_scopes",
    ):
        _replace_rls(table)


def downgrade() -> None:
    op.drop_constraint("fk_weekly_reports_created_by", "weekly_reports", type_="foreignkey")
    op.drop_index("ix_weekly_reports_created_by", table_name="weekly_reports")
    op.drop_column("weekly_reports", "created_by")

    op.drop_constraint("fk_async_tasks_created_by", "async_tasks", type_="foreignkey")
    op.drop_index("ix_async_tasks_created_by", table_name="async_tasks")
    op.drop_column("async_tasks", "created_by")

    op.drop_table("manager_org_scopes")
    op.drop_constraint("fk_users_org_unit_id", "users", type_="foreignkey")
    op.drop_index("ix_users_org_unit_id", table_name="users")
    op.drop_column("users", "org_unit_id")
    op.drop_table("org_units")
