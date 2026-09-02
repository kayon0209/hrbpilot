"""HRBP AI Workbench — scenario-specific result models.

Interview digest, Insight report, Weekly report, Culture content.
All have tenant_id for RLS.  Creator/owner references are composite
(tenant_id, id) FKs (020) so scenario rows can never bind to another tenant's
user/document/task.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class InterviewDigest(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "interview_digests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_interview_digests_tenant_document",
        ),
    )

    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["async_tasks.tenant_id", "async_tasks.id"],
            name="fk_insight_reports_tenant_task",
        ),
    )

    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    clusters_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of cluster objects
    signals_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of risk signal objects
    trends_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of trend objects

    def __repr__(self) -> str:
        return f"<InsightReport id={self.id} task={self.task_id}>"


class WeeklyReport(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    __tablename__ = "weekly_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_weekly_reports_tenant_creator",
        ),
    )

    period: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "2026-W28"
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_culture_contents_tenant_creator",
        ),
    )

    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of keyword strings
    news_article: Mapped[str] = mapped_column(Text, nullable=False)  # 800-1200 chars, formal
    group_notice: Mapped[str] = mapped_column(Text, nullable=False)  # 100-200 chars, concise
    employee_story: Mapped[str] = mapped_column(Text, nullable=False)  # 500-800 chars, narrative
    event_copy: Mapped[str] = mapped_column(Text, nullable=False)  # 200-400 chars, attractive
    tone: Mapped[str] = mapped_column(String(50), nullable=False)  # overall tone label

    def __repr__(self) -> str:
        return f"<CultureContent id={self.id} tone={self.tone}>"


class KnowledgeFeedbackCandidate(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Manager action center candidate (spec §7.7) — human-decided only."""

    __tablename__ = "knowledge_feedback_candidates"
    __table_args__ = (
        CheckConstraint(
            "NOT (org_unit_id IS NOT NULL AND source_user_id IS NOT NULL)",
            name="ck_knowledge_feedback_candidate_scope_not_ambiguous",
        ),
        # Partial unique indexes (migration 015) are the concurrency guard for
        # candidate materialization: one candidate per tenant/org|user/question_key.
        Index(
            "uq_knowledge_feedback_candidates_org_question",
            "tenant_id",
            "org_unit_id",
            "question_key",
            unique=True,
            postgresql_where=text("org_unit_id IS NOT NULL AND question_key <> ''"),
        ),
        Index(
            "uq_knowledge_feedback_candidates_user_question",
            "tenant_id",
            "source_user_id",
            "question_key",
            unique=True,
            postgresql_where=text("source_user_id IS NOT NULL AND question_key <> ''"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "org_unit_id"],
            ["org_units.tenant_id", "org_units.id"],
            name="fk_knowledge_feedback_candidates_tenant_org",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_knowledge_feedback_candidates_tenant_user",
        ),
    )

    org_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # no_evidence | negative_feedback | repeated_theme
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalized dedup key (whitespace-collapsed, lower-cased) maintained by
    # collect_candidates; the ORM default only serves direct inserts that do
    # not go through materialization, keeping them outside the partial
    # unique indexes' predicates.
    question_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    evidence_summary: Mapped[str | None] = mapped_column(Text, default=None)
    suggested_kb_id: Mapped[str | None] = mapped_column(String(36), default=None)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )  # open | confirmed | rejected | assigned
    handled_by: Mapped[str | None] = mapped_column(String(36), default=None)
    handled_reason: Mapped[str | None] = mapped_column(Text, default=None)
    assignee: Mapped[str | None] = mapped_column(String(200), default=None)


class EmployeeRequest(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Employee-visible service contract (spec §5.4) — separate from HRCase.

    hr_note / hr_case_id are internal: never serialized to the employee API.
    """

    __tablename__ = "employee_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_employee_requests_tenant_id"),
        CheckConstraint(
            "(connector_source_id IS NULL AND connector_external_event_id IS NULL AND external_sender_id IS NULL) "
            "OR (connector_source_id IS NOT NULL AND connector_external_event_id IS NOT NULL AND external_sender_id IS NOT NULL)",
            name="ck_employee_request_connector_provenance",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connector_source_id", "connector_external_event_id"],
            [
                "connector_event_log.tenant_id",
                "connector_event_log.source_id",
                "connector_event_log.external_event_id",
            ],
            name="fk_employee_request_connector_event",
        ),
        Index(
            "uq_employee_requests_connector_event",
            "tenant_id",
            "connector_source_id",
            "connector_external_event_id",
            unique=True,
            postgresql_where=text("connector_source_id IS NOT NULL"),
        ),
    )

    created_by: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # policy_check | certificate | process_help | other
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="submitted"
    )  # submitted | needs_materials | in_progress | resolved
    next_step_for_employee: Mapped[str | None] = mapped_column(Text, default=None)
    needs_materials: Mapped[str | None] = mapped_column(Text, default=None)
    hr_owner_id: Mapped[str | None] = mapped_column(String(36), default=None)
    hr_note: Mapped[str | None] = mapped_column(Text, default=None)  # internal
    hr_case_id: Mapped[str | None] = mapped_column(String(36), default=None)  # internal link to HRCase
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Immutable provenance for requests created by a verified platform event.
    # Employee-created requests deliberately leave all three columns NULL.
    connector_source_id: Mapped[str | None] = mapped_column(String(36), default=None)
    connector_external_event_id: Mapped[str | None] = mapped_column(String(255), default=None)
    external_sender_id: Mapped[str | None] = mapped_column(String(255), default=None)
