"""Document deduplication and cascade cleanup.

Revision ID: 003_document_integrity
Revises: 002_hybrid_rag
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_document_integrity"
down_revision: str | None = "002_hybrid_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy rows created before hashing may carry an empty digest. Keep them
    # migratable while enforcing deduplication for every real upload.
    op.create_index(
        "uq_documents_kb_content_sha256_nonempty",
        "documents",
        ["kb_id", "content_sha256"],
        unique=True,
        postgresql_where=sa.text("content_sha256 <> ''"),
    )
    op.drop_constraint("fk_document_chunks_document_id", "document_chunks", type_="foreignkey")
    op.create_foreign_key(
        "fk_document_chunks_document_id",
        "document_chunks",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_document_chunks_document_id", "document_chunks", type_="foreignkey")
    op.create_foreign_key("fk_document_chunks_document_id", "document_chunks", "documents", ["document_id"], ["id"])
    op.drop_index("uq_documents_kb_content_sha256_nonempty", table_name="documents")
