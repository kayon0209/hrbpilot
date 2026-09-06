"""One-time OAuth CSRF nonce store.

Revision ID: 019_oauth_nonce_csrf
Revises: 018_connector_backbone

``oauth_state`` on data_sources is the business lifecycle field
(none/pending/connected/expired/revoked) and must NOT double as the CSRF
ticket. This migration adds ``oauth_nonces`` — a short-lived, one-time,
tenant/source/actor-bound nonce whose SHA-256 fingerprint is persisted. A
callback only exchanges the authorization code after the nonce validates
and is atomically consumed, so a stolen or replayed ``state`` cannot be
used twice (the plaintext nonce itself is never stored or logged).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "019_oauth_nonce_csrf"
down_revision: str | None = "018_connector_backbone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_nonces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("nonce_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_id", "nonce_sha256", name="uq_oauth_nonce_scope"),
    )
    op.create_index("ix_oauth_nonces_source_id", "oauth_nonces", ["source_id"])
    op.create_index("ix_oauth_nonces_tenant_id", "oauth_nonces", ["tenant_id"])
    op.create_index("ix_oauth_nonces_expires_at", "oauth_nonces", ["expires_at"])
    op.execute("ALTER TABLE oauth_nonces ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY oauth_nonces_tenant_isolation ON oauth_nonces "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE oauth_nonces FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS oauth_nonces_tenant_isolation ON oauth_nonces")
    op.execute("ALTER TABLE oauth_nonces NO FORCE ROW LEVEL SECURITY")
    op.drop_index("ix_oauth_nonces_expires_at", table_name="oauth_nonces")
    op.drop_index("ix_oauth_nonces_tenant_id", table_name="oauth_nonces")
    op.drop_index("ix_oauth_nonces_source_id", table_name="oauth_nonces")
    op.drop_table("oauth_nonces")
