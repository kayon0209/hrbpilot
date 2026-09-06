"""Explicitly route approved platform events to HR employee requests.

Revision ID: 024_connector_hr_intake
Revises: 023_event_lifecycle_scope

Free-text source descriptions are administrator-facing only.  This finite,
schema-enforced route is the sole opt-in for a connector event to start an HR
workflow.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024_connector_hr_intake"
down_revision: str | None = "023_event_lifecycle_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.add_column(
        "data_sources",
        sa.Column("event_route", sa.String(length=32), nullable=False, server_default="none"),
    )
    op.create_check_constraint(
        "ck_data_sources_event_route",
        "data_sources",
        "event_route IN ('none', 'employee_request')",
    )
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE data_sources NO FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_data_sources_event_route", "data_sources", type_="check")
    op.drop_column("data_sources", "event_route")
    op.execute("ALTER TABLE data_sources FORCE ROW LEVEL SECURITY")
