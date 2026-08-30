"""Employee request table (Phase 4 — Request 闭环).

Revision ID: 010_employee_request
Revises: 009_knowledge_feedback

A Request is the EMPLOYEE-VISIBLE service contract (spec §5.4) — a separate
visibility model from the internal HRCase. Employees see only desensitized
business status and their next step; risk levels, internal plans and audit
events never appear on this surface. An optional HRCase link exists for HR
triage but is never exposed through the employee API.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010_employee_request"
down_revision: str | None = "009_knowledge_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    op.create_table(
        "employee_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("created_by", sa.String(length=36), nullable=False, index=True),
        sa.Column("request_type", sa.String(length=50), nullable=False),  # policy_check | certificate | process_help | other
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        # employee-visible business status (spec §7.9): 已提交 | 待补充材料 | 处理中 | 已解决
        sa.Column("status", sa.String(length=30), nullable=False, server_default="submitted"),
        sa.Column("next_step_for_employee", sa.Text, nullable=True),
        sa.Column("needs_materials", sa.Text, nullable=True),  # HR 请求员工补充的材料说明
        sa.Column("hr_owner_id", sa.String(length=36), nullable=True),
        sa.Column("hr_note", sa.Text, nullable=True),  # internal note — never returned to employee API
        sa.Column("hr_case_id", sa.String(length=36), nullable=True),  # internal link — never exposed to employee
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _add_rls("employee_requests")


def downgrade() -> None:
    _drop_rls("employee_requests")
    op.drop_table("employee_requests")
