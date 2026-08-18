"""HRBP AI Workbench — Async task, audit log, and eval result models.

AsyncTask tracks long-running operations (voice insight batch analysis, etc.).
AuditLog records every request for compliance and debugging.
EvalResult stores quality metrics per request.
All have tenant_id for RLS.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class AsyncTask(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "async_tasks"

    type: Mapped[str] = mapped_column(String(50), nullable=False)  # voice_insight_analysis | document_ingestion | ...
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")  # pending | running | completed | partial | failed
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100 percentage
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<AsyncTask id={self.id} type={self.type} status={self.status} progress={self.progress}%>"


class AuditLog(Base, UUIDPrimaryKey, TenantMixin):
    __tablename__ = "audit_logs"

    # No updated_at — audit logs are append-only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.now)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(50), nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text, default=None)
    output_summary: Mapped[str | None] = mapped_column(Text, default=None)
    retrieved_docs_json: Mapped[str | None] = mapped_column(Text, default=None)
    llm_model_version: Mapped[str | None] = mapped_column(String(100), default=None)
    guardrail_result_json: Mapped[str | None] = mapped_column(Text, default=None)
    eval_score: Mapped[float | None] = mapped_column(Float, default=None)
    response_latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    token_consumption: Mapped[int | None] = mapped_column(Integer, default=None)

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} scenario={self.scenario_id}>"


class EvalResult(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "eval_results"

    scenario_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)  # citation_accuracy | answer_relevance | ...
    score: Mapped[float] = mapped_column(Float, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), default=None)  # linked to audit log

    def __repr__(self) -> str:
        return f"<EvalResult id={self.id} metric={self.metric} score={self.score}>"
