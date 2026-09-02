"""Persist local-only WeCom protocol simulator delivery attempts.

Revision ID: 030_wecom_outbound_simulator
Revises: 029_wecom_callback_config

This is an outbox for local protocol simulation only.  It contains no access
token and does not grant or imply real WeCom outbound authorization.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_wecom_outbound_simulator"
down_revision: str | None = "029_wecom_callback_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A composite FK may reference only a matching unique key.  The existing
    # primary key is id alone, so add the tenant-scoped parent key first.
    op.execute("ALTER TABLE employee_requests NO FORCE ROW LEVEL SECURITY")
    try:
        op.create_unique_constraint(
            "uq_employee_requests_tenant_id", "employee_requests", ["tenant_id", "id"]
        )
    finally:
        op.execute("ALTER TABLE employee_requests FORCE ROW LEVEL SECURITY")

    op.create_table(
        "connector_delivery_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("employee_request_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False, server_default="wecom_simulator"),
        sa.Column("recipient_ref", sa.String(length=255), nullable=False),
        sa.Column("message_content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_msgid", sa.String(length=255), nullable=True),
        sa.Column("provider_errcode", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'simulated_accepted', 'retryable_failed', 'rejected')",
            name="ck_connector_delivery_attempt_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_connector_delivery_attempt_count"),
        sa.UniqueConstraint(
            "tenant_id", "employee_request_id", "content_digest",
            name="uq_connector_delivery_attempt_business_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "employee_request_id"],
            ["employee_requests.tenant_id", "employee_requests.id"],
            name="fk_connector_delivery_attempt_tenant_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_delivery_attempt_tenant_source",
        ),
    )
    op.create_index(
        "ix_connector_delivery_attempts_tenant_id", "connector_delivery_attempts", ["tenant_id"]
    )
    op.create_index(
        "ix_connector_delivery_attempts_employee_request_id",
        "connector_delivery_attempts", ["employee_request_id"],
    )
    op.create_index(
        "ix_connector_delivery_attempts_source_id", "connector_delivery_attempts", ["source_id"]
    )
    op.execute("ALTER TABLE connector_delivery_attempts ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY connector_delivery_attempts_tenant_isolation ON connector_delivery_attempts "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE connector_delivery_attempts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS connector_delivery_attempts_tenant_isolation ON connector_delivery_attempts"
    )
    op.execute("ALTER TABLE connector_delivery_attempts NO FORCE ROW LEVEL SECURITY")
    op.drop_index("ix_connector_delivery_attempts_source_id", table_name="connector_delivery_attempts")
    op.drop_index("ix_connector_delivery_attempts_employee_request_id", table_name="connector_delivery_attempts")
    op.drop_index("ix_connector_delivery_attempts_tenant_id", table_name="connector_delivery_attempts")
    op.drop_table("connector_delivery_attempts")

    op.execute("ALTER TABLE employee_requests NO FORCE ROW LEVEL SECURITY")
    try:
        op.drop_constraint("uq_employee_requests_tenant_id", "employee_requests", type_="unique")
    finally:
        op.execute("ALTER TABLE employee_requests FORCE ROW LEVEL SECURITY")
