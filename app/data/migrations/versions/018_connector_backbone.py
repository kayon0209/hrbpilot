"""Connector backbone: encrypted OAuth credentials, sync cursors, event log.

Revision ID: 018_connector_backbone
Revises: 017_persistent_work_tasks

Extends data_sources with OAuth state and tenant-encrypted credentials, and
adds two RLS-protected tables:

- connector_sync_cursors: per-(tenant, source, stream) incremental cursors so
  syncs resume exactly where they stopped — no full refetch, no gaps.
- connector_event_log: every consumed external event recorded by
  (tenant_id, source_id, external_event_id) with a UNIQUE index — idempotent
  consumption: a redelivered webhook or a replayed sync can never trigger the
  side effect twice.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018_connector_backbone"
down_revision: str | None = "017_persistent_work_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- data_sources: OAuth state + encrypted credential ---
    op.add_column(
        "data_sources",
        sa.Column("oauth_state", sa.String(length=20), nullable=False, server_default="none"),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_app_id", sa.String(length=128), nullable=True),
    )
    # none | pending | connected | expired | revoked
    op.add_column(
        "data_sources",
        sa.Column("oauth_encrypted_token", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_refresh_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_connected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_scopes", sa.Text(), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("oauth_user_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "data_sources",
        sa.Column("credential_encrypted_v2", sa.LargeBinary(), nullable=True),
    )
    op.create_check_constraint(
        "ck_data_sources_oauth_state",
        "data_sources",
        "oauth_state IN ('none', 'pending', 'connected', 'expired', 'revoked')",
    )

    # --- connector_sync_cursors ---
    op.create_table(
        "connector_sync_cursors",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("stream", sa.String(length=100), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_id", "stream", name="uq_connector_cursor_scope"),
    )
    op.create_index("ix_connector_sync_cursors_source_id", "connector_sync_cursors", ["source_id"])
    op.create_index("ix_connector_sync_cursors_tenant_id", "connector_sync_cursors", ["tenant_id"])
    op.execute("ALTER TABLE connector_sync_cursors ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY connector_sync_cursors_tenant_isolation ON connector_sync_cursors "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE connector_sync_cursors FORCE ROW LEVEL SECURITY")

    # --- connector_event_log ---
    op.create_table(
        "connector_event_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_id", "external_event_id", name="uq_connector_event_consumed"),
    )
    op.create_index(
        "ix_connector_event_log_source_id",
        "connector_event_log",
        ["source_id"],
    )
    op.create_index(
        "ix_connector_event_log_tenant_id",
        "connector_event_log",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE connector_event_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY connector_event_log_tenant_isolation ON connector_event_log "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE connector_event_log FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS connector_event_log_tenant_isolation ON connector_event_log")
    op.execute("ALTER TABLE connector_event_log NO FORCE ROW LEVEL SECURITY")
    op.drop_table("connector_event_log")

    op.execute("DROP POLICY IF EXISTS connector_sync_cursors_tenant_isolation ON connector_sync_cursors")
    op.execute("ALTER TABLE connector_sync_cursors NO FORCE ROW LEVEL SECURITY")
    op.drop_index("ix_connector_sync_cursors_tenant_id", table_name="connector_sync_cursors")
    op.drop_index("ix_connector_sync_cursors_source_id", table_name="connector_sync_cursors")
    op.drop_table("connector_sync_cursors")

    op.drop_constraint("ck_data_sources_oauth_state", "data_sources", type_="check")
    op.drop_column("data_sources", "credential_encrypted_v2")
    op.drop_column("data_sources", "oauth_user_id")
    op.drop_column("data_sources", "oauth_scopes")
    op.drop_column("data_sources", "oauth_connected_at")
    op.drop_column("data_sources", "oauth_expires_at")
    op.drop_column("data_sources", "oauth_refresh_encrypted")
    op.drop_column("data_sources", "oauth_encrypted_token")
    op.drop_column("data_sources", "oauth_app_id")
    op.drop_column("data_sources", "oauth_state")
