"""Connector event lifecycle and structured source authorization scope.

Revision ID: 023_event_lifecycle_scope
Revises: 022_work_task_idempotency

``authorized_scope`` is historic administrator-facing prose.  The new JSONB
column is the canonical machine-enforced boundary; old rows deliberately stay
NULL and therefore cannot start a message sync until an administrator records
a structured scope.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023_event_lifecycle_scope"
down_revision: str | None = "022_work_task_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE connector_event_log NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "connector_event_log",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processed"),
    )
    op.add_column(
        "connector_event_log",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("connector_event_log", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("connector_event_log", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_connector_event_log_status",
        "connector_event_log",
        "status IN ('received', 'processing', 'processed', 'failed')",
    )
    op.alter_column("connector_event_log", "status", server_default=None)
    op.execute("ALTER TABLE connector_event_log FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "data_sources",
        sa.Column("authorized_scope_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.drop_column("data_sources", "authorized_scope_json")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE connector_event_log NO FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_connector_event_log_status", "connector_event_log", type_="check")
    op.drop_column("connector_event_log", "last_error")
    op.drop_column("connector_event_log", "failed_at")
    op.drop_column("connector_event_log", "processing_started_at")
    op.drop_column("connector_event_log", "status")
    op.execute("ALTER TABLE connector_event_log FORCE ROW LEVEL SECURITY")
