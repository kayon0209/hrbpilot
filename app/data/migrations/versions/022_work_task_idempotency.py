"""Client-generated idempotency key for work-task creation.

Revision ID: 022_work_task_idempotency
Revises: 021_oauth_redirect_uri

FE-04 / TASK-03: a retried "create task" request (double-click, network
retry) must not create duplicate rows.  The client supplies an idempotency
key; a UNIQUE (tenant_id, idempotency_key) partial index turns a duplicate
submission into a no-op that returns the existing row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "022_work_task_idempotency"
down_revision: str | None = "021_oauth_redirect_uri"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE work_tasks NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "work_tasks",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    # Partial: only keys that were actually supplied participate.
    op.create_index(
        "uq_work_tasks_idempotency_key",
        "work_tasks",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.execute("ALTER TABLE work_tasks FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE work_tasks NO FORCE ROW LEVEL SECURITY")
    op.drop_index("uq_work_tasks_idempotency_key", table_name="work_tasks")
    op.drop_column("work_tasks", "idempotency_key")
    op.execute("ALTER TABLE work_tasks FORCE ROW LEVEL SECURITY")
