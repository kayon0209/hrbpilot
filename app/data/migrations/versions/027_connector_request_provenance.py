"""Link platform-created employee requests to their immutable source event.

Revision ID: 027_connector_request_provenance
Revises: 026_connector_pending_intake
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027_connector_request_provenance"
down_revision: str | None = "026_connector_pending_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE employee_requests NO FORCE ROW LEVEL SECURITY")
    op.add_column("employee_requests", sa.Column("connector_source_id", sa.String(length=36), nullable=True))
    op.add_column(
        "employee_requests", sa.Column("connector_external_event_id", sa.String(length=255), nullable=True)
    )
    op.add_column("employee_requests", sa.Column("external_sender_id", sa.String(length=255), nullable=True))
    op.create_check_constraint(
        "ck_employee_request_connector_provenance",
        "employee_requests",
        "(connector_source_id IS NULL AND connector_external_event_id IS NULL AND external_sender_id IS NULL) "
        "OR (connector_source_id IS NOT NULL AND connector_external_event_id IS NOT NULL AND external_sender_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_employee_request_connector_event",
        "employee_requests",
        "connector_event_log",
        ["tenant_id", "connector_source_id", "connector_external_event_id"],
        ["tenant_id", "source_id", "external_event_id"],
    )
    op.create_index(
        "uq_employee_requests_connector_event",
        "employee_requests",
        ["tenant_id", "connector_source_id", "connector_external_event_id"],
        unique=True,
        postgresql_where=sa.text("connector_source_id IS NOT NULL"),
    )
    op.execute("ALTER TABLE employee_requests FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE employee_requests NO FORCE ROW LEVEL SECURITY")
    op.drop_index("uq_employee_requests_connector_event", table_name="employee_requests")
    op.drop_constraint("fk_employee_request_connector_event", "employee_requests", type_="foreignkey")
    op.drop_constraint("ck_employee_request_connector_provenance", "employee_requests", type_="check")
    op.drop_column("employee_requests", "external_sender_id")
    op.drop_column("employee_requests", "connector_external_event_id")
    op.drop_column("employee_requests", "connector_source_id")
    op.execute("ALTER TABLE employee_requests FORCE ROW LEVEL SECURITY")
