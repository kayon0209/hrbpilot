"""HRBP AI Workbench — Knowledge Base, Document and DocumentChunk models.

RLS enabled via tenant_id on knowledge_bases, documents and document_chunks.
Vectors are NOT stored here — they live only in Milvus. PostgreSQL keeps the
auditable original text + keyword_text (jieba tokens) + a generated tsvector.
"""

from datetime import datetime

from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class KnowledgeBase(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "knowledge_bases"

    scenario_id: Mapped[str] = mapped_column(String(50), nullable=False)  # policy_qa | interview | culture | weekly
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")  # active | indexing | error

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name} scenario={self.scenario_id}>"


class Document(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "uq_documents_kb_content_sha256_nonempty",
            "kb_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("content_sha256 <> ''"),
        ),
    )

    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx | pdf | txt
    content_type: Mapped[str | None] = mapped_column(String(100), default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="uploaded"
    )  # uploaded | parsing | indexed | error
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename} status={self.status}>"


class DocumentChunk(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        Index("ix_document_chunks_tenant_kb", "tenant_id", "kb_id"),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keyword_text: Mapped[str] = mapped_column(Text, nullable=False)  # jieba tokens, space-joined
    section: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    start_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")  # active | stale
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', keyword_text)", persisted=True),
    )

    def __repr__(self) -> str:
        return f"<DocumentChunk id={self.id} doc={self.document_id} idx={self.chunk_index}>"
