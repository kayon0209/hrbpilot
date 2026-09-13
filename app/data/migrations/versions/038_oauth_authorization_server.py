"""OAuth 2.1 authorization server — clients, authorization codes, tokens, revocations.

Revision ID: 038_oauth_authorization_server
Revises: 037_chunk_page_number

Creates the persistence for HRBPilot's own authorization server (ADR-0002 §6).
Four tables:

- ``oauth_clients``            registered clients (pre-registered / CIMD / DCR)
- ``oauth_authorization_codes`` one-time, ~60s authorization codes (hashed)
- ``oauth_tokens``             refresh tokens + their rotation chains (hashed)
- ``oauth_revoked_tokens``     revocation list for **stateless** access tokens

Why these four tables deliberately have NO row-level security
-------------------------------------------------------------
The repo's standing rule is "FORCE RLS from day one". These are an explicit,
reasoned exception, not an oversight.

RLS works by comparing ``tenant_id`` against ``current_setting('app.tenant_id')``.
Every OAuth lookup that matters happens at a moment when the tenant is *not yet
known* — that is exactly what is being looked up:

- ``oauth_clients``: a client is a deployment-level object of the AS. CIMD clients
  come from the open internet and have no tenant at all.
- a code / refresh token is looked up **by its secret hash**; the tenant is read
  off the row that comes back.

With RLS forced, ``current_setting('app.tenant_id', true)`` is NULL on those
sessions, ``tenant_id = NULL`` is never true, and every lookup returns zero rows —
the authorization server would be dead on arrival. The only way to "make it work"
would be a policy that allows everything when no tenant is set, which is RLS in
name only. So the protection here is the **unguessable credential itself**:
authorization codes and refresh tokens are 256-bit random values that are only
ever stored as SHA-256 (same practice as ``oauth_nonces.nonce_sha256``), and every
access path is an exact-match lookup on that hash. There is no "scan a range of
rows" path for RLS to protect, which is the class of leak RLS exists to stop.

``tenant_id`` / ``user_id`` are still stored as plain columns for audit and
operational queries — they just do not carry the isolation duty here.

All statements are additive: four new tables, no existing table is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038_oauth_authorization_server"
down_revision: str | None = "037_chunk_page_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _grant_to_app_role(table: str) -> None:
    """Let the non-superuser application role use the new table.

    Migrations run as the database administrator in the container while the API
    and Worker deliberately connect as the ``hrbp`` role; without this grant the
    tables exist but every request fails on permission denied.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrbp') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO hrbp';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=512), primary_key=True),
        sa.Column("client_name", sa.String(length=255), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("grant_types", sa.JSON(), nullable=False),
        sa.Column("response_types", sa.JSON(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("token_endpoint_auth_method", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("registration_source", sa.String(length=32), nullable=False),
        sa.Column("metadata_document_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_oauth_clients_source", "oauth_clients", ["registration_source"])

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource", sa.Text(), nullable=False, server_default=""),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("code_challenge_method", sa.String(length=16), nullable=False, server_default="S256"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_oauth_codes_expires_at", "oauth_authorization_codes", ["expires_at"])
    op.create_index("ix_oauth_codes_client", "oauth_authorization_codes", ["client_id"])

    op.create_table(
        "oauth_tokens",
        sa.Column("jti", sa.String(length=36), primary_key=True),
        sa.Column("token_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("parent_jti", sa.String(length=36), nullable=True),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_oauth_tokens_family", "oauth_tokens", ["family_id"])
    op.create_index("ix_oauth_tokens_client", "oauth_tokens", ["client_id"])
    op.create_index("ix_oauth_tokens_expires_at", "oauth_tokens", ["expires_at"])

    op.create_table(
        "oauth_revoked_tokens",
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("value", sa.String(length=64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_oauth_revoked_expires_at", "oauth_revoked_tokens", ["expires_at"])

    for table in ("oauth_clients", "oauth_authorization_codes", "oauth_tokens", "oauth_revoked_tokens"):
        _grant_to_app_role(table)


def downgrade() -> None:
    op.drop_table("oauth_revoked_tokens")
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
