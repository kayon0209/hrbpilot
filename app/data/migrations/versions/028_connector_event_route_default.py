"""Restore the database default for legacy/raw data-source writers.

Revision ID: 028_event_route_default
Revises: 027_connector_request_provenance
"""

from collections.abc import Sequence

from alembic import op

revision: str = "028_event_route_default"
down_revision: str | None = "027_connector_request_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.alter_column("data_sources", "event_route", server_default="none")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # The default is part of the public data-source write contract.  Retain it
    # when rolling back the no-op repair revision; 024 removes the column.
    pass
