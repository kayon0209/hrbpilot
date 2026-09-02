"""Add explicit organisation or user ownership to knowledge feedback candidates.

Revision ID: 015_knowledge_feedback_scope
Revises: 014_force_tenant_rls

The migration also persists the normalized ``question_key`` and enforces it
with partial unique indexes so concurrent materialization of the same
tenant/scope/question can only ever insert a single candidate row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015_knowledge_feedback_scope"
down_revision: str | None = "014_force_tenant_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_feedback_candidates",
        sa.Column("org_unit_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "knowledge_feedback_candidates",
        sa.Column("source_user_id", sa.String(length=36), nullable=True),
    )
    # question_key derives from the question text (whitespace-normalized,
    # lower-cased, capped). Rows created before this column cannot have their
    # key reconstructed reliably here — the application writes it from now on,
    # and the NOT NULL default of '' keeps legacy rows out of the unique
    # indexes' partial predicates below.
    op.add_column(
        "knowledge_feedback_candidates",
        sa.Column("question_key", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_foreign_key(
        "fk_knowledge_feedback_candidates_org_unit",
        "knowledge_feedback_candidates",
        "org_units",
        ["org_unit_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_knowledge_feedback_candidates_source_user",
        "knowledge_feedback_candidates",
        "users",
        ["source_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_knowledge_feedback_candidates_org_unit_id",
        "knowledge_feedback_candidates",
        ["org_unit_id"],
    )
    op.create_index(
        "ix_knowledge_feedback_candidates_source_user_id",
        "knowledge_feedback_candidates",
        ["source_user_id"],
    )
    op.create_check_constraint(
        "ck_knowledge_feedback_candidate_scope_not_ambiguous",
        "knowledge_feedback_candidates",
        "NOT (org_unit_id IS NOT NULL AND source_user_id IS NOT NULL)",
    )
    # Partial unique indexes: one candidate per tenant/org/question and one per
    # tenant/source-user/question. Legacy rows (both scope columns NULL) stay
    # outside both predicates and remain quarantined by application filters.
    op.create_index(
        "uq_knowledge_feedback_candidates_org_question",
        "knowledge_feedback_candidates",
        ["tenant_id", "org_unit_id", "question_key"],
        unique=True,
        postgresql_where=sa.text("org_unit_id IS NOT NULL AND question_key <> ''"),
    )
    op.create_index(
        "uq_knowledge_feedback_candidates_user_question",
        "knowledge_feedback_candidates",
        ["tenant_id", "source_user_id", "question_key"],
        unique=True,
        postgresql_where=sa.text("source_user_id IS NOT NULL AND question_key <> ''"),
    )
    # Existing rows deliberately remain unscoped. Their provenance cannot be
    # reconstructed safely, so application queries quarantine them instead of
    # guessing an organisation or owner.


def downgrade() -> None:
    op.drop_index(
        "uq_knowledge_feedback_candidates_user_question",
        table_name="knowledge_feedback_candidates",
    )
    op.drop_index(
        "uq_knowledge_feedback_candidates_org_question",
        table_name="knowledge_feedback_candidates",
    )
    op.drop_constraint(
        "ck_knowledge_feedback_candidate_scope_not_ambiguous",
        "knowledge_feedback_candidates",
        type_="check",
    )
    op.drop_index(
        "ix_knowledge_feedback_candidates_source_user_id",
        table_name="knowledge_feedback_candidates",
    )
    op.drop_index(
        "ix_knowledge_feedback_candidates_org_unit_id",
        table_name="knowledge_feedback_candidates",
    )
    op.drop_constraint(
        "fk_knowledge_feedback_candidates_source_user",
        "knowledge_feedback_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_knowledge_feedback_candidates_org_unit",
        "knowledge_feedback_candidates",
        type_="foreignkey",
    )
    op.drop_column("knowledge_feedback_candidates", "source_user_id")
    op.drop_column("knowledge_feedback_candidates", "org_unit_id")
    op.drop_column("knowledge_feedback_candidates", "question_key")
