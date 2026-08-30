"""Repair historical schema drift without weakening existing constraints.

Revision ID: 013_schema_drift_repair
Revises: 012_object_acl_security
"""

from collections.abc import Sequence

from alembic import op

revision: str = "013_schema_drift_repair"
down_revision: str | None = "012_object_acl_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Migration 007 declares this column, but early deployed databases may
    # have applied a prior copy of that revision.  The conditional repair is
    # a no-op for clean installations and restores the contract for drifted
    # databases without rewriting migration history.
    op.execute("ALTER TABLE approval_requests ADD COLUMN IF NOT EXISTS input_hash VARCHAR(64)")
    op.create_index("ix_interview_digests_document_id", "interview_digests", ["document_id"])
    op.create_index("ix_insight_reports_task_id", "insight_reports", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_insight_reports_task_id", table_name="insight_reports")
    op.drop_index("ix_interview_digests_document_id", table_name="interview_digests")
    # Do not drop approval_requests.input_hash: on clean installations the
    # column belongs to migration 007, not this conditional repair.
