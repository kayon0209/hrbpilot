"""HRBP AI Workbench — Knowledge Base and Document models.

RLS enabled via tenant_id.
"""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, UUIDPrimaryKey, TimestampMixin, TenantMixin


class KnowledgeBase(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "knowledge_bases"

    scenario_id: Mapped[str] = mapped_column(String(50), nullable=False)  # policy_qa | interview | culture | weekly
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")  # active | indexing | error

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name} scenario={self.scenario_id}>"


class Document(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "documents"

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx | pdf | txt
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded")  # uploaded | parsing | indexed | error
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename} status={self.status}>"
