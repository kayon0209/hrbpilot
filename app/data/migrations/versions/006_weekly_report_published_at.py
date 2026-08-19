"""Add published_at to weekly reports.

Revision ID: 006_weekly_report_published_at
Revises: 005_chat_feedback
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_weekly_report_published_at"
down_revision: str | None = "005_chat_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("weekly_reports", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("weekly_reports", "published_at")
