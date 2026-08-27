"""HRBP AI Workbench — HR Case domain models (Phase 4).

Six models back the bounded HR Case Agent:
  - HRCase           the case aggregate root (tenant-scoped, RLS)
  - CasePlan         the agent-proposed plan (steps, tools, risks)
  - ApprovalRequest  a pending write action awaiting human approval
  - ToolExecution    idempotent record of a tool invocation
  - CaseEvent        append-only audit trail
  - AgentRun         one agent run over a case (budget, outcome, handoff)

Security invariants (enforced in service layer + tests):
  - every table carries tenant_id for RLS
  - case events are append-only (no update/delete paths)
  - write tools may only execute against an approved ApprovalRequest

subject_ref is a SYNTHETIC identifier by design (Phase 4): no real employee
profiles are stored.
"""

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKey


class HRCase(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Case aggregate root."""

    __tablename__ = "hr_cases"

    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    subject_ref: Mapped[str] = mapped_column(String(120), nullable=False, index=True)  # synthetic ref only
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # overtime | leave | payroll | ...
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW")  # LOW|MEDIUM|HIGH
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="NEW")
    owner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), default=None)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:
        return f"<HRCase id={self.id} status={self.status} risk={self.risk_level}>"


class CasePlan(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Agent-proposed plan for a case; validated server-side before storage."""

    __tablename__ = "case_plans"

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("hr_cases.id"), nullable=False, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_runs.id"), default=None)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: [{tool, params, reason, expected}]
    rationale: Mapped[str | None] = mapped_column(Text, default=None)
    risk_notes: Mapped[str | None] = mapped_column(Text, default=None)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    def __repr__(self) -> str:
        return f"<CasePlan id={self.id} case={self.case_id}>"


class ApprovalRequest(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """A pending write action that a human must approve before execution."""

    __tablename__ = "approval_requests"

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("hr_cases.id"), nullable=False, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("case_plans.id"), default=None)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")  # PENDING|APPROVED|REJECTED|EXPIRED|CONSUMED
    requested_by: Mapped[str | None] = mapped_column(String(36), default=None)  # agent run
    approver_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), default=None)
    decision_reason: Mapped[str | None] = mapped_column(Text, default=None)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<ApprovalRequest id={self.id} tool={self.tool_name} status={self.status}>"


class ToolExecution(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Idempotent tool invocation record (request_id dedupes side effects)."""

    __tablename__ = "tool_executions"

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("hr_cases.id"), nullable=False, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("approval_requests.id"), default=None)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent_runs.id"), default=None)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # idempotency key
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of params
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")  # RUNNING|SUCCEEDED|FAILED
    result_summary: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(String(50), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        return f"<ToolExecution id={self.id} tool={self.tool_name} status={self.status}>"


class CaseEvent(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """Append-only audit event. Never updated or deleted by application code."""

    __tablename__ = "case_events"

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("hr_cases.id"), nullable=False, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # per-case monotonic sequence
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # STATUS_CHANGED|PLAN_CREATED|...
    payload_json: Mapped[str | None] = mapped_column(Text, default=None)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)  # user:<id> | agent | system

    def __repr__(self) -> str:
        return f"<CaseEvent id={self.id} case={self.case_id} seq={self.seq} type={self.event_type}>"


class AgentRun(Base, UUIDPrimaryKey, TimestampMixin, TenantMixin):
    """One bounded agent run against a case."""

    __tablename__ = "agent_runs"

    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("hr_cases.id"), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING")  # RUNNING|COMPLETED|STOPPED_LIMIT|HANDED_OFF|FAILED
    steps_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handoff_reason: Mapped[str | None] = mapped_column(Text, default=None)
    final_state: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id} case={self.case_id} status={self.status}>"
