"""Hybrid RAG — document tenant isolation + document_chunks table.

Revision ID: 002_hybrid_rag
Revises: 001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_hybrid_rag"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- documents: add tenant isolation + integrity fields ---
    op.add_column("documents", sa.Column("tenant_id", sa.String(36), nullable=True))
    op.execute("UPDATE documents AS d SET tenant_id = kb.tenant_id FROM knowledge_bases AS kb WHERE d.kb_id = kb.id")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM documents WHERE tenant_id IS NULL) "
        "THEN RAISE EXCEPTION 'Cannot migrate orphan documents without a knowledge base tenant'; "
        "END IF; END $$"
    )
    op.alter_column("documents", "tenant_id", nullable=False)
    op.add_column("documents", sa.Column("content_type", sa.String(100), nullable=True))
    op.add_column("documents", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("content_sha256", sa.String(64), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_foreign_key("fk_documents_kb_id", "documents", "knowledge_bases", ["kb_id"], ["id"])

    # --- document_chunks ---
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kb_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keyword_text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(200), nullable=False, server_default=""),
        sa.Column("start_char", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_char", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('simple', keyword_text)", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_chunks_tenant_kb", "document_chunks", ["tenant_id", "kb_id"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_unique_constraint("uq_document_chunks_document_index", "document_chunks", ["document_id", "chunk_index"])
    op.create_index("ix_document_chunks_search_vector", "document_chunks", ["search_vector"], postgresql_using="gin")
    op.create_foreign_key("fk_document_chunks_document_id", "document_chunks", "documents", ["document_id"], ["id"])

    # --- RLS on documents + document_chunks ---
    for table in ["documents", "document_chunks"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )

    # These existing RAG control tables were RLS-enabled in revision 001, but
    # table owners bypass non-forced RLS. Force it and add write-time checks.
    for table in ["knowledge_bases", "async_tasks"]:
        op.execute(f"DROP POLICY tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    for table in ["knowledge_bases", "async_tasks"]:
        op.execute(f"DROP POLICY tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} USING (tenant_id = current_setting('app.tenant_id'))"
        )

    for table in ["documents", "document_chunks"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("document_chunks")
    op.drop_constraint("fk_documents_kb_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_tenant_id", table_name="documents")
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "content_sha256")
    op.drop_column("documents", "size_bytes")
    op.drop_column("documents", "content_type")
    op.drop_column("documents", "tenant_id")
