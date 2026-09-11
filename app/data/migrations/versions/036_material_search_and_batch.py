"""
Material library P1-P2: trigram search indexes + batch tracking.

Revision ID: 036_material_search_and_batch
Revises: 035_material_library

Adds:
- material_batches — batch_id tracking for 500-record bulk runs (progress 1/N)
- batch_id columns on interview_records / voice_entries
- pg_trgm extension + GIN indexes so ILIKE search benefits from index (500 scale needs no ES)
- No vector store, no separate search cluster.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036_material_search_and_batch"
down_revision: str | None = "035_material_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH_TABLE = "material_batches"


def _add_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _grant_to_app_role(table: str) -> None:
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
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        BATCH_TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="analyzing"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_material_batches_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_material_batches_tenant_creator",
        ),
    )
    _add_rls(BATCH_TABLE)
    _grant_to_app_role(BATCH_TABLE)

    op.add_column("interview_records", sa.Column("batch_id", sa.String(length=36), nullable=True))
    op.create_index("ix_interview_records_batch_id", "interview_records", ["batch_id"])
    op.add_column("voice_entries", sa.Column("batch_id", sa.String(length=36), nullable=True))
    op.create_index("ix_voice_entries_batch_id", "voice_entries", ["batch_id"])

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_employees_name_trgm ON employees USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_interview_records_employee_name_trgm "
        "ON interview_records USING gin (employee_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_interview_records_title_trgm "
        "ON interview_records USING gin (title gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_voice_entries_employee_name_trgm "
        "ON voice_entries USING gin (employee_name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_voice_entries_employee_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_interview_records_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_interview_records_employee_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_employees_name_trgm")
    op.drop_index("ix_voice_entries_batch_id", table_name="voice_entries")
    op.drop_column("voice_entries", "batch_id")
    op.drop_index("ix_interview_records_batch_id", table_name="interview_records")
    op.drop_column("interview_records", "batch_id")
    op.execute(f"DROP POLICY IF EXISTS {BATCH_TABLE}_tenant_isolation ON {BATCH_TABLE}")
    op.execute(f"ALTER TABLE {BATCH_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {BATCH_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(BATCH_TABLE)
