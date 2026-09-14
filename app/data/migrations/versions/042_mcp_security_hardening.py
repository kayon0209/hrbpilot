"""Harden external MCP identity and client revocation.

Revision ID: 042_mcp_security_hardening
Revises: 041_oauth_client_tenant
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "042_mcp_security_hardening"
down_revision: str | None = "041_oauth_client_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column(
        "oauth_authorization_codes", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("oauth_tokens", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("oauth_clients", sa.Column("status", sa.String(length=16), nullable=False, server_default="active"))
    op.create_table(
        "oauth_client_blocks",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("blocked_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "client_id"),
    )
    op.create_index("ix_oauth_client_blocks_client", "oauth_client_blocks", ["client_id"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION bump_user_auth_version()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.hashed_password IS DISTINCT FROM OLD.hashed_password
             OR NEW.role IS DISTINCT FROM OLD.role
             OR NEW.is_active IS DISTINCT FROM OLD.is_active THEN
            NEW.auth_version := OLD.auth_version + 1;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_users_bump_auth_version BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION bump_user_auth_version()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_users_bump_auth_version ON users")
    op.execute("DROP FUNCTION IF EXISTS bump_user_auth_version()")
    op.drop_index("ix_oauth_client_blocks_client", table_name="oauth_client_blocks")
    op.drop_table("oauth_client_blocks")
    op.drop_column("oauth_clients", "status")
    op.drop_column("oauth_tokens", "auth_version")
    op.drop_column("oauth_authorization_codes", "auth_version")
    op.drop_column("users", "is_active")
    op.drop_column("users", "auth_version")
