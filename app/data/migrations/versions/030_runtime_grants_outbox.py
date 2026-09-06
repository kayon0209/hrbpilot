"""Create the governed-runtime grant and transactional-outbox records.

These tables are tenant-scoped and persist only execution references and
digests.  They never contain raw prompts, employee materials, or model output.

Revision ID: 030_runtime_grants_outbox
Revises: 029_eval_result_is_stub
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_runtime_grants_outbox"
down_revision: str | None = "029_eval_result_is_stub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)


def _add_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "execution_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("capability", sa.String(length=120), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=120), nullable=False),
        sa.Column("allowed_tool", sa.String(length=120), nullable=False),
        sa.Column("allowed_action", sa.String(length=120), nullable=False),
        sa.Column("normalized_params_hash", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=120), nullable=False),
        sa.Column("permissions_version", sa.String(length=120), nullable=False),
        sa.Column("issued_for_run_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_consumptions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consumption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        *_TIMESTAMPS,
        sa.CheckConstraint("status IN ('active', 'consumed', 'revoked', 'expired')", name="ck_execution_grants_status"),
        sa.CheckConstraint("max_consumptions >= 1", name="ck_execution_grants_max_consumptions"),
        sa.CheckConstraint(
            "consumption_count >= 0 AND consumption_count <= max_consumptions",
            name="ck_execution_grants_consumption_count",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_execution_grants_tenant_id"),
    )
    op.create_index("ix_execution_grants_claimable", "execution_grants", ["tenant_id", "status", "expires_at"])
    _add_rls("execution_grants")

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("queue_name", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("payload_ref", sa.Text(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        *_TIMESTAMPS,
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'delivered', 'terminal', 'dead_letter')",
            name="ck_outbox_messages_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_messages_attempt_count"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_messages_tenant_dedupe"),
    )
    op.create_index(
        "ix_outbox_messages_claimable",
        "outbox_messages",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    _add_rls("outbox_messages")


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("execution_grants")
