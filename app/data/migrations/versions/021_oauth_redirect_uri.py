"""Persist the registered OAuth redirect_uri on data sources.

Revision ID: 021_oauth_redirect_uri
Revises: 020_tenant_composite_fk

CONN-06: the admin-registered callback URL must be persisted so that
``oauth-start`` can enforce an allowlist — the consent URL may only embed a
redirect_uri that matches what the administrator registered.  Without the
column the check could never be consistent across requests.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "021_oauth_redirect_uri"
down_revision: str | None = "020_tenant_composite_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "data_sources",
        sa.Column("oauth_redirect_uri", sa.String(length=500), nullable=True),
    )
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.drop_column("data_sources", "oauth_redirect_uri")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")
