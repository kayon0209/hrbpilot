"""Create persistent user-managed work tasks.

Revision ID: 017_persistent_work_tasks
Revises: 016_culture_content_creator
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017_persistent_work_tasks"
down_revision: str | None = "016_culture_content_creator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("waiting_for", sa.String(length=200), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_mode", sa.String(length=10), nullable=False, server_default="stage"),
        sa.Column("completed_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_units", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting', 'completed', 'cancelled')",
            name="ck_work_tasks_status",
        ),
        sa.CheckConstraint(
            "((progress_mode = 'stage' AND total_units IS NULL AND completed_units = 0) "
            "OR (progress_mode = 'units' AND total_units > 0 "
            "AND completed_units >= 0 AND completed_units <= total_units))",
            name="ck_work_tasks_truthful_progress",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_work_tasks_created_by"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_work_tasks_owner"),
        # Composite parent FK: a child can only reference a parent in the SAME
        # tenant. A single-column self FK would let any writer that bypasses
        # the service layer bind tasks across tenants (independent review P0).
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_task_id"],
            ["work_tasks.tenant_id", "work_tasks.id"],
            name="fk_work_tasks_tenant_parent",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_work_tasks_tenant_id"),
    )
    for column in ("tenant_id", "created_by", "owner_user_id", "parent_task_id"):
        op.create_index(f"ix_work_tasks_{column}", "work_tasks", [column])
    op.execute("ALTER TABLE work_tasks ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY work_tasks_tenant_isolation ON work_tasks "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE work_tasks FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("work_tasks")
