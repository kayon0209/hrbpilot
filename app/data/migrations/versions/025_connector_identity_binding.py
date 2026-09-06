"""Tenant-safe external platform identity bindings for HR intake.

Revision ID: 025_connector_identity_binding
Revises: 024_connector_hr_intake
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "025_connector_identity_binding"
down_revision: str | None = "024_connector_hr_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_identity_bindings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("source_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "source_id", "external_user_id", name="uq_connector_identity_binding"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.id"],
            name="fk_connector_identity_binding_tenant_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_connector_identity_binding_tenant_user",
        ),
    )
    op.execute("ALTER TABLE connector_identity_bindings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY connector_identity_bindings_tenant_isolation ON connector_identity_bindings "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE connector_identity_bindings FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("connector_identity_bindings")
