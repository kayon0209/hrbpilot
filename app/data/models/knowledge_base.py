"""HRBP AI Workbench — Knowledge Base, Document and DocumentChunk models.

RLS enabled via tenant_id on knowledge_bases, documents and document_chunks.
Vectors are NOT stored here — they live only in Milvus. PostgreSQL keeps the
auditable original text + keyword_text (jieba tokens) + a generated tsvector.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class SourceAuthority(str, Enum):
    """一份文档**对我们**是什么性质的东西 —— 决定它能不能被当作"制度依据"。

    为什么必须有这个字段
    --------------------
    "能不能入库"（格式是否可解析）与"能不能作为依据"（来源是否权威）是两件事。
    这个知识库真实踩过：一份《酒店工程部员工转正、晋级技能考核表》因为字面命中
    "年""假"，在检索降级时坐上了"公司年假怎么规定"的第一名 —— 它格式完全合法、
    解析毫无问题，但它跟制度毫无关系。仅靠格式过滤挡不住这一类。

    取值刻意只有四种 + 未知，**由人工在入库时声明，不做推断**：
    从文件名猜来源（"看起来像模板"）正是最不可靠的判据。
    """

    #: 国家法律法规、行政法规、部门规章。是**法定最低标准**，对任何单位都适用，
    #: 但不等于"本单位的制度"——回答"我们公司怎么规定"时不能只拿它充数。
    NATIONAL_LAW = "national_law"

    #: 本单位的正式制度：员工手册、考勤/休假/薪酬等经内部发布的规则。
    #: 只有这一类才能回答"我们公司是怎么规定的"。
    COMPANY_POLICY = "company_policy"

    #: 第三方模板/样本（hrtools、用友、三茅等）。可作写法参考，
    #: **不得**作为本单位制度引用；其中的占位符（如"×天"）尤其不能当数字讲出去。
    VENDOR_TEMPLATE = "vendor_template"

    #: 一般参考资料，与制度无关（培训材料、考核表格、行业报告等）。
    REFERENCE = "reference"

    #: 未标注。默认值——存在这个取值本身就说明"没人声明过"，不要当成 reference。
    UNKNOWN = "unknown"


#: 可以回答"依据是什么"的来源级别。``search_policy`` 用它判断命中里有没有真依据。
AUTHORITATIVE_AUTHORITIES: frozenset[str] = frozenset(
    {SourceAuthority.NATIONAL_LAW.value, SourceAuthority.COMPANY_POLICY.value}
)


class KnowledgeBase(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id"),)

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
        # 去重必须**按租户**作用域：这份数据是租户隔离的，而唯一约束若是全局的，
        # 两个租户在同一 kb_id 下上传相同内容就会互相撞约束 —— 而且只有后上传的那个
        # 租户会看到 IntegrityError（RLS 把冲突行藏起来了，报错从它的视角看毫无道理）。
        # 单租户测试永远发现不了这一类。
        Index(
            "uq_documents_kb_content_sha256_nonempty",
            "tenant_id",
            "kb_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("content_sha256 <> ''"),
        ),
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "kb_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_documents_tenant_kb",
        ),
    )

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx | pdf | txt
    content_type: Mapped[str | None] = mapped_column(String(100), default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="uploaded"
    )  # uploaded | parsing | indexed | error
    #: 来源级别（见 ``SourceAuthority``）。**入库时人工声明，不推断**。
    #: 默认 ``unknown``：让"没人声明过"这件事在数据里可见，而不是伪装成已知。
    authority: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceAuthority.UNKNOWN.value
    )
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
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_chunks_tenant_document",
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keyword_text: Mapped[str] = mapped_column(Text, nullable=False)  # jieba tokens, space-joined
    section: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    start_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 1-based source page, populated for PDFs by mapping start_char back onto
    # the parser's page spans. NULL for formats with no page concept
    # (txt/docx) — the API reports it as absent rather than inventing a number.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")  # active | stale
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', keyword_text)", persisted=True),
    )

    def __repr__(self) -> str:
        return f"<DocumentChunk id={self.id} doc={self.document_id} idx={self.chunk_index}>"
