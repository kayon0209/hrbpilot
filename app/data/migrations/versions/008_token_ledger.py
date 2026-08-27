"""Token ledger table (Phase 7).

Revision ID: 008_token_ledger
Revises: 007_hr_case
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_token_ledger"
down_revision: str | None = "007_hr_case"
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
        "token_ledger",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("request_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("agent_run_id", sa.String(length=36), nullable=True),
        sa.Column("scenario_id", sa.String(length=50), nullable=False, server_default="unknown"),
        sa.Column("model", sa.String(length=100), nullable=False, server_default="unknown"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("measured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settlement_state", sa.String(length=12), nullable=False, server_default="SETTLED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_token_ledger_tenant_request"),
    )
    _add_rls("token_ledger")


def downgrade() -> None:
    _drop_rls("token_ledger")
    op.drop_table("token_ledger")
