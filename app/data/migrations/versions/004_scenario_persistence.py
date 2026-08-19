"""Scenario persistence cleanup.

Revision ID: 004_scenario_persistence
Revises: 003_document_integrity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_scenario_persistence"
down_revision: str | None = "003_document_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "interview_digests",
        "document_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "interview_digests",
        "document_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
