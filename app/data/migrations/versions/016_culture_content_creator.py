"""Add creator ownership to culture content.

Revision ID: 016_culture_content_creator
Revises: 015_knowledge_feedback_scope
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016_culture_content_creator"
down_revision: str | None = "015_knowledge_feedback_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The application-owner migration connection has no tenant setting. FK
    # validation scans the existing table and would otherwise evaluate the
    # forced tenant policy. Keep the policy enabled and only suspend FORCE
    # inside this transactional DDL operation.
    op.execute("ALTER TABLE culture_contents NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "culture_contents",
        sa.Column("created_by", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_culture_contents_created_by",
        "culture_contents",
        "users",
        ["created_by"],
        ["id"],
    )
    op.create_index("ix_culture_contents_created_by", "culture_contents", ["created_by"])
    op.execute("ALTER TABLE culture_contents FORCE ROW LEVEL SECURITY")
    # Legacy rows remain NULL and are quarantined by ACL queries. Their
    # creator cannot be reconstructed reliably from existing data.


def downgrade() -> None:
    op.execute("ALTER TABLE culture_contents NO FORCE ROW LEVEL SECURITY")
    op.drop_index("ix_culture_contents_created_by", table_name="culture_contents")
    op.drop_constraint(
        "fk_culture_contents_created_by",
        "culture_contents",
        type_="foreignkey",
    )
    op.drop_column("culture_contents", "created_by")
    op.execute("ALTER TABLE culture_contents FORCE ROW LEVEL SECURITY")
