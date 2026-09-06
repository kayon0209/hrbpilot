"""Persist ToolExecution links to its grant, outbox message, and lease fence.

Revision ID: 031_tool_dispatch_fence
Revises: 030_runtime_grants_outbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "031_tool_dispatch_fence"
down_revision: str | None = "030_runtime_grants_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_outbox_messages_tenant_id", "outbox_messages", ["tenant_id", "id"])
    op.add_column("tool_executions", sa.Column("execution_grant_id", sa.String(length=36), nullable=True))
    op.add_column("tool_executions", sa.Column("outbox_message_id", sa.String(length=36), nullable=True))
    op.add_column("tool_executions", sa.Column("dispatch_lease_owner", sa.String(length=120), nullable=True))
    op.add_column(
        "tool_executions",
        sa.Column("dispatch_lease_token", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tool_executions",
        sa.Column("dispatch_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tool_executions_dispatch_lease_token",
        "tool_executions",
        "dispatch_lease_token >= 0",
    )
    op.create_foreign_key(
        "fk_tool_executions_tenant_grant",
        "tool_executions",
        "execution_grants",
        ["tenant_id", "execution_grant_id"],
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_tool_executions_tenant_outbox",
        "tool_executions",
        "outbox_messages",
        ["tenant_id", "outbox_message_id"],
        ["tenant_id", "id"],
    )
    op.create_index(
        "ix_tool_executions_execution_grant_id",
        "tool_executions",
        ["execution_grant_id"],
    )
    op.create_index(
        "ix_tool_executions_outbox_message_id",
        "tool_executions",
        ["outbox_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_executions_outbox_message_id", table_name="tool_executions")
    op.drop_index("ix_tool_executions_execution_grant_id", table_name="tool_executions")
    op.drop_constraint("fk_tool_executions_tenant_outbox", "tool_executions", type_="foreignkey")
    op.drop_constraint("fk_tool_executions_tenant_grant", "tool_executions", type_="foreignkey")
    op.drop_constraint("ck_tool_executions_dispatch_lease_token", "tool_executions", type_="check")
    op.drop_column("tool_executions", "dispatch_lease_expires_at")
    op.drop_column("tool_executions", "dispatch_lease_token")
    op.drop_column("tool_executions", "dispatch_lease_owner")
    op.drop_column("tool_executions", "outbox_message_id")
    op.drop_column("tool_executions", "execution_grant_id")
    op.drop_constraint("uq_outbox_messages_tenant_id", "outbox_messages", type_="unique")
