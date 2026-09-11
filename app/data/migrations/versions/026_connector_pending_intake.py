"""Retain unbound HR platform messages for verified identity reconciliation.

Revision ID: 026_connector_pending_intake
Revises: 025_connector_identity_binding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "026_connector_pending_intake"
down_revision: str | None = "025_connector_identity_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_intake_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("source_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_identity"),
        sa.Column("employee_request_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending_identity', 'materialized')",
            name="ck_connector_intake_event_status",
        ),
        sa.UniqueConstraint("tenant_id", "source_id", "external_event_id", name="uq_connector_intake_event"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "external_event_id"],
            [
                "connector_event_log.tenant_id",
                "connector_event_log.source_id",
                "connector_event_log.external_event_id",
            ],
            name="fk_connector_intake_event_source_event",
        ),
    )
    op.execute("ALTER TABLE connector_intake_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY connector_intake_events_tenant_isolation ON connector_intake_events "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE connector_intake_events FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("connector_intake_events")
