"""Data source registry (Phase 5 — 数据接入).

Revision ID: 011_data_sources
Revises: 010_employee_request

The unified ingestion contract (spec §五 Data Source): every external channel
the tenant connects registers here with its purpose, authorized scope, sync
state and business-language fields. Admins answer 接了什么 / 取了什么 /
去了哪里 / 如何撤销 from this one table. Credentials are never stored in
plaintext: only an encrypted blob or an external key-manager reference
(`credential_ref`) lives here (spec §10.4).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011_data_sources"
down_revision: str | None = "010_employee_request"
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
        "data_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),  # business-language display name
        sa.Column("platform", sa.String(length=50), nullable=False),  # wecom | feishu | dingtalk | wps365 | exchange | oa | hris
        sa.Column("purpose", sa.Text, nullable=False),  # 用途 — shown to admin before authorization
        sa.Column("authorized_scope", sa.Text, nullable=False),  # 授权范围: which folders/chats/rules
        sa.Column("content_types", sa.Text, nullable=False),  # JSON: documents | messages | approvals | attachments...
        sa.Column("data_destination", sa.Text, nullable=False),  # 数据去向: which scenario tables / workspaces
        # 4-level certification (spec §10.3): only level 4 may be called 可使用
        sa.Column("certification_level", sa.Integer, nullable=False, server_default="1"),  # 1..4
        sa.Column("sync_status", sa.String(length=20), nullable=False, server_default="never_run"),  # never_run | ok | failed | paused
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("paused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text, nullable=True),
        sa.Column("credential_ref", sa.String(length=500), nullable=True),  # encrypted blob ref or KMS key id — never the secret itself
        sa.Column("credential_encrypted", sa.LargeBinary, nullable=True),  # tenant-key encrypted credential blob
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _add_rls("data_sources")


def downgrade() -> None:
    _drop_rls("data_sources")
    op.drop_table("data_sources")
