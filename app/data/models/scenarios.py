"""HRBP AI Workbench — scenario-specific result models.

Interview digest, Insight report, Weekly report, Culture content.
All have tenant_id for RLS.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class InterviewDigest(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "interview_digests"

    document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("documents.id"), nullable=True, index=True)
    demands_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of Demand objects
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)  # HIGH | MEDIUM | LOW
    risk_signals_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of risk signal strings
    action_items_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of ActionItem objects
    suggested_owner: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<InterviewDigest id={self.id} risk={self.risk_level}>"


class InsightReport(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "insight_reports"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("async_tasks.id"), nullable=False, index=True)
    clusters_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of cluster objects
    signals_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of risk signal objects
    trends_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of trend objects

    def __repr__(self) -> str:
        return f"<InsightReport id={self.id} task={self.task_id}>"


class WeeklyReport(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "weekly_reports"

    period: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "2026-W28"
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    progress_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of ProgressItem
    risks_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of RiskItem
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of PlanItem
    data_sources_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of source references
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<WeeklyReport id={self.id} period={self.period}>"


class CultureContent(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "culture_contents"

    keywords_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of keyword strings
    news_article: Mapped[str] = mapped_column(Text, nullable=False)  # 800-1200 chars, formal
    group_notice: Mapped[str] = mapped_column(Text, nullable=False)  # 100-200 chars, concise
    employee_story: Mapped[str] = mapped_column(Text, nullable=False)  # 500-800 chars, narrative
    event_copy: Mapped[str] = mapped_column(Text, nullable=False)  # 200-400 chars, attractive
    tone: Mapped[str] = mapped_column(String(50), nullable=False)  # overall tone label

    def __repr__(self) -> str:
        return f"<CultureContent id={self.id} tone={self.tone}>"
