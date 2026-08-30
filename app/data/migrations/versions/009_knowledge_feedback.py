"""Knowledge feedback candidates table (Phase 3 — 经理行动中心).

Revision ID: 009_knowledge_feedback
Revises: 008_token_ledger

A candidate is a SUGGESTION the system derived from real usage signals
(unanswered policy questions, negative feedback, repeated themes). It never
becomes a confirmed knowledge gap on its own — a human (hr_manager) must
confirm, assign, or reject it with a reason (spec §7.7).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009_knowledge_feedback"
down_revision: str | None = "008_token_ledger"
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
        "knowledge_feedback_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("source_type", sa.String(length=30), nullable=False),  # no_evidence | negative_feedback | repeated_theme
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
        sa.Column("evidence_summary", sa.Text, nullable=True),
        sa.Column("suggested_kb_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),  # open | confirmed | rejected | assigned
        sa.Column("handled_by", sa.String(length=36), nullable=True),
        sa.Column("handled_reason", sa.Text, nullable=True),
        sa.Column("assignee", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _add_rls("knowledge_feedback_candidates")


def downgrade() -> None:
    _drop_rls("knowledge_feedback_candidates")
    op.drop_table("knowledge_feedback_candidates")
