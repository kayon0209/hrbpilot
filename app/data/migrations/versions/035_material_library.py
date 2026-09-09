"""Material library — employee dimension + interview/voice raw corpus storage.

Revision ID: 035_material_library
Revises: 034_merge_governed_runtime_heads

Adds the missing employee dimension and per-record raw-corpus persistence for
interview digests and voice insight (design doc: docs/data-scale-design-2026-09-09.md).
All three tables FORCE RLS from day one (lesson from 020/032); composite FKs
are created BEFORE RLS is forced so FK validation can see parent rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035_material_library"
down_revision: str | None = "034_merge_governed_runtime_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATERIAL_TABLES = ("employees", "interview_records", "voice_entries")


def _add_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _grant_to_app_role(table: str) -> None:
    # Migrations run under the database administrator in the container, while
    # the API and Worker deliberately use the non-superuser application role.
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
    # --- employees (the missing dimension) ---
    op.create_table(
        "employees",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("employee_no", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_employees_tenant_creator",
        ),
        sa.UniqueConstraint("tenant_id", "employee_no", name="uq_employees_tenant_no"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_employees_tenant_id"),
    )
    op.create_index("ix_employees_tenant_name", "employees", ["tenant_id", "name"])

    # --- interview_records (面谈纪要 raw corpus + task link) ---
    op.create_table(
        "interview_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("employee_id", sa.String(length=36), nullable=True),
        sa.Column("employee_name", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("interview_type", sa.String(length=30), nullable=False, server_default="general"),
        sa.Column("interview_date", sa.Date(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_text_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digest_task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="analyzing"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_interview_records_tenant_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            ["employees.tenant_id", "employees.id"],
            name="fk_interview_records_tenant_employee",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "digest_task_id"],
            ["async_tasks.tenant_id", "async_tasks.id"],
            name="fk_interview_records_tenant_task",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_interview_records_tenant_id"),
        sa.UniqueConstraint("tenant_id", "digest_task_id", name="uq_interview_records_tenant_task"),
    )
    op.create_index("ix_interview_records_tenant_created", "interview_records", ["tenant_id", "created_at"])
    op.create_index("ix_interview_records_employee", "interview_records", ["tenant_id", "employee_id"])

    # --- voice_entries (员工声音 raw corpus + task link) ---
    op.create_table(
        "voice_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("employee_id", sa.String(length=36), nullable=True),
        sa.Column("employee_name", sa.String(length=100), nullable=True),
        sa.Column("channel", sa.String(length=30), nullable=False, server_default="survey"),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("raw_text_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digest_task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="analyzing"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_voice_entries_tenant_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            ["employees.tenant_id", "employees.id"],
            name="fk_voice_entries_tenant_employee",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "digest_task_id"],
            ["async_tasks.tenant_id", "async_tasks.id"],
            name="fk_voice_entries_tenant_task",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_voice_entries_tenant_id"),
        sa.UniqueConstraint("tenant_id", "digest_task_id", name="uq_voice_entries_tenant_task"),
    )
    op.create_index("ix_voice_entries_tenant_created", "voice_entries", ["tenant_id", "created_at"])
    op.create_index("ix_voice_entries_employee", "voice_entries", ["tenant_id", "employee_id"])

    for table in MATERIAL_TABLES:
        _add_rls(table)
        _grant_to_app_role(table)


def downgrade() -> None:
    for table in reversed(MATERIAL_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
