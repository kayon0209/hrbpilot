"""Chat message feedback columns.

Revision ID: 005_chat_feedback
Revises: 004_scenario_persistence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_chat_feedback"
down_revision: str | None = "004_scenario_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("feedback_rating", sa.String(length=10), nullable=True))
    op.add_column("chat_messages", sa.Column("feedback_correction", sa.Text(), nullable=True))
    op.add_column("chat_messages", sa.Column("feedback_at", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "feedback_at")
    op.drop_column("chat_messages", "feedback_correction")
    op.drop_column("chat_messages", "feedback_rating")
